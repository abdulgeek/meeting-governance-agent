"""Live governance engine: audio over WebSocket -> streaming STT -> engine -> decisions.

A headless WebSocket API (no UI here - the Next.js frontend is the client). Reuses the
exact governance engine (consent gate, policy-check tool, precedence, actions); the only
new parts are the streaming STT and the WebSocket plumbing.

Run:  uv run uvicorn realtime.server:app --port 8000   (clients connect to ws://host:8000/ws)
Uses Deepgram if DEEPGRAM_API_KEY is set, otherwise the local fallback STT.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.envload import load_env                                       # noqa: E402
load_env(ROOT / ".env")

from governance.agent import GovernanceAgent                                  # noqa: E402
from governance.audit import Audit                                            # noqa: E402
from governance.consent import ConsentRegistry                               # noqa: E402
from governance.llm.bedrock_client import BedrockClient, DEFAULT_MODEL, DEFAULT_REGION  # noqa: E402
from governance.policy_check import PolicyChecker                            # noqa: E402
from governance.sink import Sink                                             # noqa: E402
from governance.stt.local_stream import LocalStreamingSTT                    # noqa: E402

MODEL = os.environ.get("GOV_BEDROCK_MODEL_ID", DEFAULT_MODEL)
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
POLICIES = (ROOT / "policies" / "policies.txt").read_text()
NEST_API_URL = os.environ.get("NEST_API_URL", "http://localhost:4000")
_http = httpx.AsyncClient(timeout=5.0)

# Recall.ai — this engine owns the bots; NestJS proxies launch/stop here.
RECALL_REGION = os.environ.get("RECALL_REGION", "us-west-2")

app = FastAPI(title="meeting-governance-engine")
# let the Next.js frontend talk to this backend (tighten allow_origins in production)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# /recall — a Recall.ai bot in a real meeting connects here and pushes live audio + events
from realtime.recall_ws import router as recall_router  # noqa: E402
app.include_router(recall_router)


@app.get("/")
async def root():
    return {"service": "meeting-governance-engine", "ok": True, "ws": "/ws"}


# ── Recall bots ──────────────────────────────────────────────────────────────────
# Same create-bot shape as scripts/join_meeting.py, exposed over HTTP so NestJS can
# launch/stop a bot for a logged-in user. The callback url is derived from PUBLIC_BASE_URL
# (the cloudflared tunnel that fronts this engine).

class BotRequest(BaseModel):
    meeting_url: str
    meeting_id: str | None = None
    token: str | None = None
    separate: bool = False


def _recall_base() -> str:
    return f"https://{RECALL_REGION}.recall.ai/api/v1/bot"


@app.post("/bots")
async def create_bot(req: BotRequest):
    key = os.environ.get("RECALL_API_KEY")
    public = os.environ.get("PUBLIC_BASE_URL")
    if not key or not public:
        raise HTTPException(400, "set RECALL_API_KEY and PUBLIC_BASE_URL in python-api/.env")

    # swap the tunnel's https scheme for wss; the Recall bot connects to /recall
    endpoint = public.rstrip("/").replace("https://", "wss://", 1) + "/recall"
    if req.meeting_id and req.token:
        endpoint += "?" + urlencode({"meeting": req.meeting_id, "token": req.token})

    artifact = "audio_separate_raw" if req.separate else "audio_mixed_raw"
    body = {
        "meeting_url": req.meeting_url,
        "bot_name": "Governance Bot",
        "recording_config": {
            artifact: {},
            "realtime_endpoints": [{
                "type": "websocket",
                "url": endpoint,
                "events": [f"{artifact}.data", "participant_events.chat_message",
                           "participant_events.join"],
            }],
        },
    }
    r = await _http.post(f"{_recall_base()}/", json=body,
                         headers={"Authorization": f"Token {key}"})
    if r.status_code >= 300:
        raise HTTPException(502, r.text)
    bot = r.json()
    return {"bot_id": bot.get("id"), "status": bot.get("status")}


@app.delete("/bots/{bot_id}")
async def stop_bot(bot_id: str):
    key = os.environ.get("RECALL_API_KEY")
    if not key:
        raise HTTPException(400, "set RECALL_API_KEY in python-api/.env")
    await _http.post(f"{_recall_base()}/{bot_id}/leave_call/",
                     headers={"Authorization": f"Token {key}"})
    return {"ok": True}


@app.get("/bots/{bot_id}")
async def bot_status(bot_id: str):
    key = os.environ.get("RECALL_API_KEY")
    if not key:
        raise HTTPException(400, "set RECALL_API_KEY in python-api/.env")
    r = await _http.get(f"{_recall_base()}/{bot_id}",
                        headers={"Authorization": f"Token {key}"})
    if r.status_code >= 300:
        raise HTTPException(502, r.text)
    changes = r.json().get("status_changes") or []
    return {"status": changes[-1].get("code") if changes else "unknown"}


def _make_stt():
    # Speaker identity for multi-party comes from the meeting bot (Recall) per participant,
    # so consent is keyed on identity - no voiceprint needed. Deepgram if a key is set,
    # else the local faster-whisper fallback.
    if os.environ.get("DEEPGRAM_API_KEY"):
        from governance.stt.deepgram_stream import DeepgramStreamingSTT
        return DeepgramStreamingSTT(), "deepgram"
    return LocalStreamingSTT(), "local"


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()

    # one engine per session. consent_map is mutated in place so a live config update
    # is reflected by the registry that already holds a reference to it.
    consent_map = {"you": True, "guest": False}
    consent = ConsentRegistry(consent_map)
    checker = PolicyChecker(BedrockClient(model_id=MODEL, region=REGION), POLICIES)
    out = ROOT / "out"; out.mkdir(exist_ok=True)
    agent = GovernanceAgent(consent, checker,
                            Sink(out / "live_transcript.jsonl"),
                            Audit(out / "live_audit.jsonl"))
    stt, engine = _make_stt()
    state = {"idx": 0, "meeting": None, "token": None}

    async def persist(payload: dict) -> None:
        # fire-and-forget: a persistence hiccup must not stall the live stream.
        if not (state["meeting"] and state["token"]):
            return
        try:
            await _http.post(f"{NEST_API_URL}/meetings/{state['meeting']}/lines",
                             json=payload, headers={"Authorization": f"Bearer {state['token']}"})
        except Exception:
            pass

    async def on_utterance(speaker: str, text: str) -> None:
        state["idx"] += 1
        decision, shown = agent.process(state["idx"], speaker, text)
        try:
            await sock.send_json({"type": "decision", "idx": decision.idx, "speaker": speaker,
                                  "action": decision.action.value, "policy_id": decision.policy_id,
                                  "confidence": decision.confidence, "shown": shown})
        except Exception:
            return  # client gone; don't crash the STT read loop
        asyncio.create_task(persist({
            "idx": decision.idx, "speaker": speaker, "action": decision.action.value,
            "policyId": decision.policy_id, "confidence": decision.confidence, "shown": shown}))

    try:
        await stt.start(on_utterance)
        await sock.send_json({"type": "ready", "engine": engine, "model": MODEL})
        while True:
            msg = await sock.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await stt.send_audio(msg["bytes"])
            elif msg.get("text") is not None:
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "config":
                    consent_map.clear(); consent_map.update(data.get("consent", {}))
                    if data.get("meetingId"): state["meeting"] = data["meetingId"]
                    if data.get("token"): state["token"] = data["token"]
                elif kind == "speaker":
                    await stt.set_speaker(data.get("id", "you"))
                elif kind == "eou":
                    await stt.end_utterance()
                elif kind == "bye":
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:  # client dropped mid-send, etc. - end the session cleanly
        print(f"[ws] session ended: {type(e).__name__}: {e}")
    finally:
        try:
            await stt.close()
        except Exception:
            pass
