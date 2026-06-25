# Architecture — Meeting Governance

A **consent-first, real-time meeting governance** system. A bot joins a real Zoom / Google
Meet / Teams call, captures **per-participant** audio with identity, and governs every
utterance live: only speakers who consent are transcribed, sensitive content is redacted or
dropped *before* it is stored, nothing is written unless allowed, and any meeting can be
**crypto-shredded**. The one-liner: *decide what gets recorded before it's written down.*

> All diagrams below are Mermaid — they render on GitHub. The numbered prose explains each.

---

## 1. System context — services and externals

Three deployable services; everything sensitive (capture, reasoning, storage) is an external
the engine talks to over the network.

```mermaid
flowchart LR
  user([User / host])
  subgraph ours["Our system"]
    fe["Next.js frontend<br/>(dashboard, Vault UI)"]
    nest["NestJS API<br/>(auth, meetings, crypto-shred)"]
    eng["Python engine (FastAPI)<br/>governance core + Recall control"]
  end
  mongo[("MongoDB<br/>encrypted lines + keys")]
  recall["Recall.ai<br/>(meeting bot)"]
  meet["Zoom / Meet / Teams<br/>live call"]
  bedrock["AWS Bedrock<br/>Claude (policy reasoning)"]
  stt["STT<br/>faster-whisper / Deepgram"]

  user --> fe
  fe -->|"REST + JWT"| nest
  nest -->|"proxy join/stop"| eng
  nest <--> mongo
  eng -->|"create/stop bot (HTTPS)"| recall
  recall <-->|"WebRTC"| meet
  recall -->|"per-participant audio<br/>over WebSocket → /recall"| eng
  eng -->|"per-policy verdicts"| bedrock
  eng -->|"transcribe consented audio"| stt
  eng -->|"POST governed decisions"| nest
```

Key point: **we never implement WebRTC.** Recall's bot is the WebRTC participant inside the
call; it hands us *separated per-participant audio* over a plain WebSocket — which is exactly
the input the governance engine needs (see ADR in `INTERVIEW.md`).

---

## 2. Live multi-party governance — end-to-end flow

What happens from "someone speaks" to "it shows on the dashboard."

```mermaid
sequenceDiagram
  participant P as Participant (in call)
  participant R as Recall bot
  participant E as Engine /recall
  participant L as Bedrock (Claude)
  participant N as NestJS + Mongo
  participant D as Dashboard

  P->>R: speaks (+ chat "+" to consent)
  R->>E: per-participant audio frame (PCM16) + chat events (WebSocket)
  Note over E: buffer per speaker;<br/>chat "+" → grant consent (default-deny)
  E->>E: CONSENT GATE (before STT)
  alt speaker consented
    E->>E: STT → utterance text
    E->>L: per-policy verdicts (fired? confidence?)
    L-->>E: verdicts
    E->>E: precedence lattice → ONE action
    E->>N: POST decision (kept text encrypted; DROP/DECLINE send no text)
    N-->>D: poll → live decision + per-speaker consent
  else not consented
    Note over E: DECLINE — never transcribed, nothing stored
  end
```

---

## 3. The governance decision loop — `window → infer → act`

Per utterance, online, no look-ahead. The rule that makes it trustworthy: **the model judges,
the code acts.** Claude returns *advice* (per-policy verdicts); deterministic code makes the
*decision*.

```mermaid
flowchart TD
  u["Utterance (speaker, audio)"] --> g{"Consent?<br/>(P5, code, default-deny)"}
  g -- no --> decl["DECLINE<br/>(no STT, no write)"]
  g -- yes --> stt["STT → text"]
  stt --> llm["Claude: per-policy verdicts<br/>(fired / abstain + confidence)"]
  llm --> err{"LLM ok?"}
  err -- "error / timeout / unsure" --> drop1["DROP (fail-closed)"]
  err -- ok --> prec["Precedence lattice<br/>(keep-less wins)"]
  prec --> drop2["DROP"]
  prec --> red["REDACT"]
  prec --> flag["FLAG"]
  prec --> commit["COMMIT"]
  drop2 --> sink["append-only sink"]
  red --> sink
  flag --> sink
  commit --> sink
  decl --> audit["content-free audit"]
  drop2 --> audit
  sink --> audit
```

---

## 4. Precedence lattice — "keep less always wins"

When multiple policies fire on one utterance, code resolves the conflict deterministically by
choosing the **most restrictive** action. Consent is checked first, in code, before the LLM
ever sees the audio.

```mermaid
flowchart LR
  c["P5 CONSENT<br/>(code, pre-LLM)"] --> d["DROP<br/>P1 comp · P2 codename"] --> r["REDACT<br/>P4 numbers/PII"] --> f["FLAG<br/>P3 legal"] --> k["COMMIT<br/>(default)"]
  style c fill:#0b3,stroke:#062
```

