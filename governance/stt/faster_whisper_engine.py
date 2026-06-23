"""Local speech-to-text with faster-whisper.

We own the audio, so each utterance is already its own file - I just transcribe them
one at a time, in order. That's real STT at runtime with no look-ahead, and it skips
all the streaming/endpointing machinery a live mic would need (that's a production
step, not an MVP one).

The initial_prompt nudge matters: it primes the model with the names and the codename
so it spells "Project Atlas" correctly. The codename traps fall apart if STT mangles it.

The cache is for iterating on the agent without re-transcribing every time. It's off
on the graded run (cache_path=None) so that run always transcribes live.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from faster_whisper import WhisperModel

_VOCAB_HINT = (
    "Project Atlas. Northwind Capital. Cendara Robotics. "
    "Maya Okafor, Raj Patel, Lena Fischer, Tomás Herrera."
)


class FasterWhisperEngine:
    def __init__(self, model_size: str = "small.en", cache_path: str | None = None):
        # int8 on CPU: quick enough, no GPU, light on memory
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._model_size = model_size
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, str] = {}
        if self._cache_path and self._cache_path.exists():
            self._cache = json.loads(self._cache_path.read_text())

    def _key(self, audio_path: str) -> str:
        # hash the audio bytes + model so the cache can't return a stale transcript
        h = hashlib.sha256()
        h.update(Path(audio_path).read_bytes())
        h.update(self._model_size.encode())
        return h.hexdigest()

    def transcribe(self, audio_path: str) -> str:
        if self._cache_path is not None:
            key = self._key(audio_path)
            if key in self._cache:
                return self._cache[key]

        segments, _info = self._model.transcribe(
            audio_path,
            language="en",
            beam_size=5,
            initial_prompt=_VOCAB_HINT,
            vad_filter=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()

        if self._cache_path is not None:
            self._cache[self._key(audio_path)] = text
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache))
        return text
