from __future__ import annotations

import json
from pathlib import Path

import pytest

from hive.evaluate import DEFAULT_CORPUS, evaluate_indicator_corpus


def test_sanitized_indicator_corpus_meets_regression_gate() -> None:
    result = evaluate_indicator_corpus()

    assert result["cases"] >= 60
    assert result["overall"]["precision"] >= 0.95
    assert result["overall"]["recall"] >= 0.95
    assert [failure["id"] for failure in result["failures"]] == ["manglish-direct-name"]
    assert result["by_kind"]["person_name"]["f1"] == 0.9
    assert {"en", "zh", "manglish"}.issubset(result["by_language"])
    assert result["composition"]["case_type"]["hard_negative"] >= 10
    assert result["composition"]["case_type"]["media"] >= 4
    assert result["composition"]["case_type"]["attachment"] >= 7
    assert result["composition"]["attachment_kind"]["image"] >= 4
    assert result["composition"]["split"]["evaluation"] >= 30
    assert result["fixture_safety"]["validated"] is True
    assert result["fixture_safety"]["executable_content"] is False


def test_indicator_corpus_reports_unexpected_and_missing_values(tmp_path: Path) -> None:
    corpus = {
        "schema_version": 1,
        "cases": [
            {
                "id": "deliberate-mismatch",
                "text": "Call 012-345 6789",
                "expected": [{"kind": "url", "value": "https://missing.example"}],
            }
        ],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")

    result = evaluate_indicator_corpus(path)

    assert result["overall"]["precision"] == 0.0
    assert result["overall"]["recall"] == 0.0
    assert result["failures"] == [
        {
            "id": "deliberate-mismatch",
            "unexpected": [("phone_my", "0123456789")],
            "missing": [("url", "missing.example")],
        }
    ]


def test_indicator_corpus_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "corpus.json"
    path.write_text('{"schema_version": 99, "cases": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported"):
        evaluate_indicator_corpus(path)


def test_default_corpus_is_repository_owned() -> None:
    assert DEFAULT_CORPUS.is_file()
    assert DEFAULT_CORPUS.name == "indicator_cases.json"
