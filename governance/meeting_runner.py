"""Server-side meeting bot: drive governance over a MeetingSource.

This is how the product runs a real call: a bot (the MeetingSource) supplies every
participant's audio, and we govern each speaker over the shared conversation window with
per-speaker consent gated BEFORE transcription. Reuses the exact GovernanceAgent — the
only new thing vs. the browser-mic path is that audio comes from a meeting, not one mic.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .agent import GovernanceAgent
from .audit import Audit
from .consent import ConsentRegistry
from .policy_check import PolicyChecker
from .schemas import Decision
from .sink import Sink

Transcribe = Callable[[bytes], str]
OnDecision = Callable[[Decision, str], Awaitable[None]]


class MeetingRunner:
    def __init__(self, consent: ConsentRegistry, checker: PolicyChecker,
                 transcribe: Transcribe, sink: Sink, audit: Audit,
                 window_size: int = 4, on_decision: OnDecision | None = None):
        self.consent = consent
        self.transcribe = transcribe
        self.agent = GovernanceAgent(consent, checker, sink, audit, window_size)
        self.on_decision = on_decision

    async def run(self, source) -> list[Decision]:
        idx = 0
        out: list[Decision] = []
        async for participant, pcm in source.events():
            idx += 1
            # Consent gated BEFORE STT: a non-consenting participant is never transcribed.
            # (With a bot that separates audio per participant, identity is known up front.)
            if self.consent.has_consent(participant):
                text = await asyncio.to_thread(self.transcribe, pcm)
            else:
                text = ""  # agent.process will DECLINE on the consent gate
            decision, shown = self.agent.process(idx, participant, text)
            out.append(decision)
            if self.on_decision:
                await self.on_decision(decision, shown)
        return out
