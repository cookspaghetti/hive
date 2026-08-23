"""Run deterministic intelligence extraction against the sanitized corpus."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from hive.extraction.engine import extract_contextual_hvis, extract_hvis, hvi_key, merge_hvis
from hive.state import HVI, Message

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "evaluation" / "indicator_cases.json"


def _expected_key(item: dict[str, Any]) -> tuple[str, str]:
    return hvi_key(
        HVI(
            kind=str(item["kind"]),
            value=str(item["value"]),
            source_msg_id=0,
        )
    )


def _score(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def evaluate_indicator_corpus(path: str | Path = DEFAULT_CORPUS) -> dict[str, Any]:
    corpus_path = Path(path)
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if payload.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("unsupported indicator corpus schema")

    totals = {"tp": 0, "fp": 0, "fn": 0}
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    by_language: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    by_split: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    composition: dict[str, dict[str, int]] = {
        "language": defaultdict(int),
        "category": defaultdict(int),
        "split": defaultdict(int),
        "provenance": defaultdict(int),
        "case_type": defaultdict(int),
    }
    failures: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("id") or "")
        if not case_id or case_id in case_ids:
            raise ValueError("indicator corpus case IDs must be non-empty and unique")
        case_ids.add(case_id)
        expected = {_expected_key(item) for item in case.get("expected", [])}
        raw_messages = case.get("messages")
        if not isinstance(raw_messages, list):
            raw_messages = [{"role": "stranger", "text": str(case["text"])}]
        messages = [
            Message(
                role=str(raw.get("role") or "stranger"),
                text=str(raw.get("text") or ""),
                ts=float(offset),
                msg_id=index * 100 + offset,
            )
            for offset, raw in enumerate(raw_messages, start=1)
        ]
        extracted: list[HVI] = []
        for message in messages:
            if message.role == "stranger":
                merge_hvis(extracted, extract_hvis(message.text, message.msg_id))
        for message, raw in zip(messages, raw_messages, strict=True):
            media_hvis = raw.get("media_hvis", [])
            if isinstance(media_hvis, list):
                merge_hvis(
                    extracted,
                    [
                        HVI(
                            kind=str(item["kind"]),
                            value=str(item["value"]),
                            source_msg_id=message.msg_id,
                            confidence=float(item.get("confidence", 0.8)),
                            extractor="synthetic_media",
                        )
                        for item in media_hvis
                    ],
                )
        current_ids = {message.msg_id for message in messages if message.role == "stranger"}
        merge_hvis(extracted, extract_contextual_hvis(messages, current_ids))
        actual = {hvi_key(item) for item in extracted}
        matched = expected & actual
        unexpected = actual - expected
        missing = expected - actual
        totals["tp"] += len(matched)
        totals["fp"] += len(unexpected)
        totals["fn"] += len(missing)
        language = str(case.get("language") or "unspecified")
        category = str(case.get("category") or "unspecified")
        split = str(case.get("split") or "development")
        provenance = str(case.get("provenance") or payload.get("provenance") or "unspecified")
        case_type = (
            "media"
            if any(raw.get("media_hvis") for raw in raw_messages)
            else "multi_message"
            if len(raw_messages) > 1
            else "positive"
            if expected
            else "hard_negative"
        )
        for group in (by_language[language], by_category[category], by_split[split]):
            group["tp"] += len(matched)
            group["fp"] += len(unexpected)
            group["fn"] += len(missing)
        composition["language"][language] += 1
        composition["category"][category] += 1
        composition["split"][split] += 1
        composition["provenance"][provenance] += 1
        composition["case_type"][case_type] += 1
        for kind, _value in matched:
            by_kind[kind]["tp"] += 1
        for kind, _value in unexpected:
            by_kind[kind]["fp"] += 1
        for kind, _value in missing:
            by_kind[kind]["fn"] += 1
        if unexpected or missing:
            failures.append(
                {
                    "id": case["id"],
                    "unexpected": sorted(unexpected),
                    "missing": sorted(missing),
                }
            )

    return {
        "schema_version": payload["schema_version"],
        "corpus": str(corpus_path),
        "cases": len(cases),
        "overall": _score(totals["tp"], totals["fp"], totals["fn"]),
        "by_kind": {
            kind: _score(values["tp"], values["fp"], values["fn"])
            for kind, values in sorted(by_kind.items())
        },
        "by_language": {
            key: _score(values["tp"], values["fp"], values["fn"])
            for key, values in sorted(by_language.items())
        },
        "by_category": {
            key: _score(values["tp"], values["fp"], values["fn"])
            for key, values in sorted(by_category.items())
        },
        "by_split": {
            key: _score(values["tp"], values["fp"], values["fn"])
            for key, values in sorted(by_split.items())
        },
        "composition": {
            dimension: dict(sorted(values.items()))
            for dimension, values in composition.items()
        },
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--min-precision", type=float, default=0.95)
    parser.add_argument("--min-recall", type=float, default=0.95)
    args = parser.parse_args()
    result = evaluate_indicator_corpus(args.corpus)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    overall = result["overall"]
    if (
        overall["precision"] < args.min_precision
        or overall["recall"] < args.min_recall
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
