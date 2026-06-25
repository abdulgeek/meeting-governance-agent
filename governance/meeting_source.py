"""Meeting capture layer — the seam that turns this into a product that joins real calls.

A MeetingSource yields per-participant audio from a multi-party meeting, so the SAME
governance (consent gate + policy check + actions) runs on every speaker independently.

  - RecallMeetingSource (recall_source.py): a bot joins a real Zoom/Meet/Teams call and
    streams SEPARATED per-participant audio + identity — so we get speaker attribution for
    free (no mixed-audio diarization) and gate consent by participant identity.
  - SimulatedMeetingSource (here): replays the scenario's per-speaker segments as a live
    multi-party meeting, to exercise the whole path without an external account.
"""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path
from typing import AsyncIterator, Protocol


class MeetingSource(Protocol):
    # async iterator of (participant_id, pcm16_mono_16k)
    def events(self) -> AsyncIterator[tuple[str, bytes]]: ...


class SimulatedMeetingSource:
    """Replays the scenario as a live multi-party meeting (one event per utterance)."""

    def __init__(self, root: str | Path, pace: float = 0.0):
        self._root = Path(root)
        self._pace = pace  # seconds between utterances, to mimic real-time pacing

    async def events(self) -> AsyncIterator[tuple[str, bytes]]:
        manifest = json.loads((self._root / "audio" / "manifest.json").read_text())
        for e in manifest:
            with wave.open(str(self._root / e["audio_file"]), "rb") as w:
                pcm = w.readframes(w.getnframes())
            if self._pace:
                await asyncio.sleep(self._pace)
            yield e["speaker"], pcm
