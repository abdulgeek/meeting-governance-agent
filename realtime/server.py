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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.envload import load_env                                       # noqa: E402
load_env(ROOT / ".env")

from governance.agent import GovernanceAgent                                  # noqa: E402
from governance.audit import Audit                                            # noqa: E402
from governance.consent import ConsentRegistry                               # noqa: E402
from governance.llm.bedrock_client import BedrockClient, DEFAULT_MODEL, DEFAULT_REGION  # noqa: E402
from governance.policy_check import PolicyChecker                            # noqa: E402
from governance.recall_client import BotRequest, bot_status, create_bot, leave_bot  # noqa: E402
from governance.sink import Sink                                             # noqa: E402
from governance.stt.local_stream import LocalStreamingSTT                    # noqa: E402

MODEL = os.environ.get("GOV_BEDROCK_MODEL_ID", DEFAULT_MODEL)
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
POLICIES = (ROOT / "policies" / "policies.txt").read_text()
NEST_API_URL = os.environ.get("NEST_API_URL", "http://localhost:4000")
_http = httpx.AsyncClient(timeout=5.0)

app = FastAPI(title="meeting-governance-engine")
# let the Next.js frontend talk to this backend (tighten allow_origins in production)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# /recall - a Recall.ai bot in a real meeting connects here and pushes live audio + events
from realtime.recall_ws import router as recall_router  # noqa: E402
app.include_router(recall_router)


@app.get("/")
async def root():
    return {"service": "meeting-governance-engine", "ok": True, "ws": "/ws"}


# ── Recall bots ──────────────────────────────────────────────────────────────────
# Same create-bot shape as scripts/join_meeting.py, exposed over HTTP so NestJS can
# launch/stop a bot for a logged-in user. The HTTP wiring lives in governance.recall_client;
# these routes are thin wrappers over it.

@app.post("/bots")
async def post_bot(req: BotRequest):
    return await create_bot(req)


@app.delete("/bots/{bot_id}")
async def stop_bot(bot_id: str):
    return await leave_bot(bot_id)


@app.get("/bots/{bot_id}")
async def get_bot(bot_id: str):
    return await bot_status(bot_id)


# ── Governed summary ───────────────────────────────────────────────────────────
# Summarizes ONLY the lines it is handed. The keep-only guarantee (no DROP/DECLINE,
# no redacted text) is enforced caller-side in NestJS, which decrypts and passes only
# COMMIT/REDACT/FLAG lines here. This endpoint trusts its input and adds nothing back.

class SummaryLine(BaseModel):
    speaker: str
    text: str


class SummarizeRequest(BaseModel):
    lines: list[SummaryLine]
    style: str | None = None


_SUMMARY_SYSTEM = (
    "You are a meeting-notes assistant. Summarize ONLY the transcript lines provided. "
    "Do not invent, infer, or add facts that are not present in the lines. "
    "Write clear, neutral prose. If the lines are sparse, keep the summary short."
)


@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    if not req.lines:
        return {"summary": ""}
    transcript = "\n".join(f"{ln.speaker}: {ln.text}" for ln in req.lines)
    style = (req.style or "a concise paragraph").strip()
    user = (f"Summarize the following meeting transcript as {style}. "
            f"Use only what is stated below.\n\n{transcript}")
    client = BedrockClient(model_id=MODEL, region=REGION)
    # Bedrock's boto3 client is sync; run it off the event loop so we don't block.
    summary = await asyncio.to_thread(client.complete, _SUMMARY_SYSTEM, user)
    return {"summary": summary.strip()}


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
