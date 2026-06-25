"""Phase 3 voiceprint-consent test.

Streams the 17 scenario WAVs with NO speaker hint — the engine must identify each speaker
by voice and gate consent before STT. Verifies identification matches the manifest and the
resulting decisions match the oracle (i.e. consent was applied to the right people).

Run the engine in voiceprint mode first:
    GOV_VOICEPRINT=1 uv run uvicorn realtime.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
WS = "ws://127.0.0.1:8000/ws"


def pcm(p: Path) -> bytes:
    with wave.open(str(p), "rb") as w:
        return w.readframes(w.getnframes())


async def main() -> int:
    manifest = json.loads((ROOT / "audio" / "manifest.json").read_text())
    participants = json.loads((ROOT / "meeting" / "participants.json").read_text())
    consent = {k: bool(v.get("consent", False)) for k, v in participants.items()}
    oracle = json.loads((ROOT / "tests" / "oracle.json").read_text())["expected"]

    async with websockets.connect(WS, max_size=None) as ws:
        while True:
            x = json.loads(await ws.recv())
            if x.get("type") == "ready":
                print("engine:", x["engine"]); break
        await ws.send(json.dumps({"type": "config", "consent": consent}))
        results = []
        for e in manifest:
            data = pcm(ROOT / e["audio_file"])      # no speaker hint sent
            for i in range(0, len(data), 16000):
                await ws.send(data[i:i + 16000])
            await ws.send(json.dumps({"type": "eou"}))
            while True:
                x = json.loads(await ws.recv())
                if x.get("type") == "decision":
                    results.append((e, x)); break
        await ws.send(json.dumps({"type": "bye"}))

    id_ok = act_ok = 0
    print("\n idx | expected | identified | action got/want")
    for e, x in results:
        idx = str(e["idx"])
        si = e["speaker"] == x["speaker"]
        ai = x["action"] == oracle[idx]["action"]
        id_ok += si; act_ok += ai
        print(f"  {idx:>2} | {e['speaker']:<6} | {x['speaker']:<7} {'ok ' if si else 'MISS'} | "
              f"{x['action']}/{oracle[idx]['action']} {'ok' if ai else 'MISS'}")
    n = len(results)
    print(f"\nIdentification: {id_ok}/{n} | Decisions vs oracle: {act_ok}/{n}")
    ok = id_ok == n and act_ok == n
    print("VOICEPRINT:", "PASS — speakers identified by voice, consent gated correctly" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
