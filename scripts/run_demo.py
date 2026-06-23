"""Run the whole pipeline on the generated audio.

  --mode live    (default) transcribe live, cache off. This is the graded run.
  --mode replay  reuse a dev STT cache so you can tweak the agent without re-transcribing.

Region and model default from your environment (AWS_REGION, GOV_BEDROCK_MODEL_ID) and
can be overridden with flags. AWS credentials are picked up by boto3 from your usual
AWS setup - nothing secret lives here.

The loop is intentionally lazy: each line is transcribed and decided before the next
one is transcribed, so there's never any look-ahead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.agent import GovernanceAgent          # noqa: E402
from governance.audit import Audit                     # noqa: E402
from governance.consent import ConsentRegistry         # noqa: E402
from governance.llm.bedrock_client import BedrockClient, DEFAULT_MODEL, DEFAULT_REGION  # noqa: E402
from governance.policy_check import PolicyChecker       # noqa: E402
from governance.sink import Sink                        # noqa: E402
from governance.stt.faster_whisper_engine import FasterWhisperEngine  # noqa: E402
from governance.envload import load_env                # noqa: E402

# load .env first so AWS creds + config are in the environment before we read anything
# or build the Bedrock client. boto3 then picks the creds up from the env automatically.
load_env(ROOT / ".env")

# resolve config from the environment, falling back to sensible defaults
ENV_MODEL = os.environ.get("GOV_BEDROCK_MODEL_ID", DEFAULT_MODEL)
ENV_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION


def transcribed_stream(manifest, stt, pace: float):
    """Transcribe one segment at a time, in order. Lazy on purpose (no look-ahead)."""
    for entry in manifest:
        path = str(ROOT / entry["audio_file"])
        text = stt.transcribe(path)
        if pace:
            time.sleep(pace)  # just for a live feel in the demo
        yield entry["idx"], entry["speaker"], text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    ap.add_argument("--model", default=ENV_MODEL, help="Bedrock model id (or set GOV_BEDROCK_MODEL_ID)")
    ap.add_argument("--region", default=ENV_REGION, help="AWS region (or set AWS_REGION)")
    ap.add_argument("--model-size", default="small.en", help="faster-whisper model size")
    ap.add_argument("--window", type=int, default=4)
    ap.add_argument("--pace", type=float, default=0.0, help="seconds to pause between lines")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--validate", action="store_true", help="compare decisions to tests/oracle.json")
    args = ap.parse_args()

    out = ROOT / args.out_dir
    out.mkdir(exist_ok=True)

    manifest = json.loads((ROOT / "audio" / "manifest.json").read_text())
    consent = ConsentRegistry.from_file(ROOT / "meeting" / "participants.json")
    policies = (ROOT / "policies" / "policies.txt").read_text()

    # cache only in replay mode; live always re-transcribes
    cache = str(out / "stt_cache.json") if args.mode == "replay" else None
    print("STT mode:", "LIVE (cache off, transcribing at runtime)" if args.mode == "live"
          else "REPLAY (dev cache on)")

    stt = FasterWhisperEngine(model_size=args.model_size, cache_path=cache)
    llm = BedrockClient(model_id=args.model, region=args.region)
    checker = PolicyChecker(llm, policies)
    sink = Sink(out / "transcript.jsonl")
    audit = Audit(out / "audit.jsonl")
    agent = GovernanceAgent(consent, checker, sink, audit, window_size=args.window)

    print(f"LLM: {args.model} @ {args.region} | window: {args.window} lines\n")
    print("Decisions (live):")

    decisions = list(agent.run(transcribed_stream(manifest, stt, args.pace)))

    counts: dict[str, int] = {}
    for d in decisions:
        counts[d.action.value] = counts.get(d.action.value, 0) + 1
    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"Durable lines written to {sink.path.relative_to(ROOT)}: {sink.count}")

    if args.validate:
        _validate(decisions)


def _validate(decisions) -> None:
    oracle = json.loads((ROOT / "tests" / "oracle.json").read_text())["expected"]
    print("\nValidation vs oracle:")
    ok = 0
    for d in decisions:
        exp = oracle.get(str(d.idx), {})
        want = exp.get("action", "?")
        good = d.action.value == want
        ok += good
        mark = "\033[92mPASS\033[0m" if good else "\033[91mFAIL\033[0m"
        print(f"  [{d.idx:>2}] got {d.action.value:<8} want {want:<8} {mark}  {exp.get('why','')}")
    print(f"\n{ok}/{len(decisions)} match the oracle.")
    if ok != len(decisions):
        sys.exit(1)


if __name__ == "__main__":
    main()
