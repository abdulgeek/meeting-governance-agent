"""Decision log + the coloured console output.

The log keeps what we did and why (idx, speaker, action, policy, confidence) but never
the words - otherwise the audit trail itself would become the leak. The console cards
are just for watching a run, e.g. during the demo.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import Action, Decision

# small ANSI colours so each action is easy to spot when the run streams by
_COLOR = {
    Action.COMMIT: "\033[92m",   # green
    Action.REDACT: "\033[93m",   # yellow
    Action.FLAG: "\033[96m",     # cyan
    Action.DROP: "\033[91m",     # red
    Action.DECLINE: "\033[95m",  # magenta
}
_RESET = "\033[0m"


class Audit:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")

    def record(self, decision: Decision) -> None:
        # content-free by construction: Decision has no text field
        with self.path.open("a") as f:
            f.write(decision.model_dump_json() + "\n")

    def card(self, decision: Decision, shown: str) -> None:
        # `shown` is the saved (already-masked) text for kept lines, or a placeholder
        # for dropped/declined ones - so this print never reveals anything we suppressed
        c = _COLOR.get(decision.action, "")
        tag = f"{c}{decision.action.value:<8}{_RESET}"
        rule = f"{decision.policy_id}".ljust(10)
        conf = f"{decision.confidence:.2f}"
        print(f"  [{decision.idx:>2}] {decision.speaker:<6} {tag} {rule} conf={conf}  {shown}")
