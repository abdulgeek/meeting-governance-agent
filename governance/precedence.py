"""Collapse per-policy verdicts into one action.

Order: DROP (P1/P2) > REDACT (P4) > FLAG (P3) > COMMIT.
Ties and low-confidence lean toward keeping less.
"""

from __future__ import annotations

from .schemas import Action, PolicyCheckResult

SUPPRESSION = ("P1", "P2")


def resolve(result: PolicyCheckResult) -> tuple[Action, str, list[str], float, str]:
    """Returns (action, policy_id, redaction_targets, confidence, note)."""

    # If we couldn't even parse the model's answer, lose the recording rather than
    # guess. A wrong COMMIT here would be a real leak; a wrong DROP just loses a line.
    if not result.valid:
        return Action.DROP, "UNSURE_PARSE", [], 0.0, "unparseable/timeout -> fail closed"

    v = result.by_id()

    # 1) Suppression first. Any lean toward pay/codename - fired OR unsure - drops.
    for pid in SUPPRESSION:
        verdict = v.get(pid)
        if verdict and (verdict.fired or verdict.abstain):
            note = "unsure -> drop" if (verdict.abstain or verdict.confidence < 0.6) else ""
            return Action.DROP, pid, [], verdict.confidence, note

    # 2) Financial number -> redact (if we have something concrete to mask).
    v4 = v.get("P4")
    if v4 and (v4.fired or v4.abstain):
        if v4.redaction_targets:
            note = "unsure -> redact" if v4.abstain else ""
            return Action.REDACT, "P4", v4.redaction_targets, v4.confidence, note
        # It smells like a financial number but the model gave us nothing to mask.
        # Don't commit it in the clear - hand it to a human instead.
        return Action.FLAG, "P4_NO_TARGET", [], v4.confidence, "financial suspected, nothing to mask -> flag"

    # 3) Possible legal exposure -> keep and flag.
    v3 = v.get("P3")
    if v3 and (v3.fired or v3.abstain):
        note = "unsure -> flag" if v3.abstain else ""
        return Action.FLAG, "P3", [], v3.confidence, note

    # 4) Nothing fired -> keep it.
    return Action.COMMIT, "NONE", [], 1.0, ""
