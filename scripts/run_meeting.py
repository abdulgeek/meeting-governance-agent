"""Run a multi-party governed meeting server-side — a 'bot' drives it, not a browser mic.

Default uses SimulatedMeetingSource (replays the 4-participant scenario). Swap in
RecallMeetingSource to join a real Zoom/Google Meet/Teams call. Proves the engine governs
every participant with per-speaker consent and produces a per-participant transcript.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.envload import load_env                                       # noqa: E402
load_env(ROOT / ".env")

import numpy as np                                                            # noqa: E402
from faster_whisper import WhisperModel                                       # noqa: E402

from governance.audit import Audit                                           # noqa: E402
from governance.consent import ConsentRegistry                              # noqa: E402
from governance.llm.bedrock_client import BedrockClient, DEFAULT_MODEL, DEFAULT_REGION  # noqa: E402
from governance.meeting_runner import MeetingRunner                          # noqa: E402
from governance.meeting_source import SimulatedMeetingSource                 # noqa: E402
from governance.policy_check import PolicyChecker                            # noqa: E402
from governance.sink import Sink                                            # noqa: E402

_VOCAB = "Project Atlas. Northwind Capital. Cendara Robotics. Maya Okafor, Raj Patel, Lena Fischer, Tomás Herrera."


def main() -> None:
    consent = ConsentRegistry.from_file(ROOT / "meeting" / "participants.json")
    policies = (ROOT / "policies" / "policies.txt").read_text()
    model = os.environ.get("GOV_BEDROCK_MODEL_ID", DEFAULT_MODEL)
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
    checker = PolicyChecker(BedrockClient(model_id=model, region=region), policies)

    wm = WhisperModel("small.en", device="cpu", compute_type="int8")

    def transcribe(pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = wm.transcribe(audio, language="en", beam_size=5, initial_prompt=_VOCAB)
        return " ".join(s.text.strip() for s in segs).strip()

    out = ROOT / "out"; out.mkdir(exist_ok=True)
    runner = MeetingRunner(consent, checker, transcribe,
                           Sink(out / "meeting_transcript.jsonl"),
                           Audit(out / "meeting_audit.jsonl"))
    decisions = asyncio.run(runner.run(SimulatedMeetingSource(ROOT)))

    oracle = json.loads((ROOT / "tests" / "oracle.json").read_text())["expected"]
    by_spk: dict[str, list[str]] = {}
    ok = 0
    print("\nMulti-party governed meeting (per utterance):")
    for d in decisions:
        want = oracle.get(str(d.idx), {}).get("action", "?")
        ok += d.action.value == want
        by_spk.setdefault(d.speaker, []).append(d.action.value)
        print(f"  #{d.idx:>2} {d.speaker:<6} {d.action.value:<8} ({d.policy_id})  vs {want} "
              f"{'ok' if d.action.value == want else 'MISS'}")
    print("\nPer-participant governance:")
    for spk, acts in by_spk.items():
        consented = consent.has_consent(spk)
        print(f"  {spk:<6} consent={consented!s:<5} {len(acts)} utterances -> {acts}")
    print(f"\n{ok}/{len(decisions)} match oracle — multi-party governance with per-speaker consent")
    sys.exit(0 if ok == len(decisions) else 1)


if __name__ == "__main__":
    main()
