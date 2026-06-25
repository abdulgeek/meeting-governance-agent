"""Voiceprint consent (Phase 3).

Identify who is speaking from an acoustic signature and gate consent BEFORE transcription,
so a non-consenting speaker's audio is never even sent to STT.

The MVP signature is an MFCC summary (mean/std of MFCCs + their deltas). It separates
distinct voices reliably and needs no heavy model. A learned speaker embedding (ECAPA /
resemblyzer) is a drop-in swap behind this same interface for production robustness across
many similar voices - same idea as the swappable STT and LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np


def _pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _embed(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    if audio.size < sr // 5:  # < 0.2s: too short to characterise
        return np.zeros(80, dtype=np.float32)
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20)
    d1 = librosa.feature.delta(mfcc)
    feat = np.concatenate([mfcc.mean(1), mfcc.std(1), d1.mean(1), d1.std(1)])
    norm = np.linalg.norm(feat)
    return (feat / norm).astype(np.float32) if norm else feat.astype(np.float32)


class VoiceprintRegistry:
    def __init__(self, threshold: float = 0.55):
        self.threshold = threshold  # min cosine similarity to accept an identity
        self._emb: dict[str, np.ndarray] = {}
        self._consent: dict[str, bool] = {}

    def enroll(self, speaker: str, audio: np.ndarray, consent: bool, sr: int = 16000) -> None:
        self._emb[speaker] = _embed(audio, sr)
        self._consent[speaker] = consent

    def enroll_wav(self, speaker: str, wav_path: str, consent: bool) -> None:
        audio, _ = librosa.load(wav_path, sr=16000, mono=True)
        self.enroll(speaker, audio, consent)

    @classmethod
    def from_scenario(cls, participants_path, root, manifest) -> "VoiceprintRegistry":
        """Enroll each speaker once, from their first segment in the manifest."""
        reg = cls()
        participants = json.loads(Path(participants_path).read_text())
        seen: set[str] = set()
        for e in manifest:
            spk = e["speaker"]
            if spk in seen:
                continue
            seen.add(spk)
            reg.enroll_wav(spk, str(Path(root) / e["audio_file"]),
                           bool(participants.get(spk, {}).get("consent", False)))
        return reg

    def identify(self, pcm16: bytes, sr: int = 16000) -> tuple[str, float]:
        v = _embed(_pcm16_to_float(pcm16), sr)
        best, score = "unknown", -1.0
        for spk, emb in self._emb.items():
            s = float(np.dot(v, emb))
            if s > score:
                best, score = spk, s
        return (best, score) if score >= self.threshold else ("unknown", score)

    def has_consent(self, speaker: str) -> bool:
        return self._consent.get(speaker, False)  # fail closed: unknown => no consent
