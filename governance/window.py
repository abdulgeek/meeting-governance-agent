"""Short rolling memory of the last few decisions.

The window feeds context back into the next prompt, so I store the *outcome* of each
line rather than the raw words. If dropped text stayed here it would flow straight
back into the model on the next call. So: committed/flagged lines keep their real text
(it's already on disk anyway), redacted lines keep the masked version, and
dropped/declined lines keep only a placeholder. Context still works, nothing sensitive
hangs around.

Four lines turned out to be enough for the tricky cases (telling the codename-as-deal
from the codename-as-movie, or following a salary that's mentioned across a couple of
turns) without holding much in memory.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class WindowEntry:
    idx: int
    speaker: str
    action: str
    retained_text: str   # safe to reuse; a placeholder for dropped/declined lines


class Window:
    def __init__(self, size: int = 4):
        self._entries: deque[WindowEntry] = deque(maxlen=size)  # deque drops the oldest for us
        self.size = size

    def context(self) -> list[str]:
        return [f"{e.speaker}: {e.retained_text}" for e in self._entries]

    def add(self, entry: WindowEntry) -> None:
        self._entries.append(entry)
