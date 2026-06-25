"""Local streaming-STT fallback (no API key).

Buffers PCM and transcribes a whole utterance on `end_utterance` with faster-whisper.
Push-to-talk style pseudo-streaming, for developing/testing the pipeline without Deepgram.
"""

from __future__ import annotations

import asyncio

import numpy as np
from faster_whisper import WhisperModel

from ..vocab import VOCAB_WITH_NAMES
from .streaming_base import OnUtterance

_VOCAB = VOCAB_WITH_NAMES
_MIN_BYTES = 3200  # ~0.1s at 16 kHz/16-bit; ignore anything shorter


class LocalStreamingSTT:
    def __init__(self, model_size: str = "small.en"):
        self._model_size = model_size
        self._model: WhisperModel | None = None
        self._buf = bytearray()
        self._cb: OnUtterance | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaker = "speaker"

    async def start(self, on_utterance: OnUtterance) -> None:
        self._cb = on_utterance
        self._loop = asyncio.get_event_loop()
        self._model = await self._loop.run_in_executor(
            None, lambda: WhisperModel(self._model_size, device="cpu", compute_type="int8"))

    async def set_speaker(self, speaker: str) -> None:
        self._speaker = speaker

    async def send_audio(self, pcm16: bytes) -> None:
        self._buf.extend(pcm16)

    async def end_utterance(self) -> None:
        if len(self._buf) < _MIN_BYTES:
            self._buf.clear()
            return
        pcm = bytes(self._buf)
        self._buf.clear()
        text = (await self._loop.run_in_executor(None, self._transcribe, pcm)).strip()
        if text and self._cb:
            await self._cb(self._speaker, text)

    def _transcribe(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=5, initial_prompt=_VOCAB, vad_filter=False)
        return " ".join(s.text.strip() for s in segments)

    async def close(self) -> None:
        self._buf.clear()
