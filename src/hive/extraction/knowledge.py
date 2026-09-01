"""Validated, versioned domain knowledge for HIVE indicator extraction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ExtractionKnowledge:
    schema_version: int
    version: str
    sha256: str
    model_id: str
    default_threshold: float
    labels: tuple[str, ...]
    label_to_kind: dict[str, str]
    label_thresholds: dict[str, float]
    bank_names: dict[str, tuple[str, ...]]
    account_minimum_digits: int
    account_maximum_digits: int
    account_context_terms: tuple[str, ...]
    account_negated_phrases: tuple[str, ...]
    name_stopwords: frozenset[str]
    name_boundary_particles: tuple[str, ...]
    english_name_introductions: tuple[str, ...]
    mandarin_name_introductions: tuple[str, ...]


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"extraction knowledge {field} must be a mapping")
    return value


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"extraction knowledge {field} must be a non-empty list")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result) or len(set(result)) != len(result):
        raise ValueError(f"extraction knowledge {field} contains blank or duplicate values")
    return result


def _threshold(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"extraction knowledge {field} must be numeric") from exc
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"extraction knowledge {field} must be between 0 and 1")
    return result


def _parse(raw: bytes) -> ExtractionKnowledge:
    payload = yaml.safe_load(raw)
    root = _mapping(payload, "root")
    schema_version = root.get("schema_version")
    if schema_version != 1:
        raise ValueError(f"unsupported extraction knowledge schema: {schema_version!r}")
    version = str(root.get("knowledge_version") or "").strip()
    if not version:
        raise ValueError("extraction knowledge knowledge_version is required")

    model = _mapping(root.get("model"), "model")
    model_id = str(model.get("id") or "").strip()
    if not model_id:
        raise ValueError("extraction knowledge model.id is required")
    default_threshold = _threshold(model.get("default_threshold"), "model.default_threshold")

    entity_rows = root.get("entities")
    if not isinstance(entity_rows, list) or not entity_rows:
        raise ValueError("extraction knowledge entities must be a non-empty list")
    labels: list[str] = []
    label_to_kind: dict[str, str] = {}
    label_thresholds: dict[str, float] = {}
    for index, raw_entity in enumerate(entity_rows):
        entity = _mapping(raw_entity, f"entities[{index}]")
        kind = str(entity.get("kind") or "").strip()
        if not kind:
            raise ValueError(f"extraction knowledge entities[{index}].kind is required")
        threshold = _threshold(
            entity.get("threshold", default_threshold),
            f"entities[{index}].threshold",
        )
        for label in _strings(entity.get("labels"), f"entities[{index}].labels"):
            key = label.casefold()
            if key in label_to_kind:
                raise ValueError(f"duplicate extraction knowledge entity label: {label}")
            labels.append(label)
            label_to_kind[key] = kind
            label_thresholds[key] = threshold

    raw_banks = _mapping(root.get("bank_names"), "bank_names")
    bank_names = {
        canonical: _strings(aliases, f"bank_names.{canonical}")
        for canonical, aliases in raw_banks.items()
    }
    account = _mapping(root.get("account"), "account")
    minimum_digits = int(account.get("minimum_digits", 0))
    maximum_digits = int(account.get("maximum_digits", 0))
    if minimum_digits < 1 or maximum_digits < minimum_digits:
        raise ValueError("extraction knowledge account digit bounds are invalid")

    names = _mapping(root.get("names"), "names")
    stopwords = frozenset(
        item.casefold() for item in _strings(names.get("stopwords"), "names.stopwords")
    )
    return ExtractionKnowledge(
        schema_version=1,
        version=version,
        sha256=hashlib.sha256(raw).hexdigest(),
        model_id=model_id,
        default_threshold=default_threshold,
        labels=tuple(labels),
        label_to_kind=label_to_kind,
        label_thresholds=label_thresholds,
        bank_names=bank_names,
        account_minimum_digits=minimum_digits,
        account_maximum_digits=maximum_digits,
        account_context_terms=_strings(account.get("context_terms"), "account.context_terms"),
        account_negated_phrases=_strings(
            account.get("negated_phrases"), "account.negated_phrases"
        ),
        name_stopwords=stopwords,
        name_boundary_particles=_strings(
            names.get("boundary_particles"), "names.boundary_particles"
        ),
        english_name_introductions=_strings(
            names.get("english_introductions"), "names.english_introductions"
        ),
        mandarin_name_introductions=_strings(
            names.get("mandarin_introductions"), "names.mandarin_introductions"
        ),
    )


def load_extraction_knowledge(path: str | Path | None = None) -> ExtractionKnowledge:
    """Load and validate extraction knowledge from a path or packaged default."""
    if path is not None:
        return _parse(Path(path).read_bytes())
    return _default_knowledge()


@lru_cache(maxsize=1)
def _default_knowledge() -> ExtractionKnowledge:
    resource = files("hive.extraction").joinpath("knowledge.yaml")
    return _parse(resource.read_bytes())


KNOWLEDGE = load_extraction_knowledge()
