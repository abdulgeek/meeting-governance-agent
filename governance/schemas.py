"""Types for verdicts, decisions, and actions."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Action(str, Enum):
    COMMIT = "COMMIT"      # keep the line as-is
    DROP = "DROP"          # keep nothing, leave no copy
    REDACT = "REDACT"      # keep the line, mask the sensitive value
    FLAG = "FLAG"          # keep the line, mark it for a human to review
    DECLINE = "DECLINE"    # speaker never consented, so nothing is kept


class PolicyVerdict(BaseModel):
    """The model's read on a single policy for a single line."""

    policy_id: str
    # default False on purpose: if the model omits the field we don't want a rule to
    # accidentally "fire" and drop/redact something it shouldn't.
    fired: bool = False
    suggested_action: str = ""          # advisory; we don't trust it for the final call
    redaction_targets: list[str] = Field(default_factory=list)  # P4 only: exact text to mask
    confidence: float = 1.0
    abstain: bool = False               # "can't tell" -> routes to the unsure path
    rationale: str = ""                 # should describe, never quote, the sensitive bit


class PolicyCheckResult(BaseModel):
    verdicts: list[PolicyVerdict] = Field(default_factory=list)
    valid: bool = True                  # False if we couldn't parse the model's reply

    def by_id(self) -> dict[str, PolicyVerdict]:
        return {v.policy_id: v for v in self.verdicts}


class Decision(BaseModel):
    """The final outcome for one line. No text field on purpose - decisions stay content-free."""

    idx: int
    speaker: str
    action: Action
    policy_id: str
    confidence: float
    note: str = ""