| Action | Meaning | Writes to store? |
|--------|---------|------------------|
| DECLINE | speaker hasn't consented | no |
| DROP | sensitive (comp, codename) or LLM failed | no |
| REDACT | mask the spans (numbers/PII), keep the rest | yes (masked) |
| FLAG | legal/sensitive — keep but mark for review | yes (+ flag) |
| COMMIT | nothing fired | yes |

`fail-closed`: uncertainty (LLM error/timeout/low confidence) collapses to **DROP**, never to
COMMIT. The bias is always toward keeping *less*.

---

## 5. Consent — default-deny, per speaker, in-meeting

Nobody is recorded until they opt in *for themselves*. The gate runs **before STT**, so a
non-consenting participant's audio is never even transcribed.

```mermaid
sequenceDiagram
  participant B as Bot
  participant C as Chat
  participant M as Maya
  participant T as Tomás
  participant E as Engine

  B->>C: pins "Type + to allow your recording"
  M->>C: "+"
  C->>E: chat_message(Maya, "+")
  Note over E: grant consent → Maya
  T-->>C: (no reply)
  Note over E: Tomás stays default-deny
  M->>E: audio → transcribed + governed
  T->>E: audio → DECLINE (dropped before STT)
```

---

## 6. Ephemerality and crypto-shredding

Two independent guarantees. **Decide-before-write**: there is a single append-only sink, and
DROP/DECLINE never call it — you cannot leak what was never written. **Crypto-shred**: kept
text is encrypted with a *per-meeting* key; destroying the key makes every stored byte
permanently unreadable — instant and verifiable, even across backups and replicas.

```mermaid
flowchart TD
  dec["Decision"] --> keep{"keep?"}
  keep -- "DROP / DECLINE" --> none["(nothing written)"]
  keep -- "COMMIT / REDACT / FLAG" --> enc["AES-256-GCM encrypt<br/>(per-meeting key)"]
  enc --> store[("ciphertext in Mongo")]
  dec --> aud["content-free audit<br/>(action + policy id + confidence)"]
  key[("per-meeting key")] --> enc
  shred["shred(meeting)"] --> delkey["destroy key"]
  delkey -.->|"ciphertext now unreadable"| store
```

---

## 7. Deployment topology

```mermaid
flowchart TB
  subgraph edge["Edge / public"]
    fe["Next.js (Vercel)"]
    pub["Public HTTPS/WSS URL<br/>(PUBLIC_BASE_URL → engine /recall)"]
  end
  subgraph app["App tier"]
    nest["NestJS API :4000"]
    eng["Python engine :8000"]
  end
  subgraph data["Data / external"]
    mongo[("MongoDB")]
    recall["Recall.ai"]
    bedrock["AWS Bedrock"]
  end
  fe --> nest
  nest --> eng
  nest --> mongo
  recall --> pub --> eng
  eng --> recall
  eng --> bedrock
```

`PUBLIC_BASE_URL` is the engine's own public URL (Recall connects *to it* at `/recall`); in
dev it's a cloudflared tunnel, in prod the deployed engine host. NestJS reaches the engine
server-to-server via `PYTHON_ENGINE_URL`.

---

## 8. Trust and security boundaries

```mermaid
flowchart LR
  subgraph browser["Browser (untrusted)"]
    ui["dashboard"]
  end
  subgraph trusted["Server side (trusted)"]
    nest["NestJS — JWT verify + ownership check"]
    eng["Engine — governance, fail-closed"]
    keys[("per-meeting keys<br/>(encrypt at rest)")]
  end
  ui -->|"TLS + JWT"| nest
  nest -->|"forward caller JWT"| eng
  eng -->|"Bearer (scoped to meeting)"| nest
  secrets["secrets: env / boto chain<br/>never committed, never logged"] --- eng
  secrets --- nest
```

- **In transit:** TLS everywhere; WebSocket to `/recall` is `wss`.
- **At rest:** kept text is AES-256-GCM encrypted per meeting; the audit log is content-free.
- **AuthZ:** every meeting route verifies JWT *and* that the meeting belongs to the caller.
- **Secrets:** AWS via the boto credential chain or `.env` (gitignored); `RECALL_API_KEY`,
  `DEEPGRAM_API_KEY`, `JWT_SECRET` from env; nothing secret is printed or committed.
- **Fail-closed:** any uncertainty in the governance path biases to *not* recording.

---

### Repos
- Engine (this repo): the governance core, Recall control, `/ws` + `/recall`.
- `meeting-governance-nest-api`: product API, auth, Mongo, crypto-shred.
- `meeting-governance-nextjs`: the dashboard.

See `INTERVIEW.md` for the design-decision rationale ("why not X?"), risk/security/compliance,
competitive positioning, and the roadmap.
