"""Main loop: consent check -> policy tool -> resolve -> act -> window update."""

from __future__ import annotations

from typing import Iterable, Iterator

from . import actions
from .audit import Audit
from .consent import ConsentRegistry
from .policy_check import PolicyChecker
from .precedence import resolve
from .schemas import Action, Decision
from .sink import Sink
from .window import Window, WindowEntry


class GovernanceAgent:
    def __init__(
        self,
        consent: ConsentRegistry,
        checker: PolicyChecker,
        sink: Sink,
        audit: Audit,
        window_size: int = 4,
    ):
        self.consent = consent
        self.checker = checker
        self.sink = sink
        self.audit = audit
        self.window = Window(window_size)

    def run(self, stream: Iterable[tuple[int, str, str]]) -> Iterator[Decision]:
        for idx, speaker, text in stream:
            yield self._process(idx, speaker, text)

    def _process(self, idx: int, speaker: str, text: str) -> Decision:
        raw = text  # the only cleartext copy of this line - a local, gone after the tick

        # Consent gate. If they didn't agree, we stop here: no model call, no write.
        # This is why a non-consenting speaker can't be leaked by a model slip.
        if not self.consent.has_consent(speaker):
            decision = Decision(idx=idx, speaker=speaker, action=Action.DECLINE,
                                policy_id="P5", confidence=1.0, note="no consent")
            self._emit(decision, shown="[declined::no_consent]",
                       retained="[declined::no_consent]")
            del raw
            return decision

        # Ask the model about P1-P4, then let our code pick the single action.
        result = self.checker.check(raw, speaker, self.window.context())
        action, policy_id, targets, conf, note = resolve(result)

        # Act. This is the only place text becomes durable.
        shown: str
        retained: str
        if action == Action.COMMIT:
            self.sink.write({"idx": idx, "speaker": speaker, "text": raw})
            shown = retained = raw

        elif action == Action.REDACT:
            ok, masked = actions.mask_verified(raw, targets)
            if not ok:
                # mask didn't catch the value - don't write it, drop instead
                action, policy_id, note = Action.DROP, "P4_MASK_FAILED", "mask failed -> drop"
                shown = retained = f"[removed::{policy_id}]"
            else:
                self.sink.write({"idx": idx, "speaker": speaker, "text": masked,
                                 "redacted": True})
                shown = retained = masked

        elif action == Action.FLAG:
            self.sink.write({"idx": idx, "speaker": speaker, "text": raw,
                             "flag": "legal_review"})
            shown = retained = raw

        else:  # DROP - write nothing, keep only a placeholder in the window
            shown = retained = f"[removed::{policy_id}]"

        decision = Decision(idx=idx, speaker=speaker, action=action,
                            policy_id=policy_id, confidence=conf, note=note)
        self._emit(decision, shown=shown, retained=retained)
        del raw  # drop the cleartext before moving to the next line
        return decision

    def _emit(self, decision: Decision, shown: str, retained: str) -> None:
        self.audit.record(decision)
        self.audit.card(decision, shown)
        self.window.add(WindowEntry(decision.idx, decision.speaker,
                                    decision.action.value, retained))
