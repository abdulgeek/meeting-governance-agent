"""Canonical Whisper vocabulary hints (initial_prompt biasing).

Priming the model with the codename and participant names keeps "Project Atlas" and the
speaker names spelled correctly - the codename traps fall apart if STT mangles them. Two
variants exist on purpose: the Recall path keys consent by participant identity from the
bot, so it only needs the codename/company terms.
"""

from __future__ import annotations

# Codename + company terms only (Recall path: identity comes from the bot, not STT).
VOCAB_TERMS = "Project Atlas. Northwind Capital. Cendara Robotics."

# Codename + company terms + participant names (local/simulated paths).
VOCAB_WITH_NAMES = (
    "Project Atlas. Northwind Capital. Cendara Robotics. "
    "Maya Okafor, Raj Patel, Lena Fischer, Tomás Herrera."
)
