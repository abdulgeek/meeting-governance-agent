"""Masking for REDACT, with a safety check.

Policy 4 wants the sentence kept but the number hidden. The model gives us the exact
substrings to blank out. The catch: the model is pointing at text the transcriber
produced, so its substring might not line up perfectly. After masking I re-check that
the targets are actually gone - if one survived, the caller drops the whole line
rather than let a digit slip onto disk. Cheap insurance for the one thing P4 must get
right.
"""

from __future__ import annotations

MASK = "█████"


def mask(text: str, targets: list[str]) -> str:
    masked = text
    # longest first, so a short target can't land inside a longer one and corrupt it
    for t in sorted({t for t in targets if t}, key=len, reverse=True):
        masked = masked.replace(t, MASK)
    return masked


def mask_verified(text: str, targets: list[str]) -> tuple[bool, str]:
    """Mask, then confirm the values are gone. ok=False means: don't write, drop instead."""
    masked = mask(text, targets)
    ok = all(t not in masked for t in targets if t)
    return ok, masked
