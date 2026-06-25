# Go live: join a real Zoom / Google Meet / Teams call

A Recall.ai bot joins the real call and streams its audio to our `/recall` websocket, where
the same governance engine runs. **Verified live on a Google Meet** — see the run log at the
bottom.

```
real meeting ──Recall bot──▶ (Recall cloud connects OUT to)  wss://<public>/recall
                                                              │  base64 PCM16/16k + chat events
                                                              ▼
                              recall_ws.py: buffer ▸ consent gate ▸ STT ▸ engine ▸ decisions
                              (default-deny; chat opt-in grants)        └▶ dashboard (optional)
```

Key facts (confirmed against Recall's API):
- **Recall connects to us**, not the other way around — so the engine needs a public URL.
- Audio arrives as **base64 PCM16 / 16 kHz / mono** (matches our STT) in `audio_*_raw.data`
  events; `audio_separate_raw.data` carries `data.data.participant` identity.
- The artifact must be **configured** in `recording_config` (e.g. `"audio_mixed_raw": {}`),
  not just subscribed to in `realtime_endpoints[].events`.
- **Per-participant audio (`audio_separate_raw`) is a workspace feature flag** Recall enables
  on request (Slack), not via the API. Without it you get one mixed stream — which proves the
  whole pipeline but can't attribute per speaker. With it, consent is gated per participant.

## Steps
1. `RECALL_API_KEY` + `RECALL_REGION` in `python-api/.env` (region is the one your key lives
   in — ours is `ap-northeast-1`; a wrong region returns 401).
2. Start the engine:  `uv run uvicorn realtime.server:app --port 8000`
3. Expose it publicly (Recall must reach it):
   `cloudflared tunnel --url http://localhost:8000`  → copy the `https://XXXX.trycloudflare.com`
4. Launch the bot (use the `wss://` form of the tunnel URL):
   ```
   uv run python scripts/join_meeting.py "https://meet.google.com/abc-defg-hij" wss://XXXX.trycloudflare.com
   ```
   Add `--separate` if Recall enabled per-participant audio. Add `--meeting <id> --token <jwt>`
   to persist decisions to the dashboard.
5. **Admit "Governance Bot"** in the call, then **type `I consent`** in chat. Default-deny:
   nothing is transcribed until someone opts in; revoke is symmetric.

## Verified run (live Google Meet, mixed audio)
```
#1–11  DECLINE (P5)   no consent  -> never transcribed (transcript stays empty)
       ✓ Abdul consented (chat opt-in)
#13    REDACT (P4)    "My account number is █████ ..."   (number masked before write)
#17    DROP   (P2)    codename caught — content never persists
#12,14 COMMIT         clean speech kept
```
21 utterances governed, 9 kept lines — every DROP/DECLINE absent from the record
(decide-before-write). The engine code is identical to the simulated path (`run_meeting.py`,
17/17 oracle); Recall is just the live audio source.
