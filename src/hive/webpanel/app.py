"""Durable localhost control panel for setup and live HIVE operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from hive.agent.personas import PERSONAS
from hive.config import load_settings
from hive.logging_setup import get_logger
from hive.provisioning import EnvStore, TelethonLoginManager
from hive.runtime_manager import ActiveSessionsError, RuntimeNotReadyError, probe_llm
from hive.webpanel.assets import LOGO_PATH, PANEL_CSS_PATH, PANEL_JS_PATH
from hive.webpanel.observability import get_observation_hub
from hive.webpanel.setup_app import panel_page, register_setup_routes

log = get_logger(__name__)
VALID_PERSONAS = set(PERSONAS)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def _session_summary(peer_id, session) -> dict:
    started = session.started_ts
    return {
        "peer_id": peer_id,
        "persona": session.persona,
        "phase": session.phase.value if hasattr(session.phase, "value") else str(session.phase),
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "hvis": len(session.hvis),
        "sandbox": len(session.sandbox_results),
        "started_ts": started,
        "duration_s": max(0, round(time.time() - started)) if started else None,
        "last_message_ts": session.messages[-1].ts if session.messages else None,
    }


def _session_detail(peer_id, session) -> dict:
    detail = _session_summary(peer_id, session)
    detail["messages"] = [
        {
            "role": m.role,
            "text": m.text,
            "ts": m.ts,
            "msg_id": m.msg_id,
            "media_kind": m.media_kind,
        }
        for m in session.messages
    ]
    detail["hvi_items"] = [
        {"kind": h.kind, "value": h.value, "confidence": round(h.confidence, 2)}
        for h in session.hvis
    ]
    detail["sandbox_results"] = session.sandbox_results
    detail["signal_trail"] = session.signal_trail[-20:]
    return detail


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
    telethon_login = login_manager or TelethonLoginManager(store)
    observations = get_observation_hub()
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
            chats = runtime.userbot.list_observed_chats()
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
            "activity": observations.events(limit=8),
        }

    @app.get("/api/activity", dependencies=[Depends(auth)])
    def activity(after: int = 0, limit: int = 200) -> dict[str, object]:
        return {"items": observations.events(after=after, limit=limit)}

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
            try:
                peer_id = int(path.stem.removeprefix("bundle_"))
            except ValueError:
                continue
            data = path.read_bytes()
            signature = Path(str(path) + ".sig")
            stat = path.stat()
            rows.append(
                {
                    "peer_id": peer_id,
                    "filename": path.name,
                    "created_ts": stat.st_mtime,
                    "size": stat.st_size,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "signature_present": signature.is_file(),
                    "signature_size": signature.stat().st_size if signature.is_file() else 0,
                    "download_url": f"/api/sessions/{peer_id}/evidence",
                }
            )
        return sorted(rows, key=lambda row: float(row["created_ts"]), reverse=True)

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
                "component": snapshot.get("components", {}).get("control_bot", {}),
            },
            "observed_chats": (
                len(runtime.userbot.list_observed_chats())
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
        return current_userbot.list_observed_chats()

    @app.get("/api/sessions/{peer_id}", dependencies=[Depends(auth)])
    def get_session(peer_id: int) -> dict:
        _engine, current_userbot, _settings = live()
        entry = current_userbot._sessions.get(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        return _session_detail(peer_id, entry[0])

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
        current_engine, current_userbot, current_settings = live()
        entry = current_userbot.end_takeover(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        session, chain = entry
        os.makedirs(project_root / "evidence", exist_ok=True)
        out_path = project_root / "evidence" / f"bundle_{peer_id}.pdf"
        current_engine.close_session(
            session, chain, str(out_path), current_settings.signing_key_path
        )
        observations.event(
            "evidence",
            "Session sealed",
            f"{len(session.hvis)} indicators; verdict {session.verdict}",
            peer_id=peer_id,
            severity="success",
        )
        log.info("panel: stopped + sealed peer=%s", peer_id)
        return {"ok": True, "summary": current_engine.summary(session), "bundle": str(out_path)}

    @app.get("/api/sessions/{peer_id}/evidence")
    def evidence(peer_id: int, token: str = "", x_hive_token: str = Header(default="")):
        if not authorised(x_hive_token or token):
            raise HTTPException(status_code=401, detail="unauthorised")
        path = project_root / "evidence" / f"bundle_{peer_id}.pdf"
        if not path.exists():
            raise HTTPException(status_code=404, detail="no sealed bundle")
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
