"""Local streaming-STT fallback (no API key).

Buffers PCM and transcribes a whole utterance on `end_utterance` with faster-whisper.
It's push-to-talk style pseudo-streaming, not true low-latency streaming - that's what
the Deepgram engine is for. Handy for developing/testing the pipeline without a key.
"""

from __future__ import annotations

import asyncio

import numpy as np
from faster_whisper import WhisperModel

from .streaming_base import OnUtterance

_VOCAB = "Project Atlas. Northwind Capital. Cendara Robotics. Maya Okafor, Raj Patel, Lena Fischer, Tomás Herrera."
_MIN_BYTES = 3200  # ~0.1s at 16 kHz/16-bit; ignore anything shorter


class LocalStreamingSTT:
    def __init__(self, model_size: str = "small.en", voiceprint=None):
        self._model_size = model_size
        self._model: WhisperModel | None = None
        self._buf = bytearray()
        self._cb: OnUtterance | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._speaker = "speaker"
        self._vp = voiceprint  # optional VoiceprintRegistry: identify + gate before STT

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

        # Voiceprint mode: identify the speaker from the audio and gate consent BEFORE
        # transcription. A non-consenting (or unknown) speaker is never sent to STT - we
        # emit an empty utterance and the agent's consent gate declines it.
        if self._vp is not None:
            speaker, _score = self._vp.identify(pcm)
            if not self._vp.has_consent(speaker):
                if self._cb:
                    await self._cb(speaker, "")  # not transcribed -> agent declines
                return
            text = (await self._loop.run_in_executor(None, self._transcribe, pcm)).strip()
            if text and self._cb:
                await self._cb(speaker, text)
            return

        # Manual-speaker mode (no voiceprint): transcribe, attribute to the set speaker.
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
