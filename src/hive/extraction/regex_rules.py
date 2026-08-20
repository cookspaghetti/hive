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
    "telegram_id": re.compile(r"(?<![\w.+-])@[A-Za-z0-9_]{5,32}"),
    "phone_my": re.compile(r"\b(?:\+?60|0)1\d[-\s]?\d{3,4}[-\s]?\d{4}\b"),
}

# Malaysian bank keywords for context-gated account detection.
_BANK_NAMES: dict[str, tuple[str, ...]] = {
    "Maybank": ("maybank", "mbb"),
    "CIMB": ("cimb",),
    "Public Bank": ("public bank", "pbb"),
    "RHB": ("rhb",),
    "Hong Leong Bank": ("hong leong", "hlb"),
    "AmBank": ("ambank",),
    "Bank Islam": ("bank islam",),
    "BSN": ("bsn",),
    "UOB": ("uob",),
    "OCBC": ("ocbc",),
    "HSBC": ("hsbc",),
    "Affin Bank": ("affin",),
    "Alliance Bank": ("alliance bank",),
}
_ACCOUNT_CONTEXT = re.compile(
    r"\b(?:bank\s+account|account|acct|acc|a/c)\b|(?:账户|账号|银行)",
    re.IGNORECASE,
)
# 8–17 digit run, optionally spaced/hyphenated, near a bank keyword.
_ACCOUNT_RE = re.compile(r"\b\d[\d\s-]{6,16}\d\b")
_NEGATED_BANK_CONTEXT = re.compile(
    r"\b(?:not|isn'?t|is not)\s+(?:a\s+|my\s+|the\s+)?(?:bank\s+)?account\b|"
    r"(?:不是|并非)(?:银行)?(?:账号|账户)",
    re.IGNORECASE,
)


def has_bank_context(text: str) -> bool:
    """Return whether text contains a bank or account marker."""
    if _NEGATED_BANK_CONTEXT.search(text):
        return False
    return bool(bank_names(text) or _ACCOUNT_CONTEXT.search(text))


def bank_names(text: str) -> list[str]:
    """Return canonical bank names explicitly mentioned in text."""
    names: list[str] = []
    for canonical, aliases in _BANK_NAMES.items():
        if any(
            re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.IGNORECASE)
            for alias in aliases
        ):
            names.append(canonical)
    return names


def account_numbers(text: str) -> list[str]:
    """Return structurally plausible account-number candidates without context."""
    numbers: list[str] = []
    for match in _ACCOUNT_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 17:
            numbers.append(digits)
    return list(dict.fromkeys(numbers))


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

    for name in bank_names(text):
        hits.append(
            HVI(
                kind="bank_name",
                value=name,
                source_msg_id=source_msg_id,
                confidence=0.95,
                extractor="regex",
            )
        )

    if has_bank_context(text):
        account_values = account_numbers(text)
        if account_values:
            hits = [
                item
                for item in hits
                if not (
                    item.kind in {"phone", "phone_my"}
                    and re.sub(r"\D", "", item.value) in account_values
                )
            ]
        for digits in account_values:
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
