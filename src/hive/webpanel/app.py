"""Durable localhost control panel for setup and live HIVE operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from hive.agent.personas import PERSONAS
from hive.analysis_runs import (
    AnalysisRunStore,
    analysis_summary,
    build_analysis_run_store,
)
from hive.audit import AuditLedger, get_audit_ledger
from hive.case_intelligence import (
    CaseIntelligenceStore,
    build_case_intelligence_store,
    build_case_profile,
)
from hive.config import load_settings
from hive.history import HistoryStore, build_history_store
from hive.logging_setup import get_logger
from hive.provisioning import EnvStore, TelethonLoginManager
from hive.reanalysis_service import ReanalysisRunner, ReanalysisService
from hive.runtime_manager import ActiveSessionsError, RuntimeNotReadyError, probe_llm
from hive.takeover import (
    TakeoverBusyError,
    TakeoverCoordinator,
    TakeoverNotFoundError,
    TakeoverSealError,
)
from hive.vault.paths import parse_bundle_name
from hive.webpanel.assets import LOGO_PATH, PANEL_CSS_PATH, PANEL_JS_PATH
from hive.webpanel.observability import get_observation_hub
from hive.webpanel.setup_app import panel_page, register_setup_routes

log = get_logger(__name__)
VALID_PERSONAS = set(PERSONAS)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_IMPORTANT_ACTIVITY_TYPES = frozenset(
    {
        "budget",
        "configuration",
        "control_message",
        "extraction",
        "llm_error",
        "media_capture",
        "operator_event",
        "reply_delivery",
        "reply_steering",
        "sandbox",
        "session_lifecycle",
        "signing_key",
        "takeover",
        "takeover_request",
        "telegram_authorisation",
        "verdict",
        "vision_error",
    }
)
_IMPORTANT_SESSION_ACTIONS = frozenset(
    {
        "benign_handback_requested",
        "session_seal_failed",
        "session_sealed",
        "session_started",
    }
)
_IMPORTANT_TAKEOVER_REQUEST_ACTIONS = frozenset(
    {
        "takeover_request_created",
        "takeover_request_dismissed",
        "takeover_request_notification_failed",
    }
)


def _session_summary(peer_id, session) -> dict:
    started = session.started_ts
    return {
        "peer_id": peer_id,
        "persona": session.persona,
        "phase": session.phase.value if hasattr(session.phase, "value") else str(session.phase),
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "exchanges": getattr(session, "exchange_count", session.turn_count),
        "hvis": len(session.hvis),
        "sandbox": len(session.sandbox_results),
        "started_ts": started,
        "duration_s": max(0, round(time.time() - started)) if started else None,
        "last_message_ts": session.messages[-1].ts if session.messages else None,
    }


def _session_detail(peer_id, session) -> dict:
    detail = _session_summary(peer_id, session)
    detail["session_id"] = session.session_id
    detail["messages"] = [
        {
            "role": m.role,
            "text": m.text,
            "ts": m.ts,
            "msg_id": m.msg_id,
            "media_kind": m.media_kind,
            "media_name": m.media_name,
            "media_mime": m.media_mime,
            "media_size": m.media_size,
            "media_available": bool(m.media_path),
            "media_sha256": m.media_sha256,
            "media_url": (
                f"/api/media/{session.session_id}/{m.msg_id}" if m.media_path else None
            ),
        }
        for m in session.messages
    ]
    detail["hvi_items"] = [
        {
            "kind": h.kind,
            "value": h.value,
            "confidence": round(h.confidence, 2),
            "source_msg_id": h.source_msg_id,
            "extractor": h.extractor,
        }
        for h in session.hvis
    ]
    detail["sandbox_results"] = session.sandbox_results
    detail["media_analysis"] = session.media_analysis
    detail["signal_trail"] = session.signal_trail[-20:]
    return detail


def _history_detail(record: dict[str, Any]) -> dict[str, Any]:
    """Add authenticated media URLs without exposing archive internals."""
    detail = dict(record)
    session_id = str(record.get("session_id") or "")
    messages = []
    for original in record.get("messages", []):
        message = dict(original)
        available = bool(message.pop("media_path", None) or message.get("media_available"))
        message["media_available"] = available
        message["media_url"] = (
            f"/api/media/{session_id}/{message.get('msg_id')}"
            if available and _SESSION_ID.fullmatch(session_id)
            else None
        )
        messages.append(message)
    detail["messages"] = messages
    return detail


def _embedded_analysis(record: dict[str, Any]) -> dict[str, Any] | None:
    metadata = record.get("analysis")
    if not isinstance(metadata, dict) or not metadata.get("id"):
        return None
    return {
        **metadata,
        "peer_id": record.get("peer_id"),
        "verdict": record.get("verdict"),
        "score": record.get("score"),
        "turns": record.get("turns"),
        "exchanges": record.get("exchanges"),
    }


def _history_with_analysis(
    record: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    detail = _history_detail(record)
    for field in (
        "verdict",
        "score",
        "turns",
        "exchanges",
        "hvi_items",
        "sandbox_results",
        "signal_trail",
        "media_analysis",
        "related_cases",
    ):
        if field in analysis:
            detail[field] = analysis[field]
    detail["selected_analysis"] = analysis_summary(analysis)
    return detail


def _pending_takeover_requests(userbot: Any) -> list[dict[str, object]]:
    return [
        chat
        for chat in userbot.list_observed_chats()
        if bool(chat.get("request_pending", not chat.get("active", False)))
    ]


def _important_activity(row: dict[str, Any]) -> bool:
    event_type = str(row.get("event_type", ""))
    action = str(row.get("action", ""))
    payload = row.get("payload") or {}
    if event_type == "operator_event":
        return True
    if event_type in {"configuration", "signing_key", "telegram_authorisation"}:
        return True
    if event_type in {"budget", "llm_error", "sandbox", "vision_error"}:
        return True
    if event_type == "extraction":
        return bool(payload.get("discovered"))
    if event_type == "session_lifecycle":
        return action in _IMPORTANT_SESSION_ACTIONS
    if event_type == "takeover":
        return True
    if event_type == "takeover_request":
        return action in _IMPORTANT_TAKEOVER_REQUEST_ACTIONS
    if event_type == "media_capture":
        return action in {"telegram_media_captured", "telegram_media_capture_failed"}
    if event_type == "reply_delivery":
        return action == "telegram_send_failed"
    if event_type == "reply_steering":
        return action == "reply_steering_failed"
    if event_type == "control_message":
        return action == "control_bot_document_sent"
    return event_type == "verdict"


def _activity_detail(row: dict[str, Any], *, exhaustive: bool) -> str:
    payload = row.get("payload") or {}
    if exhaustive:
        detail = payload.get("message") or payload.get("text") or payload.get("detail")
        if not detail and payload:
            detail = json.dumps(payload, ensure_ascii=False, default=str)
        return str(detail or "")

    action = str(row.get("action", ""))
    if action == "indicators_extracted":
        discovered = payload.get("discovered") or []
        values = [
            f"{str(item.get('kind', 'indicator')).replace('_', ' ')}: {item.get('value', '')}"
            for item in discovered[:3]
            if isinstance(item, dict)
        ]
        suffix = f" (+{len(discovered) - 3} more)" if len(discovered) > 3 else ""
        return f"{len(discovered)} new indicator(s): {', '.join(values)}{suffix}"
    if action == "verdict_updated":
        verdict = str(payload.get("verdict", "inconclusive")).replace("_", " ").title()
        score = round(float(payload.get("score", 0) or 0) * 100)
        return f"{verdict} · {score}% risk"
    if action == "takeover_request_created":
        sender = payload.get("name") or payload.get("username") or "New contact"
        text = str(payload.get("text", "")).strip()
        return f"{sender}: {text}" if text else str(sender)
    if action == "takeover_started":
        return f"Persona: {str(payload.get('persona', 'default')).replace('_', ' ')}"
    if action in {"session_sealed", "stop_and_seal_completed"}:
        bundle = payload.get("bundle") or payload.get("out_path")
        verdict = str(payload.get("verdict", "")).replace("_", " ")
        return " · ".join(str(value) for value in (verdict, bundle) if value)
    if action == "telegram_media_captured":
        name = payload.get("name") or "Telegram media"
        size = payload.get("size")
        return f"{name} · {size} bytes" if size is not None else str(name)
    for key in ("detail", "message", "error", "summary", "reason", "url", "path"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _activity_severity(row: dict[str, Any]) -> str:
    level = str(row.get("level", "info"))
    action = str(row.get("action", ""))
    payload = row.get("payload") or {}
    if level in {"error", "critical"} or action.endswith("_failed"):
        return "error"
    if level == "warning" or payload.get("verdict") == "likely_scam":
        return "warning"
    if action.endswith(("_completed", "_created", "_saved", "_sealed", "_succeeded")):
        return "success"
    return "info"


def _audit_activity(
    ledger: AuditLedger,
    *,
    after: int = 0,
    limit: int = 200,
    important_only: bool = True,
) -> list[dict]:
    rows = ledger.list(
        after=after,
        limit=1000 if important_only else limit,
        event_types=_IMPORTANT_ACTIVITY_TYPES if important_only else None,
    )
    items = []
    last_verdict: dict[tuple[object, object], str] = {}
    for row in rows:
        if important_only and not _important_activity(row):
            continue
        payload = row.get("payload") or {}
        if important_only and row.get("event_type") == "verdict":
            key = (row.get("peer_id"), row.get("session_id"))
            verdict = str(payload.get("verdict", ""))
            if last_verdict.get(key) == verdict:
                continue
            last_verdict[key] = verdict
        action = str(row.get("action") or row.get("event_type") or "Activity")
        items.append(
            {
                "id": row.get("sequence", 0),
                "ts": row.get("ts"),
                "category": row.get("event_type") or row.get("component"),
                "title": action.replace("_", " ").capitalize(),
                "detail": _activity_detail(row, exhaustive=not important_only),
                "peer_id": row.get("peer_id"),
                "severity": _activity_severity(row),
            }
        )
    return items[-max(1, min(limit, 1000)) :]


class _LegacyRuntime:
    """Compatibility adapter for callers that pass engine/userbot/settings."""

    def __init__(self, engine: Any, userbot: Any, settings: Any) -> None:
        self.engine = engine
        self.userbot = userbot
        self.settings = settings
        self.is_running = True
        self.restart_required = False

    def snapshot(self) -> dict[str, object]:
        return {
            "state": "running",
            "running": True,
            "restart_required": False,
            "active_sessions": len(self.userbot._sessions),
            "error": "",
            "ready": True,
            "checks": {},
        }

    def mark_restart_required(self) -> None:
        self.restart_required = True

    async def start(self) -> dict[str, object]:
        return self.snapshot()

    async def stop(self, *, force: bool = False) -> dict[str, object]:
        return self.snapshot()

    async def restart(self, *, force: bool = False) -> dict[str, object]:
        return self.snapshot()


def create_app(
    engine: Any = None,
    userbot: Any = None,
    settings: Any = None,
    *,
    runtime_manager: Any = None,
    root: str | Path = ".",
    session_token: str | None = None,
    bound_host: str = "",
    auto_start: bool = False,
    env_store: EnvStore | None = None,
    login_manager: TelethonLoginManager | None = None,
    history_store: HistoryStore | None = None,
    analysis_run_store: AnalysisRunStore | None = None,
    case_intelligence_store: CaseIntelligenceStore | None = None,
    reanalysis_runner: ReanalysisRunner | None = None,
    audit_ledger: AuditLedger | None = None,
) -> FastAPI:
    """Create one panel that remains available across Telegram restarts."""
    if runtime_manager is None:
        if engine is None or userbot is None or settings is None:
            raise ValueError("runtime_manager or engine/userbot/settings are required")
        runtime_manager = _LegacyRuntime(engine, userbot, settings)
    runtime = runtime_manager
    project_root = Path(root).resolve()
    token = session_token or getattr(settings, "panel_token", "") or secrets.token_urlsafe(32)
    store = env_store or EnvStore(project_root / ".env")
    configured = settings or load_settings()
    configured_media_root = Path(
        getattr(configured, "media_path", "./evidence/media")
    )
    media_root = (
        configured_media_root
        if configured_media_root.is_absolute()
        else project_root / configured_media_root
    ).resolve()
    history = history_store or build_history_store(
        project_root / "evidence" / "history",
        getattr(configured, "database_url", ""),
    )
    analysis_runs = analysis_run_store or build_analysis_run_store(
        project_root / "evidence" / "history" / "analysis_runs",
        getattr(configured, "database_url", ""),
    )
    case_intelligence = case_intelligence_store or build_case_intelligence_store(
        project_root / "evidence" / "cases",
        getattr(configured, "database_url", ""),
        qdrant_url=getattr(configured, "qdrant_url", ""),
        enable_semantic=getattr(configured, "use_case_similarity", False),
        similarity_threshold=getattr(configured, "case_similarity_threshold", 0.72),
        embedding_model=getattr(
            configured,
            "case_embedding_model",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
    )
    reanalysis = ReanalysisService(
        analysis_runs,
        case_intelligence=case_intelligence,
        **({"runner": reanalysis_runner} if reanalysis_runner is not None else {}),
    )
    fallback_takeovers = (
        TakeoverCoordinator(
            runtime.engine,
            runtime.userbot,
            configured,
            history,
            evidence_root=project_root / "evidence",
            case_intelligence=case_intelligence,
        )
        if isinstance(runtime, _LegacyRuntime)
        else None
    )
    telethon_login = login_manager or TelethonLoginManager(store)
    observations = get_observation_hub()
    audit = audit_ledger or get_audit_ledger()
    migrate_history_ids = getattr(history, "migrate_legacy_ids", None)
    if migrate_history_ids is not None:
        migrate_history_ids()
    migrate_analysis_metadata = getattr(history, "migrate_analysis_metadata", None)
    if migrate_analysis_metadata is not None:
        migrate_analysis_metadata()
    for summary in history.list():
        record = history.get(str(summary["id"]))
        if record is None or record.get("replay_of"):
            continue
        embedded = record.get("analysis") or {}
        existing = case_intelligence.get(str(record["id"]))
        if existing and existing.get("analysis_run_id") == embedded.get("id"):
            continue
        try:
            case_intelligence.index(build_case_profile(record))
        except Exception:
            log.exception("case intelligence: startup backfill failed case=%s", record["id"])
    observations.event("runtime", "Control panel ready", "Local operator console initialized")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if auto_start:
            app.state.runtime_start_task = asyncio.create_task(_start_if_ready(runtime))
        yield
        start_task = getattr(app.state, "runtime_start_task", None)
        if start_task is not None and not start_task.done():
            start_task.cancel()
        await telethon_login.close()
        if auto_start:
            await runtime.stop(force=True)

    app = FastAPI(title="HIVE Control Panel", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def audit_http_request(request: Request, call_next):
        started = time.perf_counter()
        payload = {
            "method": request.method,
            "path": request.url.path,
            "query_keys": sorted(request.query_params.keys()),
            "client": request.client.host if request.client else "",
        }
        try:
            response = await call_next(request)
        except Exception as exc:
            audit.append(
                "panel_request",
                "http_request_failed",
                component="webpanel.http",
                payload={
                    **payload,
                    "error": str(exc),
                    "duration_s": time.perf_counter() - started,
                },
                level="error",
            )
            raise
        audit.append(
            "panel_request",
            "http_request_completed",
            component="webpanel.http",
            payload={
                **payload,
                "status_code": response.status_code,
                "duration_s": time.perf_counter() - started,
            },
            level="warning" if response.status_code >= 400 else "info",
        )
        return response

    def authorised(supplied: str) -> bool:
        candidates = [token]
        try:
            persistent = load_settings().panel_token
        except Exception:
            persistent = getattr(getattr(runtime, "settings", None), "panel_token", "")
        if persistent:
            candidates.append(persistent)
        return bool(supplied) and any(hmac.compare_digest(supplied, value) for value in candidates)

    def auth(x_hive_token: str = Header(default="")) -> None:
        if not authorised(x_hive_token):
            raise HTTPException(status_code=401, detail="unauthorised")

    if bound_host:
        app.state.bound_host = bound_host

        @app.middleware("http")
        async def validate_host(request: Request, call_next):
            host = _host_without_port(request.headers.get("host", ""))
            if not _accepted_host(host, bound_host):
                return JSONResponse(status_code=400, content={"detail": "invalid Host header"})
            return await call_next(request)

    register_setup_routes(
        app,
        auth=auth,
        root=project_root,
        env_store=store,
        login_manager=telethon_login,
        on_change=runtime.mark_restart_required,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(
            panel_page(token),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/logo.png")
    @app.get("/favicon.png")
    def logo() -> FileResponse:
        return FileResponse(LOGO_PATH, media_type="image/png")

    @app.get("/panel.css")
    def panel_css() -> FileResponse:
        return FileResponse(
            PANEL_CSS_PATH,
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/panel.js")
    def panel_js() -> FileResponse:
        return FileResponse(
            PANEL_JS_PATH,
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        status = runtime.snapshot()
        return {
            "status": "ok" if status["running"] else "degraded",
            "panel": "up",
            "runtime": {
                "state": status["state"],
                "running": status["running"],
                "ready": status["ready"],
            },
            "components": status.get("components", {}),
        }

    @app.get("/api/runtime/status", dependencies=[Depends(auth)])
    def runtime_status() -> dict[str, object]:
        return runtime.snapshot()

    @app.get("/api/dashboard", dependencies=[Depends(auth)])
    def dashboard() -> dict[str, object]:
        status = runtime.snapshot()
        sessions: list[dict[str, object]] = []
        chats: list[dict[str, object]] = []
        if runtime.is_running and runtime.userbot is not None:
            sessions = [
                _session_summary(peer_id, session)
                for peer_id, (session, _chain) in runtime.userbot._sessions.items()
            ]
            chats = _pending_takeover_requests(runtime.userbot)
        return {
            "runtime": status,
            "metrics": {
                "active_sessions": len(sessions),
                "observed_chats": len(chats),
                "likely_scams": sum(row["verdict"] == "likely_scam" for row in sessions),
                "hvis": sum(int(row["hvis"]) for row in sessions),
                "sandbox_runs": sum(int(row["sandbox"]) for row in sessions),
                "turns": sum(int(row["turns"]) for row in sessions),
            },
            "sessions": sorted(
                sessions,
                key=lambda row: float(row.get("last_message_ts") or row.get("started_ts") or 0),
                reverse=True,
            )[:6],
            "chats": chats[:6],
            "activity": (
                _audit_activity(audit, limit=8)
                if audit.status().get("enabled")
                else observations.events(limit=8)
            ),
        }

    @app.get("/api/activity", dependencies=[Depends(auth)])
    def activity(
        after: int = 0,
        limit: int = 200,
        scope: str = "important",
    ) -> dict[str, object]:
        if scope not in {"important", "all"}:
            raise HTTPException(status_code=400, detail="scope must be 'important' or 'all'")
        if audit.status().get("enabled"):
            return {
                "items": _audit_activity(
                    audit,
                    after=after,
                    limit=limit,
                    important_only=scope == "important",
                ),
                "scope": scope,
            }
        return {"items": observations.events(after=after, limit=limit), "scope": scope}

    @app.get("/api/audit", dependencies=[Depends(auth)])
    def audit_records(
        after: int = 0,
        limit: int = 200,
        peer_id: int | None = None,
        event_type: str = "",
    ) -> dict[str, object]:
        return {
            "status": audit.status(),
            "items": audit.list(
                after=after,
                limit=limit,
                peer_id=peer_id,
                event_type=event_type,
            ),
        }

    @app.get("/api/audit/status", dependencies=[Depends(auth)])
    def audit_status() -> dict[str, Any]:
        return audit.status()

    @app.get("/api/logs", dependencies=[Depends(auth)])
    def logs(after: int = 0, limit: int = 200) -> dict[str, object]:
        return {"items": observations.logs(after=after, limit=limit)}

    @app.get("/api/evidence", dependencies=[Depends(auth)])
    def evidence_index() -> list[dict[str, object]]:
        evidence_root = project_root / "evidence"
        if not evidence_root.is_dir():
            return []
        rows = []
        for path in evidence_root.glob("bundle_*.pdf"):
            parsed = parse_bundle_name(path.name)
            if parsed is None:
                continue
            peer_id, bundle_id = parsed
            data = path.read_bytes()
            signature = Path(str(path) + ".sig")
            stat = path.stat()
            rows.append(
                {
                    "peer_id": peer_id,
                    "bundle_id": bundle_id,
                    "filename": path.name,
                    "created_ts": stat.st_mtime,
                    "size": stat.st_size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "signature_present": signature.is_file(),
                    "signature_size": signature.stat().st_size if signature.is_file() else 0,
                    "download_url": f"/api/evidence/{path.name}",
                }
            )
        return sorted(rows, key=lambda row: float(row["created_ts"]), reverse=True)

    @app.get("/api/history", dependencies=[Depends(auth)])
    def takeover_history() -> list[dict[str, Any]]:
        return history.list()

    @app.get("/api/history/{history_id}", dependencies=[Depends(auth)])
    def takeover_history_detail(history_id: str) -> dict[str, Any]:
        record = history.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="takeover history not found")
        return _history_detail(record)

    @app.get("/api/history/{history_id}/analyses", dependencies=[Depends(auth)])
    def takeover_analysis_runs(history_id: str) -> list[dict[str, Any]]:
        record = history.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="takeover history not found")
        embedded = _embedded_analysis(record)
        return ([embedded] if embedded else []) + analysis_runs.list(str(record["id"]))

    def enrich_case_matches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = []
        for row in rows:
            related = case_intelligence.get(str(row.get("related_case_id") or "")) or {}
            enriched.append(
                {
                    **row,
                    "related_history_id": related.get("history_id") or row.get("related_case_id"),
                    "peer_id": related.get("peer_id"),
                    "verdict": related.get("verdict"),
                    "case_score": related.get("score"),
                    "created_ts": related.get("created_ts"),
                    "methods": related.get("methods") or [],
                }
            )
        return enriched

    @app.get("/api/history/{history_id}/related", dependencies=[Depends(auth)])
    def related_takeover_cases(history_id: str) -> list[dict[str, Any]]:
        record = history.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="takeover history not found")
        return enrich_case_matches(case_intelligence.related(str(record["id"])))

    @app.get(
        "/api/history/{history_id}/analyses/{run_id}",
        dependencies=[Depends(auth)],
    )
    def takeover_analysis_detail(history_id: str, run_id: str) -> dict[str, Any]:
        record = history.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="takeover history not found")
        canonical_id = str(record["id"])
        embedded = _embedded_analysis(record)
        if embedded and embedded["id"] == run_id:
            return _history_with_analysis(record, embedded)
        analysis = analysis_runs.get(canonical_id, run_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="analysis run not found")
        return _history_with_analysis(record, analysis)

    @app.post(
        "/api/history/{history_id}/reanalyze",
        dependencies=[Depends(auth)],
        status_code=202,
    )
    def reanalyze_takeover(history_id: str) -> dict[str, Any]:
        record = history.get(history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="takeover history not found")
        try:
            current_engine, _current_userbot, current_settings = live()
        except HTTPException as exc:
            raise HTTPException(status_code=409, detail="runtime must be ready") from exc
        return reanalysis.submit(record, current_engine, current_settings)

    @app.get("/api/reanalysis/{job_id}", dependencies=[Depends(auth)])
    def reanalysis_status(job_id: str) -> dict[str, Any]:
        job = reanalysis.status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="reanalysis job not found")
        return job

    @app.get("/api/media/{session_id}/{message_id}")
    def takeover_media(
        session_id: str,
        message_id: int,
        token: str = "",
        x_hive_token: str = Header(default=""),
    ) -> FileResponse:
        if not authorised(x_hive_token or token):
            raise HTTPException(status_code=401, detail="unauthorised")
        if not _SESSION_ID.fullmatch(session_id):
            raise HTTPException(status_code=404, detail="media not found")
        session_directory = (media_root / session_id).resolve()
        if session_directory.parent != media_root or not session_directory.is_dir():
            raise HTTPException(status_code=404, detail="media not found")
        matches = sorted(session_directory.glob(f"{message_id}_*"))
        path = next((candidate for candidate in matches if candidate.is_file()), None)
        if path is None:
            raise HTTPException(status_code=404, detail="media not found")
        download_name = path.name.split("_", 1)[1] if "_" in path.name else path.name
        media_type = mimetypes.guess_type(download_name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            filename=download_name,
            content_disposition_type="inline" if media_type.startswith("image/") else "attachment",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @app.get("/api/evidence/{filename}")
    def evidence_file(
        filename: str,
        token: str = "",
        x_hive_token: str = Header(default=""),
    ):
        if not authorised(x_hive_token or token):
            raise HTTPException(status_code=401, detail="unauthorised")
        if parse_bundle_name(filename) is None:
            raise HTTPException(status_code=404, detail="no sealed bundle")
        path = project_root / "evidence" / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no sealed bundle")
        return FileResponse(path, media_type="application/pdf", filename=filename)

    @app.get("/api/panel/session")
    def panel_session() -> JSONResponse:
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

    @app.get("/api/models/status", dependencies=[Depends(auth)])
    def model_status() -> dict[str, object]:
        current = runtime.settings if runtime.settings is not None else load_settings()
        component = runtime.snapshot().get("components", {}).get("llm", {})
        return {
            "endpoint": current.llm_base_url,
            "configured": bool(current.llm_api_key),
            "models": {
                "cheap": current.llm_model_cheap,
                "strong": current.llm_model_strong,
                "light": current.llm_model_light,
                "vision": current.vision_model,
            },
            "component": component,
            "restart_required": getattr(runtime, "restart_required", False),
        }

    @app.post("/api/models/probe", dependencies=[Depends(auth)])
    async def model_probe() -> dict[str, object]:
        current = load_settings()
        if not current.llm_api_key:
            raise HTTPException(status_code=409, detail="LLM API key is not configured")
        try:
            detail = await asyncio.to_thread(probe_llm, current)
        except Exception as exc:
            observations.event("model", "Model endpoint check failed", str(exc), severity="error")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        observations.event("model", "Model endpoint checked", detail, severity="success")
        return {"ok": True, "detail": detail}

    @app.get("/api/telegram/status", dependencies=[Depends(auth)])
    def telegram_status() -> dict[str, object]:
        current = runtime.settings if runtime.settings is not None else load_settings()
        snapshot = runtime.snapshot()
        phone = current.tg_phone
        masked_phone = (
            f"{phone[:3]}••••{phone[-3:]}" if len(phone) > 7 else ("configured" if phone else "")
        )
        return {
            "running": runtime.is_running,
            "account": {
                "configured": bool(current.tg_api_id and current.tg_api_hash and current.tg_phone),
                "phone": masked_phone,
                "component": snapshot.get("components", {}).get("userbot", {}),
            },
            "control_bot": {
                "configured": bool(current.control_bot_token and current.operator_id > 0),
                "operator_id": current.operator_id or None,
                "operator_name": getattr(current, "operator_name", "") or None,
                "component": snapshot.get("components", {}).get("control_bot", {}),
            },
            "observed_chats": (
                len(_pending_takeover_requests(runtime.userbot))
                if runtime.is_running and runtime.userbot is not None
                else 0
            ),
        }

    @app.post("/api/runtime/start", dependencies=[Depends(auth)])
    async def runtime_start() -> dict[str, object]:
        try:
            result = await runtime.start()
            observations.event("runtime", "Agent started", severity="success")
            return result
        except RuntimeNotReadyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "setup_incomplete", "missing": exc.missing},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/runtime/restart", dependencies=[Depends(auth)])
    async def runtime_restart(payload: Annotated[dict, Body()]) -> dict[str, object]:
        try:
            forced = bool(payload.get("force", False))
            result = await runtime.restart(force=forced)
            observations.event(
                "runtime",
                "Agent restarted",
                "Active sessions were discarded" if forced else "",
                severity="warning" if forced else "success",
            )
            return result
        except ActiveSessionsError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "active_sessions", "count": exc.count},
            ) from exc
        except RuntimeNotReadyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "setup_incomplete", "missing": exc.missing},
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/runtime/stop", dependencies=[Depends(auth)])
    async def runtime_stop(payload: Annotated[dict, Body()]) -> dict[str, object]:
        try:
            forced = bool(payload.get("force", False))
            result = await runtime.stop(force=forced)
            observations.event(
                "runtime",
                "Agent stopped",
                "Active sessions were discarded" if forced else "",
                severity="warning" if forced else "info",
            )
            return result
        except ActiveSessionsError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "active_sessions", "count": exc.count},
            ) from exc

    def live() -> tuple[Any, Any, Any]:
        if not runtime.is_running or runtime.engine is None or runtime.userbot is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "agent_not_running", "state": runtime.snapshot()["state"]},
            )
        return runtime.engine, runtime.userbot, runtime.settings

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def list_sessions() -> list[dict]:
        _engine, current_userbot, _settings = live()
        return [
            _session_summary(peer_id, session)
            for peer_id, (session, _chain) in current_userbot._sessions.items()
        ]

    @app.get("/api/chats", dependencies=[Depends(auth)])
    def list_chats() -> list[dict[str, object]]:
        _engine, current_userbot, _settings = live()
        return _pending_takeover_requests(current_userbot)

    @app.get("/api/sessions/{peer_id}", dependencies=[Depends(auth)])
    def get_session(peer_id: int) -> dict:
        _engine, current_userbot, _settings = live()
        entry = current_userbot._sessions.get(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        detail = _session_detail(peer_id, entry[0])
        detail["related_cases"] = enrich_case_matches(entry[0].related_cases)
        return detail

    @app.post("/api/takeover", dependencies=[Depends(auth)])
    def takeover(payload: Annotated[dict, Body()]) -> dict:
        _engine, current_userbot, current_settings = live()
        peer_id = payload.get("peer_id")
        persona = payload.get("persona") or current_settings.default_persona
        if not isinstance(peer_id, int):
            raise HTTPException(status_code=400, detail="peer_id must be an integer")
        if persona not in VALID_PERSONAS:
            raise HTTPException(status_code=400, detail=f"unknown persona: {persona}")
        current_userbot.begin_takeover(peer_id, persona)
        observations.event(
            "takeover",
            "Takeover started",
            f"Persona: {persona}",
            peer_id=peer_id,
            severity="success",
        )
        return {"ok": True, "peer_id": peer_id, "persona": persona}

    @app.post("/api/sessions/{peer_id}/persona", dependencies=[Depends(auth)])
    def set_persona(peer_id: int, payload: Annotated[dict, Body()]) -> dict:
        _engine, current_userbot, _settings = live()
        entry = current_userbot._sessions.get(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        persona = payload.get("persona")
        if persona not in VALID_PERSONAS:
            raise HTTPException(status_code=400, detail=f"unknown persona: {persona}")
        entry[0].persona = persona
        observations.event("takeover", "Persona changed", persona, peer_id=peer_id)
        return {"ok": True, "peer_id": peer_id, "persona": persona}

    @app.post("/api/sessions/{peer_id}/stop", dependencies=[Depends(auth)])
    def stop_session(peer_id: int) -> dict:
        live()
        coordinator = getattr(runtime, "takeovers", None) or fallback_takeovers
        if coordinator is None:
            raise HTTPException(status_code=409, detail="takeover service unavailable")
        try:
            sealed = coordinator.seal(peer_id)
        except TakeoverNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no active takeover") from exc
        except TakeoverBusyError as exc:
            raise HTTPException(status_code=409, detail="takeover is already being sealed") from exc
        except TakeoverSealError as exc:
            observations.event(
                "evidence",
                "Seal failed",
                "Takeover remains active and can be retried",
                peer_id=peer_id,
                severity="error",
            )
            log.exception("panel: seal failed; takeover retained peer=%s", peer_id)
            raise HTTPException(
                status_code=500,
                detail="seal failed; takeover remains active and can be retried",
            ) from exc
        observations.event(
            "evidence",
            "Session sealed",
            f"{len(sealed.session.hvis)} indicators; verdict {sealed.session.verdict}",
            peer_id=peer_id,
            severity="success",
        )
        log.info("panel: stopped + sealed peer=%s", peer_id)
        return {
            "ok": True,
            "summary": sealed.summary,
            "bundle": str(sealed.path),
            "history_id": sealed.history_record["id"],
            "download_url": f"/api/evidence/{sealed.path.name}",
        }

    @app.get("/api/sessions/{peer_id}/evidence")
    def evidence(peer_id: int, token: str = "", x_hive_token: str = Header(default="")):
        if not authorised(x_hive_token or token):
            raise HTTPException(status_code=401, detail="unauthorised")
        evidence_root = project_root / "evidence"
        matches = [
            path
            for path in evidence_root.glob(f"bundle_{peer_id}*.pdf")
            if (parsed := parse_bundle_name(path.name)) is not None and parsed[0] == peer_id
        ]
        if not matches:
            raise HTTPException(status_code=404, detail="no sealed bundle")
        path = max(matches, key=lambda candidate: candidate.stat().st_mtime_ns)
        return FileResponse(path, media_type="application/pdf", filename=f"evidence_{peer_id}.pdf")

    return app


async def _start_if_ready(runtime: Any) -> None:
    try:
        await runtime.start()
    except RuntimeNotReadyError:
        log.info("runtime: setup incomplete; panel remains available")
    except asyncio.CancelledError:
        raise
    except Exception:
        # The manager records the actionable error for /api/runtime/status.
        pass


def _host_without_port(value: str) -> str:
    host = value.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end >= 0 else host.strip("[]")
    return host.rsplit(":", 1)[0] if ":" in host else host


def _accepted_host(request_host: str, bound_host: str) -> bool:
    bound = bound_host.lower()
    if bound in {"0.0.0.0", "::"}:
        return True
    if bound in _LOOPBACK_HOSTS:
        return request_host in _LOOPBACK_HOSTS
    return hmac.compare_digest(request_host, bound)
