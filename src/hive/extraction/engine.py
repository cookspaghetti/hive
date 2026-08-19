"""Extraction engine (fyp.txt L3).

Single entry point that harvests HVIs from a message: regex (always) + GLiNER2
NER (when a backend is available), de-duplicated. Each HVI is meant to be
stored in the Evidence Vault and to act as a hard signal for the Verdict
Engine (fyp.txt S6).

The NER backend is optional/injected so extraction runs (regex-only) even
without the model loaded, and so tests stay offline.
"""

from __future__ import annotations

import re

from hive.extraction.ner import NerBackend, extract_entities
from hive.extraction.regex_rules import extract_regex
from hive.logging_setup import get_logger
from hive.state import HVI

log = get_logger(__name__)


_PERSON_HONORIFICS = re.compile(r"^(?:mr|mrs|ms|miss|dr|dato|datuk)\.?\s+", re.IGNORECASE)


def hvi_key(hvi: HVI) -> tuple[str, str]:
    """Return a stable cross-message key for an indicator."""
    value = hvi.value.strip().casefold()
    if hvi.kind == "person_name":
        value = _PERSON_HONORIFICS.sub("", value)
    if hvi.kind in {"bank_account", "phone", "phone_my"}:
        value = re.sub(r"\D", "", value)
    elif hvi.kind == "url":
        value = re.sub(r"^https?://(?:www\.)?", "", value).rstrip("/")
    else:
        value = re.sub(r"[^\w@.+:/-]", "", value)
    return hvi.kind, value


def _dedup(hvis: list[HVI]) -> list[HVI]:
    """Collapse duplicates by (kind, normalised value), keeping highest score."""
    best: dict[tuple[str, str], HVI] = {}
    for h in hvis:
        key = hvi_key(h)
        cur = best.get(key)
        if cur is None or h.confidence > cur.confidence:
            best[key] = h
    return list(best.values())


def merge_hvis(existing: list[HVI], incoming: list[HVI]) -> list[HVI]:
    """Merge indicators across messages and return new or upgraded evidence."""
    by_key = {hvi_key(item): item for item in existing}
    accepted: list[HVI] = []
    for item in _dedup(incoming):
        key = hvi_key(item)
        current = by_key.get(key)
        if current is None:
            existing.append(item)
            by_key[key] = item
            accepted.append(item)
        elif item.confidence > current.confidence:
            current.value = item.value
            current.confidence = item.confidence
            current.source_msg_id = item.source_msg_id
            accepted.append(current)
    return accepted


def extract_hvis(
    text: str,
    source_msg_id: int,
    *,
    ner_backend: NerBackend | None = None,
) -> list[HVI]:
    """Harvest and de-duplicate HVIs from a single message's text."""
    hvis = extract_regex(text, source_msg_id)
    if ner_backend is not None:
        hvis += extract_entities(text, source_msg_id, ner_backend)
    result = _dedup(hvis)
    log.info(
        "L3 extract: msg=%d raw=%d unique=%d kinds=%s",
        source_msg_id,
        len(hvis),
        len(result),
        sorted({h.kind for h in result}),
    )
    return result
