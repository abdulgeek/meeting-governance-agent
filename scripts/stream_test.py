"""Headless test of the live path: stream the 17 scenario WAVs through the WebSocket
server (with the real consent map) and check the streaming pipeline still gets 17/17.

Proves the real-time route (WS -> streaming STT -> engine -> WS) is correct, without a
mic or a Deepgram key (uses the server's local fallback STT). Start the server first:
    uv run uvicorn realtime.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
URL = "ws://127.0.0.1:8000/ws"


def pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


async def main() -> int:
    manifest = json.loads((ROOT / "audio" / "manifest.json").read_text())
    participants = json.loads((ROOT / "meeting" / "participants.json").read_text())
    consent = {sid: bool(info.get("consent", False)) for sid, info in participants.items()}
    oracle = json.loads((ROOT / "tests" / "oracle.json").read_text())["expected"]

    async with websockets.connect(URL, max_size=None) as ws:
        while True:
            m = json.loads(await ws.recv())
            if m.get("type") == "ready":
                print(f"engine: {m['engine']} | model: {m['model']}")
                break
        await ws.send(json.dumps({"type": "config", "consent": consent}))

        results = []
        for e in manifest:
            await ws.send(json.dumps({"type": "speaker", "id": e["speaker"]}))
            data = pcm(ROOT / e["audio_file"])
            for i in range(0, len(data), 16000):       # stream ~0.5s frames
                await ws.send(data[i:i + 16000])
            await ws.send(json.dumps({"type": "eou"}))
            while True:                                 # wait for this line's decision
                m = json.loads(await ws.recv())
                if m.get("type") == "decision":
                    results.append(m)
                    break
        await ws.send(json.dumps({"type": "bye"}))

    ok = 0
    print("\nStreaming decisions vs oracle:")
    for m in results:
        want = oracle.get(str(m["idx"]), {}).get("action", "?")
        good = m["action"] == want
        ok += good
        print(f"  [{m['idx']:>2}] {m['speaker']:<6} got {m['action']:<8} want {want:<8} "
              f"{'PASS' if good else 'FAIL'}")
    print(f"\n{ok}/{len(results)} match the oracle (streaming path).")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
