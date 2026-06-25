# Go live: join a real Zoom / Google Meet / Teams call

The governance engine is meeting-agnostic — it consumes per-participant audio over a
`MeetingSource` and governs every speaker with per-speaker consent. To join a *real* call
we use **Recall.ai**: it puts a bot in the meeting and streams us **separated
per-participant audio + identity**, so we get speaker attribution for free (no mixed-audio
diarization) and gate consent by participant identity.

```
real meeting ──Recall bot──▶ per-participant audio + chat ──▶ RecallMeetingSource
                                                              │  (AudioEvent / ConsentEvent)
                                                              ▼
                                              MeetingRunner ─▶ governance engine ─▶ decisions
                                              (consent gated BEFORE STT)            └▶ dashboard
```

## Steps
1. Create a Recall.ai account (free trial) and an API key: https://recall.ai
2. Put it in `python-api/.env`:  `RECALL_API_KEY=...`
3. (Optional, to show it on the dashboard) set `GOV_API_EMAIL` / `GOV_API_PASSWORD`.
4. Run the bot against a meeting URL:
   ```
   uv run python scripts/join_meeting.py "https://meet.google.com/abc-defg-hij"
   ```
   The bot joins, posts the consent prompt in chat, and governs each participant live:
   consenters are transcribed and governed (commit / drop / redact / flag); everyone else
   is declined and never transcribed.

## Consent model (why audio, not Recall's transcripts)
We take Recall's **per-participant audio** and run STT ourselves, so a non-consenting
participant's audio is **never transcribed** — consent stays gated *before* STT, which is
the whole point. (Recall can also return ready-made transcripts, but that would transcribe
everyone before our gate, so we don't use that path.) Consent is dynamic: it's granted by a
chat opt-in (`ConsentEvent`) and can be revoked mid-meeting.

## What to finalize once a key is set (a short verify, like we did for Deepgram)
`governance/recall_source.py` is written against Recall's real-time media API shape. With a
key, confirm two things against current Recall docs:
- the exact realtime message field names (audio frame + chat message + participant id), and
- per-participant **utterance segmentation**: buffer each participant's frames and emit one
  `AudioEvent` per utterance (Recall provides VAD / word timing for the endpoint), so STT
  runs on whole utterances rather than raw frames.

Everything downstream (consent, governance, persistence, the dashboard) is already built and
verified — this is the only piece that needs the live key to lock in.
