"""The LLM provider interface.

The decision core only knows about this tiny surface, so swapping Bedrock for another
provider (or a local model) doesn't touch anything else.
"""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the model's text reply. May raise on a transport error."""
        ...
