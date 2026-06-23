"""Policy 5, the consent gate.

This runs in code, before the model is ever called. I deliberately did not make
consent something the LLM decides: if a speaker hasn't agreed, their words must never
be kept, and I don't want a model mistake (or someone talking the model into ignoring
it) to be able to break that. Putting it here also makes it trivial to test.
"""

from __future__ import annotations

import json
from pathlib import Path


class ConsentRegistry:
    def __init__(self, consent_by_speaker: dict[str, bool]):
        self._consent = consent_by_speaker

    @classmethod
    def from_file(cls, path: str | Path) -> "ConsentRegistry":
        data = json.loads(Path(path).read_text())
        return cls({sid: bool(info.get("consent", False)) for sid, info in data.items()})

    def has_consent(self, speaker_id: str) -> bool:
        # fail closed: an unknown speaker is treated as "did not consent"
        return self._consent.get(speaker_id, False)
