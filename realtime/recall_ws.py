"""/recall — Recall.ai connects here and pushes live meeting audio + events; we govern.

Recall (cloud) opens this websocket and streams base64 PCM16/16k frames (mixed, or per
participant if the workspace has the separate-audio flag) plus chat events. We buffer per
participant, flush on a short interval, gate consent by identity (chat opt-in, default-deny),
and run the same governance engine. Persists to the dashboard if ?meeting=&token= are set.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
import numpy as np
from faster_whisper import WhisperModel
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.agent import GovernanceAgent                                   # noqa: E402
from governance.audit import Audit                                             # noqa: E402
from governance.consent import ConsentRegistry                                # noqa: E402
from governance.llm.bedrock_client import BedrockClient, DEFAULT_MODEL, DEFAULT_REGION  # noqa: E402
from governance.policy_check import PolicyChecker                             # noqa: E402
from governance.sink import Sink                                             # noqa: E402
from governance.vocab import VOCAB_TERMS                                      # noqa: E402

POLICIES = (ROOT / "policies" / "policies.txt").read_text()
MODEL = os.environ.get("GOV_BEDROCK_MODEL_ID", DEFAULT_MODEL)
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
NEST = os.environ.get("NEST_API_URL", "http://localhost:4000")
# A participant opts in by typing a single token (the bot asks them to type "+"). Exact
# match on the whole (trimmed) message, so normal chatter never trips it.
OPT_IN = {"+", "yes", "y", "ok", "👍", "1"}
_VOCAB = VOCAB_TERMS
_FLUSH_SECS = 4.0
_MIN_BYTES = 16000  # ~0.5s at 16 kHz/16-bit

router = APIRouter()
_model: WhisperModel | None = None


def _pid(d: dict) -> str:
    p = d.get("participant") or {}
    return p.get("name") or p.get("email") or (f"spk{p['id']}" if p.get("id") is not None else "meeting")


def _email(d: dict) -> str | None:
    # Identity (lite): forward the Recall participant's email when known so NestJS can key
    # DSAR on email||name. speaker (_pid) stays the display name. None when absent.
    return (d.get("participant") or {}).get("email")


@router.websocket("/recall")
async def recall_ws(sock: WebSocket) -> None:
    await sock.accept()
    meeting = sock.query_params.get("meeting")
    token = sock.query_params.get("token")

    consent = ConsentRegistry({})  # default-deny; participants opt in via chat
    checker = PolicyChecker(BedrockClient(model_id=MODEL, region=REGION), POLICIES)
    out = ROOT / "out"; out.mkdir(exist_ok=True)
    agent = GovernanceAgent(consent, checker, Sink(out / "recall_transcript.jsonl"),
                            Audit(out / "recall_audit.jsonl"))
    buffers: dict[str, bytearray] = {}
    emails: dict[str, str] = {}  # pid -> email (identity lite), learned from Recall events
    state = {"idx": 0}
    loop = asyncio.get_event_loop()
    http = httpx.AsyncClient(timeout=10,
                             headers={"Authorization": f"Bearer {token}"} if token else {})

    global _model
    if _model is None:
        _model = await loop.run_in_executor(
            None, lambda: WhisperModel("small.en", device="cpu", compute_type="int8"))

    def _transcribe(pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = _model.transcribe(audio, language="en", beam_size=5, initial_prompt=_VOCAB)
        return " ".join(s.text.strip() for s in segs).strip()

    async def govern(pid: str, pcm: bytes, email: str | None = None) -> None:
        if len(pcm) < _MIN_BYTES:
            return
        state["idx"] += 1
        text = await loop.run_in_executor(None, _transcribe, pcm) if consent.has_consent(pid) else ""
        decision, shown = agent.process(state["idx"], pid, text)
        show = shown if decision.action.value in ("COMMIT", "REDACT", "FLAG") else ""
        print(f"[recall] #{decision.idx} {pid} -> {decision.action.value} ({decision.policy_id}) {show[:70]}", flush=True)
        if meeting and token:
            payload = {"idx": decision.idx, "speaker": pid, "action": decision.action.value,
                       "policyId": decision.policy_id, "confidence": decision.confidence,
                       "shown": shown}
            if email:  # identity (lite): email when known; speaker stays the display name
                payload["email"] = email
            try:
                await http.post(f"{NEST}/meetings/{meeting}/lines", json=payload)
            except Exception:
                pass

    async def flush_loop() -> None:
        try:
            while True:
                await asyncio.sleep(_FLUSH_SECS)
                for pid in list(buffers):
                    pcm = bytes(buffers.pop(pid, b""))
                    if len(pcm) >= _MIN_BYTES:
                        await govern(pid, pcm, emails.get(pid))
        except asyncio.CancelledError:
            pass

    print(f"[recall] bot connected (meeting={meeting or 'none'})", flush=True)
    flusher = asyncio.create_task(flush_loop())
    try:
        while True:
            msg = await sock.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            raw = msg.get("text")
            if not raw:
                continue
            m = json.loads(raw)
            ev = m.get("event")
            d = (m.get("data") or {}).get("data") or {}
            if ev in ("audio_mixed_raw.data", "audio_separate_raw.data"):
                buf = d.get("buffer")
                if buf:
                    pid = _pid(d)
                    email = _email(d)
                    if email:
                        emails[pid] = email
                    buffers.setdefault(pid, bytearray()).extend(base64.b64decode(buf))
            elif ev == "participant_events.chat_message":
                pid = _pid(d)
                email = _email(d)
                if email:
                    emails[pid] = email
                text = ((d.get("data") or {}).get("text") or "").strip().lower()
                if text in OPT_IN:
                    consent.grant(pid)
                    consent.grant("meeting")  # mixed audio is keyed "meeting"; opt-in covers it
                    print(f"[recall] ✓ {pid} consented (typed '{text}')", flush=True)
                    if meeting and token:
                        payload = {"participant": pid, "granted": True}
                        if email:  # identity (lite): forward email when known
                            payload["email"] = email
                        try:
                            await http.post(f"{NEST}/meetings/{meeting}/consent", json=payload)
                        except Exception:
                            pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[recall] session ended: {type(e).__name__}: {e}")
    finally:
        flusher.cancel()
        await http.aclose()
