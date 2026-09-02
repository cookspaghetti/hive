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
from urllib.parse import urlparse

from hive.extraction.knowledge import KNOWLEDGE
from hive.extraction.ner import NerBackend, extract_entities
from hive.extraction.regex_rules import account_numbers, extract_regex, has_bank_context
from hive.logging_setup import get_logger
from hive.state import HVI, Message

log = get_logger(__name__)


_PERSON_HONORIFICS = re.compile(r"^(?:mr|mrs|ms|miss|dr|dato|datuk)\.?\s+", re.IGNORECASE)
_PHONE_MY = re.compile(r"^(?:\+?60|0)1\d{8,9}$")
_LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,3}")
_CJK_NAME = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,6}")
_ENGLISH_NAME_INTRODUCTIONS = "|".join(
    re.escape(value)
    for value in sorted(KNOWLEDGE.english_name_introductions, key=len, reverse=True)
)
_MANDARIN_NAME_INTRODUCTIONS = "|".join(
    re.escape(value)
    for value in sorted(KNOWLEDGE.mandarin_name_introductions, key=len, reverse=True)
)
_NAME_BOUNDARY_PARTICLES = "|".join(
    re.escape(value)
    for value in sorted(KNOWLEDGE.name_boundary_particles, key=len, reverse=True)
)
_DIRECT_NAME_PATTERNS = (
    re.compile(
        rf"\b(?:{_ENGLISH_NAME_INTRODUCTIONS})\s+"
        r"([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,3})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:{_MANDARIN_NAME_INTRODUCTIONS})"
        r"\s*([\u3400-\u4dbf\u4e00-\u9fff]{2,6})"
    ),
)
_NAME_QUESTION = re.compile(
    r"\b(?:what|which|whose)\s+(?:is\s+the\s+)?name\b|"
    r"\b(?:under|holder|beneficiary)\b.{0,24}\bname\b|"
    r"(?:叫什么|什么名字|谁的名字|户名|账户名|收款人)",
    re.IGNORECASE,
)
_AGENT_REFERENCE = re.compile(
    r"\b(?:this|that|he|she)\s*(?:is|'s)?\s*my\s+agent\b|"
    r"\bmy\s+agent\b|(?:这是|他是|她是).{0,8}(?:代理|经纪人)",
    re.IGNORECASE,
)
_NAME_STOPWORDS = KNOWLEDGE.name_stopwords


def _validate_hvi(item: HVI, source_text: str) -> HVI | None:
    """Apply deterministic type guards to noisy NER spans."""
    item.value = item.value.strip()
    if not item.value:
        return None

    if item.kind in {"phone", "phone_my", "bank_account"}:
        digits = re.sub(r"\D", "", item.value)
        if item.kind == "bank_account":
            if not (
                KNOWLEDGE.account_minimum_digits
                <= len(digits)
                <= KNOWLEDGE.account_maximum_digits
            ):
                return None
            item.value = digits
            return item

        # A label such as "phone number" is not itself an indicator.
        if not 8 <= len(digits) <= 15:
            return None
        compact = re.sub(r"[\s()-]", "", item.value)
        if has_bank_context(source_text) and not _PHONE_MY.fullmatch(compact):
            item.kind = "bank_account"
            item.value = digits
        return item

    if item.kind == "url":
        candidate = item.value
        if not re.match(r"https?://", candidate, re.IGNORECASE):
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if "." not in parsed.hostname and parsed.hostname != "localhost":
            return None
        item.value = candidate
        return item

    if item.kind == "person_name":
        letters = re.findall(r"[^\W\d_]", item.value, re.UNICODE)
        if len(letters) < 2 or not _plausible_name(item.value):
            return None
    return item


