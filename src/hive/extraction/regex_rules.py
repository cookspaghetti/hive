"""Deterministic regex HVI patterns (fyp.txt L3).

Complements GLiNER2 for high-precision, well-structured indicators (URLs,
crypto addresses, phones, Telegram handles). Reference pattern only — Hermes
approval.py pattern-registry idea (reference-mapping.md L3).

Bank account numbers are handled context-sensitively: a bare digit run has too
many false positives, so we only emit an account HVI when a bank keyword sits
nearby. Free-standing account extraction is left to GLiNER2.
"""

from __future__ import annotations

import re

from hive.state import HVI

# High-precision structural patterns. Scam messages commonly omit the scheme
# ("example.co" rather than "https://example.co"), so bare domains are URLs
# too. The leading guard avoids harvesting the domain portion of an email.
_URL_RE = re.compile(
    r"(?<![@\w])(?:"
    r"https?://[^\s<>()]+|"
    r"www\.[^\s<>()]+|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+[a-z]{2,24}"
    r"(?:/[^\s<>()]*)?"
    r")",
    re.IGNORECASE,
)
_URL_TRAILING_PUNCTUATION = ".,!?;:)]}'\""

PATTERNS: dict[str, re.Pattern[str]] = {
    "url": _URL_RE,
    "crypto_btc": re.compile(r"\b(?:bc1|[13])[a-km-zA-HJ-NP-Z1-9]{25,39}\b"),
    "crypto_eth": re.compile(r"\b0x[0-9a-fA-F]{40}\b"),
    "telegram_id": re.compile(r"@[A-Za-z0-9_]{5,32}"),
    "phone_my": re.compile(r"\b(?:\+?60|0)1\d[-\s]?\d{3,4}[-\s]?\d{4}\b"),
}

# Malaysian bank keywords for context-gated account detection.
_BANK_KEYWORDS = (
    "maybank", "cimb", "public bank", "rhb", "hong leong", "ambank", "bank islam",
    "mbb", "bsn", "uob", "ocbc", "hsbc", "affin", "alliance", "account", "acc", "acct",
    "a/c",
)
# 8–17 digit run, optionally spaced/hyphenated, near a bank keyword.
_ACCOUNT_RE = re.compile(r"\b\d[\d\s-]{6,16}\d\b")


def has_bank_context(text: str) -> bool:
    """Return whether text contains a bank or account marker."""
    lower = text.casefold()
    return any(keyword in lower for keyword in _BANK_KEYWORDS)


def extract_regex(text: str, source_msg_id: int) -> list[HVI]:
    hits: list[HVI] = []
    for kind, pat in PATTERNS.items():
        for m in pat.finditer(text):
            value = m.group(0).strip()
            if kind == "url":
                value = value.rstrip(_URL_TRAILING_PUNCTUATION)
                if not re.match(r"https?://", value, re.IGNORECASE):
                    value = f"https://{value}"
            hits.append(
                HVI(
                    kind=kind,
                    value=value,
                    source_msg_id=source_msg_id,
                    extractor="regex",
                )
            )

    if has_bank_context(text):
        for m in _ACCOUNT_RE.finditer(text):
            digits = re.sub(r"\D", "", m.group(0))
            if 8 <= len(digits) <= 17:
                hits.append(
                    HVI(
                        kind="bank_account",
                        value=digits,
                        source_msg_id=source_msg_id,
                        confidence=0.6,
                        extractor="regex",
                    )
                )
    return hits
