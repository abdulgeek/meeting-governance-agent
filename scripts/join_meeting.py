"""Join a REAL Zoom / Google Meet / Teams call via Recall.ai and govern every participant.

The live counterpart of run_meeting.py: same engine, same per-speaker consent, but the
audio comes from a Recall bot in the actual call instead of the simulated source.

Prereqs (see RECALL.md):
  RECALL_API_KEY=...                  in python-api/.env
  GOV_API_EMAIL / GOV_API_PASSWORD    (optional) to persist to the dashboard

Usage:
  uv run python scripts/join_meeting.py "https://meet.google.com/abc-defg-hij"
"""

from __future__ import annotations

import asyncio
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
from governance.policy_check import PolicyChecker                            # noqa: E402
from governance.recall_source import RecallMeetingSource                     # noqa: E402
from governance.sink import Sink                                            # noqa: E402

_VOCAB = "Project Atlas. Northwind Capital. Cendara Robotics."


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: join_meeting.py <meeting_url>")
    meeting_url = sys.argv[1]
    if not os.environ.get("RECALL_API_KEY"):
        sys.exit("set RECALL_API_KEY in python-api/.env first (see RECALL.md)")

    # default-deny: participants are recorded only after they opt in to the bot's prompt
    consent = ConsentRegistry({})
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

    async def on_decision(d, shown: str) -> None:
        tag = {"COMMIT": "kept", "DROP": "dropped", "REDACT": "redacted",
               "FLAG": "flagged", "DECLINE": "declined (no consent)"}.get(d.action.value, d.action.value)
        print(f"  {d.speaker:<14} {tag}")

    async def on_consent(participant: str, granted: bool) -> None:
        print(f"  ✓ {participant} {'consented' if granted else 'revoked consent'}")

    runner = MeetingRunner(consent, checker, transcribe,
                           Sink(out / "live_meeting_transcript.jsonl"),
                           Audit(out / "live_meeting_audit.jsonl"),
                           on_decision=on_decision, on_consent=on_consent)

    print(f"Bot joining {meeting_url} … (announces consent prompt; governs each participant)")
    asyncio.run(runner.run(RecallMeetingSource(meeting_url)))


if __name__ == "__main__":
    main()
