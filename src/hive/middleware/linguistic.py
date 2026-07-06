"""Linguistic noise: typos, regional shorthand, Manglish (fyp.txt L1).

Post-processes the agent's raw reply so it reads like a real Malaysian texting,
not a polished LLM. Original contribution (reference-mapping.md): no reference
framework ships this.

Design notes:
- Typos are applied probabilistically per word so most text stays legible.
- Manglish particles ("lah", "lor", "meh", ...) are added sparingly.
- A `seed` makes the transform deterministic for tests; production passes None.
- Effectiveness is measured by the detection-evasion metric (fyp.txt S9).
"""

from __future__ import annotations

import random

from hive.logging_setup import get_logger

log = get_logger(__name__)

# Sensible per-persona defaults; tune during Part 2.
_PERSONA_TYPO_RATE: dict[str, float] = {
    "confused_elderly": 0.14,       # more fumbling
    "naive_young_adult": 0.10,
    "overseas_worker": 0.07,
    "small_business_owner": 0.05,   # more fluent
}
_DEFAULT_TYPO_RATE = 0.09

_MANGLISH_PARTICLES = ["lah", "lor", "meh", "ah", "leh", "one"]

# QWERTY neighbours for believable fat-finger substitutions.
_NEIGHBOURS: dict[str, str] = {
    "a": "sq", "s": "ad", "d": "sf", "f": "dg", "g": "fh", "h": "gj",
    "j": "hk", "k": "jl", "l": "k", "q": "wa", "w": "qe", "e": "wr",
    "r": "et", "t": "ry", "y": "tu", "u": "yi", "i": "uo", "o": "ip",
    "p": "o", "n": "bm", "m": "n", "o0": "0",
}


def _typo_word(word: str, rng: random.Random) -> str:
    if len(word) < 3:
        return word
    op = rng.choice(("swap", "drop", "double", "neighbour"))
    i = rng.randrange(len(word) - 1)
    if op == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if op == "drop":
        return word[:i] + word[i + 1:]
    if op == "double":
        return word[:i] + word[i] + word[i:]
    # neighbour substitution
    ch = word[i].lower()
    if ch in _NEIGHBOURS:
        sub = rng.choice(_NEIGHBOURS[ch])
        return word[:i] + sub + word[i + 1:]
    return word


def inject_noise(
    text: str,
    persona: str,
    *,
    rate: float | None = None,
    manglish: bool = True,
    seed: int | None = None,
) -> str:
    """Return `text` with human-like imperfections applied.

    `rate` overrides the per-persona typo probability; `seed` makes it
    deterministic for tests.
    """
    rng = random.Random(seed)
    typo_rate = rate if rate is not None else _PERSONA_TYPO_RATE.get(persona, _DEFAULT_TYPO_RATE)

    words = text.split()
    noised = [_typo_word(w, rng) if rng.random() < typo_rate else w for w in words]
    out = " ".join(noised)

    # Occasionally append a Manglish particle (not after punctuation-heavy text).
    if manglish and words and rng.random() < 0.25:
        particle = rng.choice(_MANGLISH_PARTICLES)
        out = out.rstrip(".!") + " " + particle

    log.info(
        "L1 linguistic: persona=%s typo_rate=%.2f words=%d changed~=%d",
        persona,
        typo_rate,
        len(words),
        sum(1 for a, b in zip(words, noised) if a != b),
    )
    return out
