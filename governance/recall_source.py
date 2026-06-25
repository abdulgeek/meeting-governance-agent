"""RecallMeetingSource — join a real Zoom / Google Meet / Teams call and capture everyone.

Recall.ai runs the bot that joins the meeting URL and streams SEPARATED per-participant
audio plus each participant's identity. That gives us speaker attribution for free (no
mixed-audio diarization) and lets us gate consent by participant identity — exactly what
the multi-party product needs. This is the seam: its output (participant_id, pcm16) is the
same MeetingSource shape the engine already consumes.

Status: connector skeleton. The flow (create bot -> receive real-time per-participant audio
over a websocket) matches Recall's real-time media API; field names should be confirmed
against current Recall docs once RECALL_API_KEY is set. Needs no other code changes — drop
it in wherever SimulatedMeetingSource is used.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator

import httpx
import websockets


class RecallMeetingSource:
    def __init__(self, meeting_url: str, api_key: str | None = None, region: str = "us-west-2"):
        self._meeting_url = meeting_url
        self._key = api_key or os.environ["RECALL_API_KEY"]
        self._region = region
        self._base = f"https://{region}.recall.ai/api/v1"
        self._queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

    async def _create_bot(self) -> dict:
        """Send a bot into the meeting and ask for real-time, per-participant audio."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self._base}/bot/",
                headers={"Authorization": f"Token {self._key}"},
                json={
                    "meeting_url": self._meeting_url,
                    # request separate audio per participant (vs a single mixed track)
                    "recording_config": {
                        "audio_separate_raw": {},
                        "realtime_endpoints": [
                            {"type": "websocket",
                             "events": ["audio_separate_raw.data", "participant_events.join"]}
                        ],
                    },
                },
            )
            r.raise_for_status()
            return r.json()

    async def events(self) -> AsyncIterator[tuple[str, bytes]]:
        """Yield (participant_id, pcm16) as the bot streams the live call.

        Recall delivers per-participant audio frames over a websocket; we forward each as a
        MeetingSource event. (The exact message schema is finalized against Recall's docs;
        the consumer side is unchanged.)
        """
        bot = await self._create_bot()
        ws_url = bot.get("realtime_websocket_url") or bot["recording"]["realtime_websocket_url"]
        async with websockets.connect(ws_url, max_size=None) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event") == "audio_separate_raw.data":
                    d = msg["data"]
                    participant = str(d["participant"]["id"])  # or .name / .email -> consent key
                    pcm = bytes.fromhex(d["audio"]) if isinstance(d["audio"], str) else d["audio"]
                    yield participant, pcm
