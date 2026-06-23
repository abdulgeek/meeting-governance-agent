"""Tiny .env loader.

Just enough to read a local .env into the process environment so boto3 (and our
config) pick it up. I didn't pull in python-dotenv for this - it's a handful of lines
and one less dependency. Existing env vars win, so the shell can still override .env.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # don't clobber something already set in the real environment
        os.environ.setdefault(key, val)
