"""Server-side meeting bot: drive governance over a MeetingSource.

This is how the product runs a real call. The source (a bot) supplies a stream of events:
participants opt in (ConsentEvent) and speak (AudioEvent). Consent is dynamic + default-deny
- a participant is only transcribed once they've opted in, and can revoke. Everyone is
governed over the shared conversation window with the same engine.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .agent import GovernanceAgent
from .audit import Audit
from .consent import ConsentRegistry
from .meeting_source import AudioEvent, ConsentEvent
from .policy_check import PolicyChecker
from .schemas import Decision
from .sink import Sink

Transcribe = Callable[[bytes], str]
OnDecision = Callable[[Decision, str], Awaitable[None]]
OnConsent = Callable[[str, bool], Awaitable[None]]


class MeetingRunner:
    def __init__(self, consent: ConsentRegistry, checker: PolicyChecker,
                 transcribe: Transcribe, sink: Sink, audit: Audit, window_size: int = 4,
                 on_decision: OnDecision | None = None, on_consent: OnConsent | None = None):
        self.consent = consent
        self.transcribe = transcribe
        self.agent = GovernanceAgent(consent, checker, sink, audit, window_size)
        self.on_decision = on_decision
        self.on_consent = on_consent

    async def run(self, source) -> list[Decision]:
        """Drive governance over the source's event stream.

        ConsentEvents update the registry (grant/revoke, default-deny). AudioEvents pass the
        consent gate first: only a consented participant is transcribed (STT), then the engine
        processes the utterance and emits a Decision. on_consent/on_decision hooks fire as
        each event is handled.
        """
        idx = 0
        out: list[Decision] = []
        async for ev in source.events():
            if isinstance(ev, ConsentEvent):
                self.consent.grant(ev.participant) if ev.granted else self.consent.revoke(ev.participant)
                if self.on_consent:
                    await self.on_consent(ev.participant, ev.granted)
                continue

            assert isinstance(ev, AudioEvent)
            idx += 1
            # Consent gated BEFORE STT: a not-yet-consented participant is never transcribed.
            if self.consent.has_consent(ev.participant):
                text = await asyncio.to_thread(self.transcribe, ev.pcm)
            else:
                text = ""  # agent.process will DECLINE on the consent gate
            decision, shown = self.agent.process(idx, ev.participant, text)
            out.append(decision)
            if self.on_decision:
                await self.on_decision(decision, shown)
        return out
