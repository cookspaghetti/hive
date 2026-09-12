"""Checkpoint and resume behaviour for long red-team campaigns."""

from __future__ import annotations

import json

import pytest

from hive.redteam.evaluate import load_checkpointed_results, pending_matrix_jobs
from hive.redteam.runner import ExtractionMetrics, RunResult, write_results
from hive.redteam.scenarios import DEFAULT_SCENARIOS


def test_pending_matrix_jobs_preserves_order_and_skips_completed_rows() -> None:
    scenarios = list(DEFAULT_SCENARIOS[:2])
    personas = ["confused_elderly", "overseas_worker"]
    completed = {
        (scenarios[0].key, personas[0], 1),
        (scenarios[0].key, personas[0], 2),
        (scenarios[0].key, personas[1], 1),
    }

    jobs = pending_matrix_jobs(scenarios, personas, 2, completed)

    assert [(job[1].key, job[3], job[4]) for job in jobs] == [
        (scenarios[0].key, personas[1], 2),
        (scenarios[1].key, personas[0], 1),
        (scenarios[1].key, personas[0], 2),
        (scenarios[1].key, personas[1], 1),
        (scenarios[1].key, personas[1], 2),
    ]


def test_checkpoint_round_trip_restores_nested_metrics(tmp_path) -> None:
    expected = RunResult(
        archetype="job_scam",
        persona="confused_elderly",
        scenario="job-zh-account",
        replicate=2,
        language="zh",
        transcript=[("scammer", "你好"), ("hive", "请问是什么工作？")],
        extraction=ExtractionMetrics(
            true_positive=2,
            false_positive=1,
            false_negative=0,
            precision=2 / 3,
            recall=1.0,
            f1=0.8,
        ),
        chain_valid=True,
        evidence_verified=True,
    )

    write_results([expected], tmp_path)
    restored = load_checkpointed_results(tmp_path)

    assert restored == [expected]
    assert isinstance(restored[0].extraction, ExtractionMetrics)
    assert not list(tmp_path.glob(".*.tmp"))


def test_checkpoint_rejects_unknown_result_fields(tmp_path) -> None:
    payload = RunResult(archetype="parcel", persona="confused_elderly").as_dict()
    payload["future_unvalidated_field"] = True
    (tmp_path / "redteam_runs.json").write_text(
        json.dumps([payload]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected fields"):
        load_checkpointed_results(tmp_path)
