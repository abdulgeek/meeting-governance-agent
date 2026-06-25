"""Streaming STT interface.

The live server only depends on this, so the local fallback and the Deepgram engine are
interchangeable. An engine receives raw PCM16 mono 16 kHz audio and calls `on_utterance`
(speaker, text) whenever it has a finalized utterance.

`set_speaker` lets the server tell the engine who's currently talking (from the demo's
speaker selector for now; from voiceprint ID later). `end_utterance` is how the local
engine is told a turn ended; Deepgram finalizes on its own and ignores it.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

OnUtterance = Callable[[str, str], Awaitable[None]]  # async (speaker, text) -> None


class StreamingSTT(Protocol):
    async def start(self, on_utterance: OnUtterance) -> None: ...
    async def set_speaker(self, speaker: str) -> None: ...
    async def send_audio(self, pcm16: bytes) -> None: ...
    async def end_utterance(self) -> None: ...
    async def close(self) -> None: ...
