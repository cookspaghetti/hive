"""Deterministic regex HVI patterns (fyp.txt L3).

Complements GLiNER2 for high-precision, well-structured indicators.
Reference pattern only — Hermes approval.py pattern-registry idea.
"""

from __future__ import annotations

import re

from hive.state import HVI

# NOTE: placeholder patterns — refine for Malaysian bank formats in Part 2.
PATTERNS: dict[str, re.Pattern[str]] = {
    "url": re.compile(r"https?://[^\s]+"),
    "crypto_btc": re.compile(r"\b(?:bc1|[13])[a-km-zA-HJ-NP-Z1-9]{25,39}\b"),
    "crypto_eth": re.compile(r"\b0x[0-9a-fA-F]{40}\b"),
    "telegram_id": re.compile(r"@[A-Za-z0-9_]{5,32}"),
    "phone_my": re.compile(r"\b(?:\+?60|0)1\d[-\s]?\d{3,4}[-\s]?\d{4}\b"),
}


def extract_regex(text: str, source_msg_id: int) -> list[HVI]:
    hits: list[HVI] = []
    for kind, pat in PATTERNS.items():
        for m in pat.finditer(text):
            hits.append(HVI(kind=kind, value=m.group(0), source_msg_id=source_msg_id))
    return hits
