"""Archived-case background reanalysis orchestration tests."""

import time

from hive.analysis_runs import LocalAnalysisRunStore, analysis_run_record
from hive.reanalysis_service import ReanalysisService
from hive.state import SessionState


def _record() -> dict:
    return {
        "id": "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
        "peer_id": 919,
        "persona": "confused_elderly",
        "messages": [],
    }


def _runner(record, engine, settings):
    session = SessionState(peer_id=record["peer_id"], persona=record["persona"])
    session.verdict = "inconclusive"
    return analysis_run_record(record, session, created_ts=20)


def _wait(service: ReanalysisService, job_id: str) -> dict:
    for _attempt in range(100):
        job = service.status(job_id)
        if job and job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("reanalysis job did not finish")


def test_reanalysis_service_persists_completed_run(tmp_path):
    store = LocalAnalysisRunStore(tmp_path)
    service = ReanalysisService(store, runner=_runner)

    job = _wait(service, service.submit(_record(), object(), object())["id"])

    assert job["status"] == "completed"
    assert job["stage"] == "Complete"
    assert job["progress"] == 100
    assert store.get(_record()["id"], job["analysis_run_id"])["kind"] == "reanalysis"


def test_reanalysis_service_reports_worker_failure(tmp_path):
    def fail(record, engine, settings):
        raise RuntimeError("model unavailable")

    service = ReanalysisService(LocalAnalysisRunStore(tmp_path), runner=fail)

    job = _wait(service, service.submit(_record(), object(), object())["id"])

    assert job["status"] == "failed"
    assert job["stage"] == "Failed"
    assert job["progress"] == 100
    assert job["error"] == "model unavailable"
