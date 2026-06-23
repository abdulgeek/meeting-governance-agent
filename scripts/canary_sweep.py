"""Check that dropped/declined lines really leave no copy.

Idea: put a unique made-up token in a line that must be suppressed (a dropped pay line
and a declined non-consenter line), run the real agent, then grep every file we write
for those tokens. If they show up anywhere, we leaked.

I use a fake model here so the test is deterministic and needs no network - this is
checking our data flow (does dropped text ever get written), not the model's judgment.
It's a regression guard, not a proof of "no copy anywhere": in a real run the text
still passes through Bedrock to be judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from governance.agent import GovernanceAgent          # noqa: E402
from governance.audit import Audit                     # noqa: E402
from governance.consent import ConsentRegistry         # noqa: E402
from governance.policy_check import PolicyChecker       # noqa: E402
from governance.sink import Sink                        # noqa: E402

CANARY_DROP = "CANARY-DROP-7f3a9e21"        # goes in a pay line (P1 -> DROP)
CANARY_DECLINE = "CANARY-DECLINE-b48c0d55"  # goes in a non-consenter's line (P5 -> DECLINE)


class FakeLLM:
    """Fires P1 (drop) when it sees the canary, otherwise says everything's clear."""

    def complete(self, system: str, user: str) -> str:
        fired = CANARY_DROP in user
        p1 = ("true" if fired else "false")
        return (
            '{"verdicts":['
            f'{{"policy_id":"P1","fired":{p1},"suggested_action":"DROP","redaction_targets":[],'
            f'"confidence":0.99,"abstain":false,"rationale":"compensation"}},'
            '{"policy_id":"P2","fired":false,"confidence":0.99,"abstain":false,"rationale":"n/a"},'
            '{"policy_id":"P3","fired":false,"confidence":0.99,"abstain":false,"rationale":"n/a"},'
            '{"policy_id":"P4","fired":false,"confidence":0.99,"abstain":false,"rationale":"n/a"}'
            ']}'
        )


def main() -> int:
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    consent = ConsentRegistry({"maya": True, "tomas": False})
    checker = PolicyChecker(FakeLLM(), "(policies omitted for canary test)")
    sink = Sink(out / "canary_transcript.jsonl")
    audit = Audit(out / "canary_audit.jsonl")
    agent = GovernanceAgent(consent, checker, sink, audit, window_size=4)

    stream = [
        (1, "maya", "Let me set the agenda for today."),                      # COMMIT
        (2, "maya", f"The lead engineer base salary is {CANARY_DROP} dollars."),  # DROP (P1)
        (3, "tomas", f"My privileged legal note contains {CANARY_DECLINE}."),  # DECLINE (P5)
        (4, "maya", "Thanks everyone, talk next week."),                      # COMMIT
    ]
    list(agent.run(stream))

    # read back everything we wrote and look for the canaries
    sinks = [out / "canary_transcript.jsonl", out / "canary_audit.jsonl"]
    blob = "\n".join(p.read_text() for p in sinks if p.exists())
    leaks = [c for c in (CANARY_DROP, CANARY_DECLINE) if c in blob]

    print("Swept:", ", ".join(p.name for p in sinks))
    if leaks:
        print(f"\033[91mFAIL\033[0m - canaries found in a sink: {leaks}")
        return 1
    print("\033[92mPASS\033[0m - no dropped/declined content found in any sink.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
