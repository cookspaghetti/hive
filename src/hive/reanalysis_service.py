"""Background orchestration for immutable archived-case reanalysis."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from hive.analysis_runs import AnalysisRunStore, analysis_run_record, model_manifest
from hive.audit import audit_event
from hive.case_intelligence import CaseIntelligenceStore, build_case_profile
from hive.replay import replay_history_record

ReanalysisRunner = Callable[[dict[str, Any], Any, Any], dict[str, Any]]


def run_reanalysis(
    record: dict[str, Any],
    engine: Any,
    settings: Any,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    session = replay_history_record(record, engine, progress_callback=progress_callback)
    return analysis_run_record(record, session, models=model_manifest(settings))


class ReanalysisService:
    """Run model-backed replay off the request path and expose job status."""

    def __init__(
        self,
        store: AnalysisRunStore,
        *,
        runner: ReanalysisRunner = run_reanalysis,
        case_intelligence: CaseIntelligenceStore | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.case_intelligence = case_intelligence
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def submit(self, record: dict[str, Any], engine: Any, settings: Any) -> dict[str, Any]:
        job_id = str(uuid4())
        job = {
            "id": job_id,
            "history_id": str(record["id"]),
            "peer_id": int(record["peer_id"]),
            "status": "queued",
            "stage": "Queued",
            "progress": 5,
            "created_ts": time.time(),
            "started_ts": None,
            "completed_ts": None,
            "analysis_run_id": None,
            "error": "",
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._execute,
            args=(job_id, dict(record), engine, settings),
            name=f"hive-reanalysis-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return dict(job)

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _execute(self, job_id: str, record: dict[str, Any], engine: Any, settings: Any) -> None:
        self._update(
            job_id,
            status="running",
            stage="Analysing transcript",
            progress=20,
            started_ts=time.time(),
        )
        audit_event(
            "analysis_run",
            "reanalysis_started",
            component="reanalysis.service",
            payload={"job_id": job_id, "history_id": record["id"]},
            peer_id=int(record["peer_id"]),
        )
        try:
            if self.runner is run_reanalysis:
                analysis = run_reanalysis(
                    record,
                    engine,
                    settings,
                    progress_callback=lambda completed, total: self._analysis_progress(
                        job_id, completed, total
                    ),
                )
            else:
                analysis = self.runner(record, engine, settings)
            self._update(job_id, stage="Saving analysis run", progress=78)
            self.store.create(analysis)
        except Exception as exc:  # noqa: BLE001 - persisted job must report any worker failure
            self._update(
                job_id,
                status="failed",
                stage="Failed",
                progress=100,
                completed_ts=time.time(),
                error=str(exc),
            )
            audit_event(
                "analysis_run",
                "reanalysis_failed",
                component="reanalysis.service",
                payload={"job_id": job_id, "history_id": record["id"], "error": str(exc)},
                peer_id=int(record["peer_id"]),
                level="error",
            )
            return
        if self.case_intelligence is not None:
            self._update(job_id, stage="Updating pattern index", progress=92)
            try:
                self.case_intelligence.index(build_case_profile(record, analysis))
            except Exception as exc:  # noqa: BLE001 - retain completed immutable analysis
                audit_event(
                    "case_intelligence",
                    "reanalysis_case_index_failed",
                    component="reanalysis.service",
                    payload={
                        "history_id": record["id"],
                        "analysis_run_id": analysis["id"],
                        "error": str(exc),
                    },
                    peer_id=int(record["peer_id"]),
                    level="error",
                )
        self._update(
            job_id,
            status="completed",
            stage="Complete",
            progress=100,
            completed_ts=time.time(),
            analysis_run_id=analysis["id"],
        )
        audit_event(
            "analysis_run",
            "reanalysis_completed",
            component="reanalysis.service",
            payload={
                "job_id": job_id,
                "history_id": record["id"],
                "analysis_run_id": analysis["id"],
                "verdict": analysis.get("verdict"),
                "score": analysis.get("score"),
            },
            peer_id=int(record["peer_id"]),
        )

    def _analysis_progress(self, job_id: str, completed: int, total: int) -> None:
        if total <= 0:
            return
        bounded = min(max(completed, 0), total)
        progress = 20 + round((bounded / total) * 56)
        self._update(
            job_id,
            stage=f"Analysing transcript · {bounded}/{total} messages",
            progress=min(progress, 76),
        )

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(values)
