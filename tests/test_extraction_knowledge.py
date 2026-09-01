"""Versioned extraction knowledge registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hive.analysis_runs import ANALYSIS_VERSIONS
from hive.extraction.knowledge import KNOWLEDGE, load_extraction_knowledge
from hive.extraction.ner import DEFAULT_LABELS, extract_entities


class ThresholdBackend:
    def predict(self, text, labels):
        assert labels == DEFAULT_LABELS
        return [
            ("url", "example.test", 0.69),
            ("person name", "Alex Tan", 0.59),
            ("company name", "Example Trading", 0.61),
        ]


def test_packaged_knowledge_is_loaded_and_recorded_in_analysis_provenance():
    assert KNOWLEDGE.schema_version == 1
    assert KNOWLEDGE.version == "2026-09-01-v1"
    assert len(KNOWLEDGE.sha256) == 64
    assert DEFAULT_LABELS == list(KNOWLEDGE.labels)
    assert KNOWLEDGE.bank_names["Maybank"] == ("maybank", "mbb")
    assert ANALYSIS_VERSIONS["extraction_knowledge"] == KNOWLEDGE.version
    assert ANALYSIS_VERSIONS["extraction_knowledge_sha256"] == KNOWLEDGE.sha256


def test_per_label_thresholds_filter_noisy_gliner_predictions():
    extracted = extract_entities("sample", 1, ThresholdBackend())

    assert [(item.kind, item.value) for item in extracted] == [
        ("organization", "Example Trading")
    ]


def test_invalid_knowledge_is_rejected(tmp_path: Path):
    path = tmp_path / "knowledge.yaml"
    path.write_text(
        """
schema_version: 1
knowledge_version: bad-test
model: {id: test/model, default_threshold: 0.5}
entities:
  - {kind: url, labels: [url], threshold: 1.2}
bank_names: {Test Bank: [test]}
account:
  minimum_digits: 8
  maximum_digits: 17
  context_terms: [account]
  negated_phrases: [not an account]
names:
  stopwords: [hello]
  boundary_particles: [and]
  english_introductions: [my name is]
  mandarin_introductions: [我叫]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="between 0 and 1"):
        load_extraction_knowledge(path)
