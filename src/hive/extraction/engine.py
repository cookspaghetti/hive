"""Extraction engine (fyp.txt L3).

Single entry point that harvests HVIs from a message: regex (always) + GLiNER2
NER (when a backend is available), de-duplicated. Each HVI is meant to be
stored in the Evidence Vault and to act as a hard signal for the Verdict
Engine (fyp.txt S6).

The NER backend is optional/injected so extraction runs (regex-only) even
without the model loaded, and so tests stay offline.
"""

from __future__ import annotations

from hive.extraction.ner import NerBackend, extract_entities
from hive.extraction.regex_rules import extract_regex
from hive.logging_setup import get_logger
from hive.state import HVI

log = get_logger(__name__)


def _dedup(hvis: list[HVI]) -> list[HVI]:
    """Collapse duplicates by (kind, normalised value), keeping highest score."""
    best: dict[tuple[str, str], HVI] = {}
    for h in hvis:
        key = (h.kind, h.value.lower().replace(" ", ""))
        cur = best.get(key)
        if cur is None or h.confidence > cur.confidence:
            best[key] = h
    return list(best.values())


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
