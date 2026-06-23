"""The speech-to-text interface.

Same idea as the LLM interface: the loop only knows transcribe(path) -> text, so we
can move from local faster-whisper to a streaming cloud engine later without changing
the pipeline.
"""

from __future__ import annotations

from typing import Protocol


class SttEngine(Protocol):
    def transcribe(self, audio_path: str) -> str:
        """Transcribe one audio segment to text (real STT, at runtime)."""
        ...
