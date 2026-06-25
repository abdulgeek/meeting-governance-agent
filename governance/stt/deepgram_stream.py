"""Deepgram streaming STT over a raw WebSocket.

Talks to wss://api.deepgram.com/v1/listen directly (the documented streaming protocol)
instead of the SDK, so it's transparent and not tied to an SDK version. True low-latency
streaming with automatic endpointing, so end_utterance is a no-op. Needs DEEPGRAM_API_KEY.

A keepalive is sent while idle so Deepgram doesn't close the socket (1011) during silence
(e.g. between push-to-talk turns), and close()/send are guarded so a dropped connection
never bubbles an exception into the request handler.
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
_KEEPALIVE_SECS = 5  # Deepgram closes after ~10s of no audio; ping well under that


class DeepgramStreamingSTT:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._key = api_key or os.environ["DEEPGRAM_API_KEY"]
        self._params = dict(_PARAMS)
        if model:
            self._params["model"] = model
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._keepalive: asyncio.Task | None = None
        self._cb: OnUtterance | None = None
        self._speaker = "you"

    async def start(self, on_utterance: OnUtterance) -> None:
        self._cb = on_utterance
        url = f"{_URL}?{urlencode(self._params)}"
        self._ws = await websockets.connect(
            url, additional_headers={"Authorization": f"Token {self._key}"}, max_size=None)
        self._reader = asyncio.create_task(self._read_loop())
        self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def set_speaker(self, speaker: str) -> None:
        self._speaker = speaker

    async def send_audio(self, pcm16: bytes) -> None:
        try:
            if self._ws is not None:
                await self._ws.send(pcm16)
        except Exception:
            pass

    async def end_utterance(self) -> None:
        return  # Deepgram finalizes on its own endpointing

    async def close(self) -> None:
        for task in (self._keepalive, self._reader):
            if task is not None:
                task.cancel()
        try:
            if self._ws is not None:
                try:
                    await self._ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass
                await self._ws.close()
        except Exception:
            pass

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_KEEPALIVE_SECS)
                if self._ws is not None:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass
        except Exception:
            pass

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
                # unit. speech_final only fires after real silence, so waiting for it would
                # miss content mid-meeting.
                if text and data.get("is_final") and self._cb:
                    # consent uses the server-set speaker (the dropdown / the bot's
                    # per-participant identity); Deepgram's per-word diarized speaker is in
                    # alt["words"][i]["speaker"] if finer attribution is wanted.
                    await self._cb(self._speaker, text)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception:
            pass
