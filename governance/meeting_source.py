"""Meeting capture layer - the seam that turns this into a product that joins real calls.

A MeetingSource yields a stream of events from a multi-party meeting:
  - AudioEvent(participant, pcm)  : that participant spoke (per-participant audio)
  - ConsentEvent(participant, granted) : that participant opted in/out (in-meeting consent)

so the SAME governance runs on every speaker, and consent is dynamic + default-deny (nobody
is recorded until they opt in to the bot's prompt).

  - SimulatedMeetingSource (here): replays the scenario as a live multi-party meeting,
    emitting the consenting participants' opt-ins first, then their audio.

The live counterpart is the /recall websocket (realtime/recall_ws.py): a Recall.ai bot in a
real Zoom/Meet/Teams call connects there and pushes audio + chat, governed the same way.
See RECALL.md.
"""

from __future__ import annotations

import asyncio
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Protocol


@dataclass
class AudioEvent:
    participant: str
    pcm: bytes


@dataclass
class ConsentEvent:
    participant: str
    granted: bool = True


MeetingEvent = AudioEvent | ConsentEvent


class MeetingSource(Protocol):
    def events(self) -> AsyncIterator[MeetingEvent]: ...


class SimulatedMeetingSource:
    """Replays the scenario as a live multi-party meeting with an in-meeting consent prompt:
    consenting participants opt in at the start; the rest never do (and stay declined)."""

    def __init__(self, root: str | Path, pace: float = 0.0):
        self._root = Path(root)
        self._pace = pace

    async def events(self) -> AsyncIterator[MeetingEvent]:
        participants = json.loads((self._root / "meeting" / "participants.json").read_text())
        manifest = json.loads((self._root / "audio" / "manifest.json").read_text())

        # bot prompts "reply to consent" -> consenting participants opt in
        for pid, info in participants.items():
            if info.get("consent"):
                yield ConsentEvent(pid, True)

        for e in manifest:
            with wave.open(str(self._root / e["audio_file"]), "rb") as w:
                pcm = w.readframes(w.getnframes())
            if self._pace:
                await asyncio.sleep(self._pace)
            yield AudioEvent(e["speaker"], pcm)