def _plausible_name(text: str) -> bool:
    value = text.strip().strip(".,!?;:'\"")
    if not value or any(character.isdigit() for character in value):
        return False
    words = {word.casefold().strip(".'-") for word in value.split()}
    if value.casefold() in _NAME_STOPWORDS or words.issubset(_NAME_STOPWORDS):
        return False
    return bool(_LATIN_NAME.fullmatch(value) or _CJK_NAME.fullmatch(value))


def _context_name(text: str) -> str | None:
    for pattern in _DIRECT_NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = re.split(
                rf"\b(?:{_NAME_BOUNDARY_PARTICLES})\b",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            candidate = candidate.strip().strip(".,!?;:'\"")
            if _plausible_name(candidate):
                return candidate.strip()
    return None


def extract_contextual_hvis(
    messages: list[Message],
    current_message_ids: set[int],
) -> list[HVI]:
    """Extract indicators whose meaning depends on neighbouring messages.

    The window is intentionally small and rule-bound. It covers split account
    disclosures and names introduced by a preceding question or following role
    description without treating arbitrary short chat fragments as entities.
    """
    hits: list[HVI] = []
    for index, message in enumerate(messages):
        if message.role != "stranger":
            continue

        if message.msg_id in current_message_ids:
            name = _context_name(message.text)
            if name:
                hits.append(
                    HVI(
                        kind="person_name",
                        value=name,
                        source_msg_id=message.msg_id,
                        confidence=0.86,
                        extractor="context",
                    )
                )

            if _plausible_name(message.text):
                recent = messages[max(0, index - 4) : index]
                if any(
                    item.role == "agent" and _NAME_QUESTION.search(item.text)
                    for item in recent
                ):
                    hits.append(
                        HVI(
                            kind="person_name",
                            value=message.text.strip(),
                            source_msg_id=message.msg_id,
                            confidence=0.78,
                            extractor="context",
                        )
                    )

        if message.msg_id in current_message_ids and _AGENT_REFERENCE.search(message.text):
            for candidate in reversed(messages[max(0, index - 3) : index]):
                if candidate.role == "stranger" and _plausible_name(candidate.text):
                    hits.append(
                        HVI(
                            kind="person_name",
                            value=candidate.text.strip(),
                            source_msg_id=candidate.msg_id,
                            confidence=0.84,
                            extractor="context",
                        )
                    )
                    break

    for index, message in enumerate(messages):
        if message.role != "stranger":
            continue
        candidates = account_numbers(message.text)
        if not candidates:
            continue
        neighbours = messages[max(0, index - 2) : index + 3]
        contextual = [item for item in neighbours if has_bank_context(item.text)]
        if not contextual or not (
            message.msg_id in current_message_ids
            or any(item.msg_id in current_message_ids for item in contextual)
        ):
            continue
        for value in candidates:
            hits.append(
                HVI(
                    kind="bank_account",
                    value=value,
                    source_msg_id=message.msg_id,
                    confidence=0.55,
                    extractor="context",
                )
            )

    validated = [
        item
        for item in hits
        if _validate_hvi(
            item,
            next(message.text for message in messages if message.msg_id == item.source_msg_id),
        )
        is not None
    ]
    return _dedup(validated)


def hvi_key(hvi: HVI) -> tuple[str, str]:
    """Return a stable cross-message key for an indicator."""
    value = hvi.value.strip().casefold()
    if hvi.kind == "person_name":
        value = _PERSON_HONORIFICS.sub("", value)
        value = re.sub(r"[^\w\s'-]", "", value)
        value = re.sub(r"\s+", " ", value).strip()
    if hvi.kind in {"bank_account", "phone", "phone_my"}:
        digits = re.sub(r"\D", "", value)
        value = digits or value
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
    validated = [item for item in hvis if _validate_hvi(item, text) is not None]
    result = _dedup(validated)
    log.info(
        "L3 extract: msg=%d raw=%d unique=%d kinds=%s",
        source_msg_id,
        len(hvis),
        len(result),
        sorted({h.kind for h in result}),
    )
    return result
