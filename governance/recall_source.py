"""RecallMeetingSource — join a real Zoom / Google Meet / Teams call and capture everyone.

Recall.ai runs the bot that joins the meeting URL and streams SEPARATED per-participant
audio + identity, and relays chat. That gives speaker attribution for free (no mixed-audio
diarization) and lets us run the in-meeting consent prompt: the bot announces recording,
and a participant's chat opt-in ("I consent") becomes a ConsentEvent. Until then they're
default-deny (not transcribed). Same MeetingSource shape the engine already consumes.

Status: connector skeleton. The flow (create bot -> announce -> receive per-participant
audio + chat over a websocket) matches Recall's real-time media API; field names should be
confirmed against current Recall docs once RECALL_API_KEY is set. No other code changes -
drop it in wherever SimulatedMeetingSource is used.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx
import websockets

from .meeting_source import AudioEvent, ConsentEvent, MeetingEvent

CONSENT_PHRASES = {"i consent", "consent", "yes i consent", "opt in"}
ANNOUNCE = ("This meeting is governed and recorded with consent. Reply 'I consent' in chat "
            "to be recorded; otherwise your speech will not be recorded.")


class RecallMeetingSource:
    def __init__(self, meeting_url: str, api_key: str | None = None, region: str = "us-west-2"):
        self._meeting_url = meeting_url
        self._key = api_key or os.environ["RECALL_API_KEY"]
        self._base = f"https://{region}.recall.ai/api/v1"

    async def _create_bot(self) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self._base}/bot/",
                headers={"Authorization": f"Token {self._key}"},
                json={
                    "meeting_url": self._meeting_url,
                    "chat": {"on_bot_join": {"send_to": "everyone", "message": ANNOUNCE}},
                    "recording_config": {
                        "audio_separate_raw": {},   # per-participant audio, not a mixed track
                        "realtime_endpoints": [
                            {"type": "websocket",
                             "events": ["audio_separate_raw.data", "chat_message.received"]}
                        ],
                    },
                },
            )
            r.raise_for_status()
            return r.json()

    async def events(self) -> AsyncIterator[MeetingEvent]:
        bot = await self._create_bot()
        ws_url = bot.get("realtime_websocket_url") or bot["recording"]["realtime_websocket_url"]
        async with websockets.connect(ws_url, max_size=None) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                event, data = msg.get("event"), msg.get("data", {})
                if event == "audio_separate_raw.data":
                    pid = str(data["participant"]["id"])  # or .name/.email as the consent key
                    pcm = bytes.fromhex(data["audio"]) if isinstance(data["audio"], str) else data["audio"]
                    yield AudioEvent(pid, pcm)
                elif event == "chat_message.received":
                    pid = str(data["participant"]["id"])
                    if data.get("text", "").strip().lower() in CONSENT_PHRASES:
                        yield ConsentEvent(pid, True)
