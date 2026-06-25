"""Join a real Zoom / Google Meet / Teams call and govern it live.

Launches a Recall.ai bot that streams the meeting's audio to our /recall websocket, where
the same governance engine runs (consent gate -> STT -> policy -> commit/drop/redact/flag).

Proven flow (see RECALL.md):
  1. engine:   uv run uvicorn realtime.server:app --port 8000
  2. tunnel:   cloudflared tunnel --url http://localhost:8000   ->  https://XXXX.trycloudflare.com
  3. join:     uv run python scripts/join_meeting.py <meeting_url> wss://XXXX.trycloudflare.com

Reads RECALL_API_KEY + RECALL_REGION from python-api/.env. To show decisions on the
dashboard, append:  --meeting <dashboard_meeting_id> --token <jwt>
Add --separate if Recall has enabled per-participant audio for your workspace (per-speaker
consent); otherwise we use the always-available mixed stream.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from governance.envload import load_env  # noqa: E402
load_env(ROOT / ".env")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: join_meeting.py <meeting_url> <wss_base> [--separate] "
                 "[--meeting <id>] [--token <jwt>]")
    meeting_url, wss_base = sys.argv[1], sys.argv[2].rstrip("/")
    opts = sys.argv[3:]
    separate = "--separate" in opts
    mid = opts[opts.index("--meeting") + 1] if "--meeting" in opts else None
    tok = opts[opts.index("--token") + 1] if "--token" in opts else None

    key = os.environ.get("RECALL_API_KEY")
    region = os.environ.get("RECALL_REGION", "us-west-2")
    if not key:
        sys.exit("set RECALL_API_KEY in python-api/.env first (see RECALL.md)")

    endpoint = f"{wss_base}/recall"
    if mid and tok:
        endpoint += "?" + urlencode({"meeting": mid, "token": tok})

    artifact = "audio_separate_raw" if separate else "audio_mixed_raw"
    body = {
        "meeting_url": meeting_url,
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

    req = urllib.request.Request(
        f"https://{region}.recall.ai/api/v1/bot/",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            bot = json.load(r)
        print(f"bot joining {meeting_url}\n  id:     {bot.get('id')}\n  audio:  {artifact}"
              f"\n  stream: {endpoint}\nAdmit 'Governance Bot' in the call, then type "
              f"'I consent' in chat to start governing.")
    except urllib.error.HTTPError as e:
        sys.exit(f"Recall error {e.code}: {e.read().decode()}")


if __name__ == "__main__":
    main()
