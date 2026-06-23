"""The one place durable text gets written (out/transcript.jsonl).

Everything funnels through here, and it's only ever called for COMMIT / REDACT / FLAG.
Drop and decline never reach it - which is the whole point of "a drop leaves no copy":
there's nothing to clean up because nothing was written. Keeping it to a single small
file makes that easy to reason about and easy to audit.
"""

from __future__ import annotations

import json
from pathlib import Path


class Sink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # wipe at the start of a run so the transcript is exactly this run, nothing stale
        self.path.write_text("")
        self.count = 0

    def write(self, record: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1
