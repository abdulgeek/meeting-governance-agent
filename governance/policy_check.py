"""LLM policy-check tool - grades P1-P4, returns structured verdicts (no side effects)."""

from __future__ import annotations

import json
import logging
import re

from .llm.base import LLMClient
from .schemas import PolicyCheckResult, PolicyVerdict

_log = logging.getLogger(__name__)

_SYSTEM = """You are the policy-check tool inside a real-time meeting-governance system.
You are given governance policies written in plain English, a few lines of prior
context, and ONE current utterance. Reason about the MEANING of the utterance - never
match on keywords alone.

Evaluate ONLY policies P1, P2, P3, P4 below. Do NOT evaluate consent (P5); that is
handled in code before you are ever called.

POLICIES
{policies}

Return STRICT JSON ONLY (no prose, no markdown fences), exactly this shape:
{{
  "verdicts": [
    {{
      "policy_id": "P1",
      "fired": true,
      "suggested_action": "DROP",
      "redaction_targets": [],
      "confidence": 0.0,
      "abstain": false,
      "rationale": "one short sentence, CONTENT-FREE: describe, never quote the sensitive words or digits"
    }}
    // ... exactly one object for EACH of P1, P2, P3, P4 ...
  ]
}}

Rules for the fields:
- "fired": true only if this policy genuinely applies to THIS utterance's meaning.
- "suggested_action": one of DROP (P1/P2), REDACT (P4), FLAG (P3), COMMIT (nothing).
- "redaction_targets": ONLY for P4 when it fired - copy the EXACT substrings (as they
  appear in the utterance text, character for character) that must be blacked out,
  e.g. the digits of the account/card number. Empty list otherwise.
- "confidence": 0..1, your certainty about THIS verdict.
- "abstain": true if you genuinely cannot tell - this triggers a safe fallback.
- "rationale": MUST NOT contain the sensitive value itself (no salary figures, no
  account/card digits, no codename-as-deal). Describe it instead.

Watch the classic traps: a movie/book title that merely shares the codename's words is
NOT the deal (P2 should not fire); company revenue is not anyone's pay (P1) and not an
account number (P4); an ordinary phone number is not a financial identifier (P4 should
not fire); naming an agenda topic (e.g. "comp philosophy", "liabilities") is not the
same as disclosing figures or admitting a problem.
"""

_USER = """CONTEXT (most recent last; placeholders mean prior content was not retained):
{context}

CURRENT UTTERANCE
speaker: {speaker}
text: "{text}"

Return the JSON now."""


def _extract_json(raw: str) -> str:
    # models sometimes wrap JSON in ```fences``` or add a stray word; be forgiving and
    # just grab the outermost {...}
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


class PolicyChecker:
    def __init__(self, llm: LLMClient, policies_text: str):
        self._llm = llm
        # bake the policies into the system prompt once, at startup
        self._system = _SYSTEM.format(policies=policies_text)

    def check(self, text: str, speaker: str, context: list[str]) -> PolicyCheckResult:
        ctx = "\n".join(context) if context else "(none)"
        user = _USER.format(context=ctx, speaker=speaker, text=text)
        try:
            raw = self._llm.complete(self._system, user)
            data = json.loads(_extract_json(raw))
            verdicts = [PolicyVerdict(**v) for v in data.get("verdicts", [])]
            if not verdicts:
                return PolicyCheckResult(verdicts=[], valid=False)
            return PolicyCheckResult(verdicts=verdicts, valid=True)
        except Exception as e:
            # anything goes wrong -> unsure, and precedence will fail closed
            _log.warning("policy check failed (%s); failing closed", type(e).__name__)
            return PolicyCheckResult(verdicts=[], valid=False)
