"""Deepgram streaming STT over a raw WebSocket.

Talks to wss://api.deepgram.com/v1/listen directly (the documented streaming protocol)
instead of the SDK, so it's transparent and not tied to an SDK version. True low-latency
streaming with automatic endpointing, so end_utterance is a no-op. Needs DEEPGRAM_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlencode

import websockets

from .streaming_base import OnUtterance

_URL = "wss://api.deepgram.com/v1/listen"
_PARAMS = {
    "model": "nova-2",
    "language": "en-US",
    "encoding": "linear16",
    "sample_rate": "16000",
    "channels": "1",
    "punctuate": "true",
    "smart_format": "true",
    "interim_results": "true",
    "diarize": "true",
    "endpointing": "300",
}


class DeepgramStreamingSTT:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._key = api_key or os.environ["DEEPGRAM_API_KEY"]
        self._params = dict(_PARAMS)
        if model:
            self._params["model"] = model
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._cb: OnUtterance | None = None
        self._speaker = "you"

    async def start(self, on_utterance: OnUtterance) -> None:
        self._cb = on_utterance
        url = f"{_URL}?{urlencode(self._params)}"
        self._ws = await websockets.connect(
            url, additional_headers={"Authorization": f"Token {self._key}"}, max_size=None)
        self._reader = asyncio.create_task(self._read_loop())

    async def set_speaker(self, speaker: str) -> None:
        self._speaker = speaker

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is not None:
            await self._ws.send(pcm16)

    async def end_utterance(self) -> None:
        return  # Deepgram finalizes on its own endpointing

    async def close(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
        finally:
            if self._reader is not None:
                self._reader.cancel()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                if data.get("type") != "Results":
                    continue
                alt = (data.get("channel", {}).get("alternatives") or [{}])[0]
                text = (alt.get("transcript") or "").strip()
                # is_final marks a finalized (non-overlapping) ASR segment = one governed
                # unit. speech_final (endpointing) only fires after real silence, which
                # doesn't always happen mid-meeting, so we'd miss content waiting for it.
                if text and data.get("is_final") and self._cb:
                    # consent uses the server-set speaker for now; Deepgram's per-word
                    # diarized speaker is in alt["words"][i]["speaker"] and is what the
                    # phase-3 voiceprint layer will map to an identity.
                    await self._cb(self._speaker, text)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
