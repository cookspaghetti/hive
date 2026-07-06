"""Linguistic noise: typos, regional shorthand, Manglish/code-switching.

Original contribution (reference-mapping.md): no framework ships this.
Reference pattern only — Hermes/OpenClaw middleware hook shape.
"""

from __future__ import annotations


def inject_noise(text: str, persona: str, rate: float = 0.08) -> str:
    """Return `text` with human-like imperfections applied.

    TODO(L1): character-level typos, dropped punctuation, Manglish particles
    ("lah", "meh", "can or not"), persona-appropriate register.
    """
    raise NotImplementedError
