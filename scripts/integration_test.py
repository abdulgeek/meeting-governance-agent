"""End-to-end integration test across all three services (headless).

Flow: register + create a meeting via NestJS -> stream the 17 scenario WAVs through the
engine WebSocket (with meetingId + JWT) -> the engine persists each governed decision to
NestJS -> read the saved transcript back and assert the ephemerality boundary held.

Prereqs: MongoDB, NestJS (:4000), and the engine in LOCAL mode (eou-driven) on :8000:
    DEEPGRAM_API_KEY= uv run uvicorn realtime.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
API = "http://localhost:4000"
WS = "ws://127.0.0.1:8000/ws"


def http(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def pcm(p: Path) -> bytes:
    with wave.open(str(p), "rb") as w:
        return w.readframes(w.getnframes())


async def main() -> int:
    manifest = json.loads((ROOT / "audio" / "manifest.json").read_text())
    participants = json.loads((ROOT / "meeting" / "participants.json").read_text())
    consent = {k: bool(v.get("consent", False)) for k, v in participants.items()}

    email = f"itest{int(time.time())}@example.com"
    _, reg = http("POST", "/auth/register", {"email": email, "password": "testpw123"})
    token = reg["accessToken"]
    _, m = http("POST", "/meetings", {"title": "Integration test"}, token=token)
    mid = m["_id"]
    print(f"registered {email} | meeting {mid}")

    async with websockets.connect(WS, max_size=None) as ws:
        while True:
            x = json.loads(await ws.recv())
            if x.get("type") == "ready":
                print("engine:", x["engine"]); break
        await ws.send(json.dumps({"type": "config", "consent": consent,
                                  "meetingId": mid, "token": token}))
        for e in manifest:
            await ws.send(json.dumps({"type": "speaker", "id": e["speaker"]}))
            data = pcm(ROOT / e["audio_file"])
            for i in range(0, len(data), 16000):
                await ws.send(data[i:i + 16000])
            await ws.send(json.dumps({"type": "eou"}))
            while True:
                x = json.loads(await ws.recv())
                if x.get("type") == "decision":
                    break
        await ws.send(json.dumps({"type": "bye"}))

    await asyncio.sleep(3)  # let the fire-and-forget persists land
    _, lines = http("GET", f"/meetings/{mid}/lines", token=token)
    print(f"\npersisted {len(lines)} lines in MongoDB:")
    for l in lines:
        shown = "<no text>" if not l.get("text") else l["text"][:46]
        print(f"  #{l['idx']:>2} {l['action']:<8} {l.get('policyId','-'):<6} {shown}")

    drops = [l for l in lines if l["action"] in ("DROP", "DECLINE")]
    keeps = [l for l in lines if l["action"] in ("COMMIT", "REDACT", "FLAG")]
    blob = json.dumps(lines)
    ok = (
        len(lines) == 17
        and all(not l.get("text") for l in drops)        # suppressed lines: no text
        and all(l.get("text") for l in keeps)            # kept lines: text present
        and not any(n in blob for n in ["8847", "4012", "190", "160"])  # no sensitive digits
    )
    print("\nINTEGRATION:", "PASS — 17 persisted; DROP/DECLINE store no text; no sensitive digits leaked"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
