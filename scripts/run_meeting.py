"""Run a multi-party governed meeting server-side — a 'bot' drives it, not a browser mic.

Uses SimulatedMeetingSource (replays the 4-participant scenario). Proves the engine governs
every participant with per-speaker consent and produces a per-participant transcript. To join
a real call instead, see scripts/join_meeting.py + the /recall endpoint (RECALL.md).
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
    # default-deny: nobody is consented until they opt in to the bot's in-meeting prompt
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

    # Optional: persist to the NestJS product API so the meeting shows on the dashboard.
    # Set GOV_API_EMAIL + GOV_API_PASSWORD to enable (registers/logs in, makes a meeting).
    import httpx
    nest = os.environ.get("NEST_API_URL", "http://localhost:4000")
    email, password = os.environ.get("GOV_API_EMAIL"), os.environ.get("GOV_API_PASSWORD")
    meeting_id = None
    client: httpx.AsyncClient | None = None
    if email and password:
        r = httpx.post(f"{nest}/auth/register", json={"email": email, "password": password})
        if r.status_code == 409:
            r = httpx.post(f"{nest}/auth/login", json={"email": email, "password": password})
        token = r.json()["accessToken"]
        meeting_id = httpx.post(f"{nest}/meetings", json={"title": "Multi-party governed meeting"},
                                headers={"Authorization": f"Bearer {token}"}).json()["_id"]
        client = httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {token}"})
        print(f"persisting to dashboard meeting {meeting_id} (log in as {email})\n")

    print("In-meeting consent prompt — opt-ins:")

    async def on_consent(participant: str, granted: bool) -> None:
        print(f"  ✓ {participant} {'opted in (consented)' if granted else 'revoked consent'}")
        if client:
            await client.post(f"{nest}/meetings/{meeting_id}/consent",
                              json={"participant": participant, "granted": granted})

    async def on_decision(d, shown: str) -> None:
        if client:
            await client.post(f"{nest}/meetings/{meeting_id}/lines",
                              json={"idx": d.idx, "speaker": d.speaker, "action": d.action.value,
                                    "policyId": d.policy_id, "confidence": d.confidence, "shown": shown})

    runner = MeetingRunner(consent, checker, transcribe,
                           Sink(out / "meeting_transcript.jsonl"),
                           Audit(out / "meeting_audit.jsonl"),
                           on_decision=on_decision, on_consent=on_consent)
    decisions = asyncio.run(runner.run(SimulatedMeetingSource(ROOT)))
    if meeting_id:
        print(f"\n→ open the dashboard, log in as {email}, open the meeting to see "
              f"participants + consent + the governed transcript.")

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
