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
import re

from hive.logging_setup import get_logger

log = get_logger(__name__)

# Sensible per-persona defaults; tune during Part 2.
_PERSONA_TYPO_RATE: dict[str, float] = {
    "confused_elderly": 0.012,
    "naive_young_adult": 0.008,
    "overseas_worker": 0.006,
    "small_business_owner": 0.004,
}
_DEFAULT_TYPO_RATE = 0.008

_MANGLISH_PARTICLES = ["lah", "lor", "meh", "ah", "leh"]
_PARTICLE_RE = re.compile(r"\b(?:lah|lor|meh|ah|leh)\b", re.IGNORECASE)

# QWERTY neighbours for believable fat-finger substitutions.
_NEIGHBOURS: dict[str, str] = {
    "a": "sq", "s": "ad", "d": "sf", "f": "dg", "g": "fh", "h": "gj",
    "j": "hk", "k": "jl", "l": "k", "q": "wa", "w": "qe", "e": "wr",
    "r": "et", "t": "ry", "y": "tu", "u": "yi", "i": "uo", "o": "ip",
    "p": "o", "n": "bm", "m": "n", "o0": "0",
}


def _typo_word(word: str, rng: random.Random) -> str:
    if len(word) < 3 or not word.isascii() or not any(char.isalpha() for char in word):
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


def has_manglish_particle(text: str) -> bool:
    return _PARTICLE_RE.search(text) is not None


def inject_noise(
    text: str,
    persona: str,
    *,
    rate: float | None = None,
    manglish: bool = True,
    max_typos: int = 1,
    seed: int | None = None,
) -> str:
    """Return `text` with human-like imperfections applied.

    `rate` overrides the per-persona typo probability; `seed` makes it
    deterministic for tests.
    """
    rng = random.Random(seed)
    typo_rate = rate if rate is not None else _PERSONA_TYPO_RATE.get(persona, _DEFAULT_TYPO_RATE)

    words = text.split()
    normalized = [re.sub(r"[^a-z0-9]", "", word.lower()) for word in words]
    protected = {
        index
        for index, value in enumerate(normalized)
        if value
        and (
            (index > 0 and normalized[index - 1] == value)
            or (index + 1 < len(normalized) and normalized[index + 1] == value)
        )
    }
    noised: list[str] = []
    changed = 0
    for index, word in enumerate(words):
        if changed < max_typos and index not in protected and rng.random() < typo_rate:
            transformed = _typo_word(word, rng)
            changed += transformed != word
            noised.append(transformed)
        else:
            noised.append(word)
    out = " ".join(noised)

    # Occasionally append a Manglish particle (not after punctuation-heavy text).
    latin_letters = sum(char.isascii() and char.isalpha() for char in text)
    all_letters = sum(char.isalpha() for char in text)
    mostly_latin = bool(all_letters) and latin_letters / all_letters >= 0.8
    if (
        manglish
        and words
        and mostly_latin
        and not has_manglish_particle(out)
        and rng.random() < 0.06
    ):
        particle = rng.choice(_MANGLISH_PARTICLES)
        out = out.rstrip(".!") + " " + particle

    log.info(
        "L1 linguistic: persona=%s typo_rate=%.2f words=%d changed~=%d",
        persona,
        typo_rate,
        len(words),
        changed,
    )
    return out
