"""Recall.ai bot management — create/leave/status + the consent announcement.

This engine owns the bots; NestJS proxies launch/stop here. Kept out of the FastAPI routes
so the HTTP wiring is one place. Same request/response shapes as before.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

# Recall.ai — this engine owns the bots; NestJS proxies launch/stop here.
RECALL_REGION = os.environ.get("RECALL_REGION", "us-west-2")

# Shared client, reused across bot calls.
_http = httpx.AsyncClient(timeout=5.0)


class BotRequest(BaseModel):
    meeting_url: str
    meeting_id: str | None = None
    token: str | None = None
    separate: bool = False


def _recall_base() -> str:
    return f"https://{RECALL_REGION}.recall.ai/api/v1/bot"


# What the bot posts in chat once it's recording. Default-deny: typing "+" is the opt-in;
# doing nothing means you're not recorded. Pinned so late joiners still see it.
ANNOUNCE = ("🔒 This meeting is governed for privacy. To allow your voice to be recorded and "
            "governed, type a single  +  in the chat. No reply = you are not recorded.")


async def announce_when_ready(bot_id: str, key: str) -> None:
    # Wait for the bot to be admitted + recording, then post the consent ask. Best-effort:
    # it must never raise into the request that spawned it.
    hdr = {"Authorization": f"Token {key}"}
    for _ in range(40):  # ~120s, matching Recall's waiting-room timeout
        await asyncio.sleep(3)
        try:
            r = await _http.get(f"{_recall_base()}/{bot_id}", headers=hdr)
            changes = r.json().get("status_changes") or []
            code = changes[-1].get("code") if changes else ""
            if code in ("in_call_recording", "in_call_not_recording"):
                await _http.post(f"{_recall_base()}/{bot_id}/send_chat_message/",
                                 json={"to": "everyone", "message": ANNOUNCE, "pin": True}, headers=hdr)
                return
            if code in ("call_ended", "done", "fatal"):
                return
        except Exception:
            pass


async def create_bot(req: BotRequest) -> dict:
    key = os.environ.get("RECALL_API_KEY")
    public = os.environ.get("PUBLIC_BASE_URL")
    if not key or not public:
        raise HTTPException(400, "set RECALL_API_KEY and PUBLIC_BASE_URL in python-api/.env")

    # swap the tunnel's https scheme for wss; the Recall bot connects to /recall
    endpoint = public.rstrip("/").replace("https://", "wss://", 1) + "/recall"
    if req.meeting_id and req.token:
        endpoint += "?" + urlencode({"meeting": req.meeting_id, "token": req.token})

    artifact = "audio_separate_raw" if req.separate else "audio_mixed_raw"
    body = {
        "meeting_url": req.meeting_url,
        "bot_name": "Governance Bot",
        "recording_config": {
            artifact: {},
            "realtime_endpoints": [{
                "type": "websocket",
                "url": endpoint,
                "events": [f"{artifact}.data", "participant_events.chat_message",
                           "participant_events.join"],
            }],
        },
    }
    r = await _http.post(f"{_recall_base()}/", json=body,
                         headers={"Authorization": f"Token {key}"})
    if r.status_code >= 300:
        raise HTTPException(502, r.text)
    bot = r.json()
    bid = bot.get("id")
    if bid:
        asyncio.create_task(announce_when_ready(bid, key))  # ask for consent once it's in the call
    return {"bot_id": bid, "status": bot.get("status")}


async def leave_bot(bot_id: str) -> dict:
    key = os.environ.get("RECALL_API_KEY")
    if not key:
        raise HTTPException(400, "set RECALL_API_KEY in python-api/.env")
    await _http.post(f"{_recall_base()}/{bot_id}/leave_call/",
                     headers={"Authorization": f"Token {key}"})
    return {"ok": True}


async def bot_status(bot_id: str) -> dict:
    key = os.environ.get("RECALL_API_KEY")
    if not key:
        raise HTTPException(400, "set RECALL_API_KEY in python-api/.env")
    r = await _http.get(f"{_recall_base()}/{bot_id}",
                        headers={"Authorization": f"Token {key}"})
    if r.status_code >= 300:
        raise HTTPException(502, r.text)
    changes = r.json().get("status_changes") or []
    return {"status": changes[-1].get("code") if changes else "unknown"}
