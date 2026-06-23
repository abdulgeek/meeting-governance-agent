# Real-Time Meeting Governance Agent

A working MVP of a real-time governance layer for high-stakes meetings. It listens to
live(-ish) speech and decides, line by line and *before anything is written to disk*,
what may be recorded: keep it, drop it, mask a number in it, flag it for legal, or
decline it entirely if the speaker never consented.

The scenario is an M&A diligence call (Northwind acquiring Cendara, deal codename
"Project Atlas") with five plain-English governance rules. It runs end to end on real
speech-to-text and real LLM inference, and on the scenario's deliberately tricky lines
it gets all 17 right.

---

## What it does

```
audio  ->  transcribe (faster-whisper, live)  ->  for each finished line:
       ->  [1. consent gate  - code]
       ->  [2. policy-check   - Claude on Bedrock]
       ->  [3. pick one action - code]   ->  keep / drop / redact / flag
```

The single idea the whole design rests on: **the model judges, the code decides.** The
LLM only returns an opinion on each rule. Every step that can't be undone - the consent
gate, breaking ties between rules, and the actual write to disk - is plain code I can
read and test. So a model that's wrong, or that someone tries to talk around, can't by
itself cause a leak or override consent.

### The five rules (plain English, in [`policies/policies.txt`](policies/policies.txt))
| | Rule | Action |
|---|---|---|
| P1 | Talk about anyone's pay (salary/bonus/equity) | **DROP** |
| P2 | The deal codename "Project Atlas", when it means *this* deal | **DROP** |
| P3 | A possible legal/regulatory admission | **FLAG** (keep + mark for legal) |
| P4 | A bank account / card number read aloud | **REDACT** (keep, mask the digits) |
| P5 | A speaker who hasn't consented | **DECLINE** - beats every other rule |

### The action set
`COMMIT` · `DROP` (no copy kept) · `REDACT(value)` · `FLAG_FOR_REVIEW` · `CONSENT-GATE-DECLINE`

---

## Quickstart

**You'll need:** macOS (for the `say` TTS that generates the meeting), `ffmpeg`
(`brew install ffmpeg`), [`uv`](https://docs.astral.sh/uv/), and AWS Bedrock access to a
Claude model.

**Credentials & config.** Either works:
- your normal AWS setup (`~/.aws` or the standard `AWS_*` env vars), or
- copy [`.env.example`](.env.example) to `.env` and fill it in. The app loads `.env` at
  startup ([`governance/envload.py`](governance/envload.py)) and boto3 reads the credentials
  from there. `.env` is gitignored, so secrets never land in the repo.

Config keys: `AWS_REGION` (default `us-east-1`) and `GOV_BEDROCK_MODEL_ID` (default
`us.anthropic.claude-sonnet-4-6`). Haiku (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) is a
faster, cheaper option that still passes all 17. Make sure your chosen model is enabled in the
Bedrock console. The `--model` / `--region` flags override the env if you want a one-off.

```bash
uv sync                              # 3.12 venv + deps
uv run python scripts/gen_audio.py   # build the meeting audio (a copy is committed too)
```

### Reproduce the live run
Transcribes the audio live at runtime (no STT cache) and checks every decision against
the answer key in [`tests/oracle.json`](tests/oracle.json):

```bash
uv run python scripts/run_demo.py --mode live --validate
```

Expect `17/17 match the oracle`. Flags: `--model`, `--region`, `--model-size`
(faster-whisper, default `small.en`), `--window` (default 4), `--pace 0.6` (pause
between lines for a more live feel).

### Prove a DROP leaves no copy
```bash
uv run python scripts/canary_sweep.py
```

### Tests (offline, no network)
```bash
uv run --with pytest pytest -q
```

---

## Example output (a real live run)

```
  [ 4] tomas  DECLINE  P5    conf=1.00  [declined::no_consent]            non-consenter, regardless of topic
  [ 6] maya   DROP     P2    conf=0.99  [removed::P2]                     codename, tied to the deal
  [ 8] lena   REDACT   P4    conf=0.99  ...account number is █████.       digits masked, sentence kept
  [10] raj    DROP     P1    conf=0.98  [removed::P1]                     compensation figures
  [12] tomas  DECLINE  P5    conf=1.00  [declined::no_consent]            legal content, but consent wins over flag
  [13] lena   FLAG     P3    conf=0.98  ...safety defect... missed ...    kept + flagged for legal
  [14] raj    COMMIT   NONE  conf=1.00  ...documentary, Project Atlas..   same words as #6, but a movie -> kept
  [15] raj    COMMIT   NONE  conf=1.00  ...office line is 415-555-0182.   phone, not financial -> kept
  [16] maya   COMMIT   NONE  conf=1.00  ...comp philosophy at the offsite topic, no figures -> kept
Summary: COMMIT=9, DECLINE=2, DROP=3, FLAG=1, REDACT=2
```

The interesting pairs: lines 6 and 14 are the same words ("Project Atlas") with opposite
outcomes; lines 8/9 and 15 are all read in the same grouped-digit cadence but only the
account and card numbers get masked. A keyword filter fails both; the model has to reason
about meaning.

---

### Why the window is 4 lines
The window is measured in utterances (the whole pipeline is turn-by-turn), and it holds
the last 4 decided lines plus the current one. Four was the sweet spot: enough trailing
context to tell the codename-as-deal (#6) from the codename-as-movie (#14), and to follow
a salary mentioned across a couple of turns (#10-#11), without holding much sensitive text
in memory or adding latency. It stores the *outcome* of each line, not the raw words:
kept lines keep their text (already on disk), redacted lines keep the masked version, and
dropped/declined lines keep only a placeholder - so dropped content can't slip back into a
later prompt.

