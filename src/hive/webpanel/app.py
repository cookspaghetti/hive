"""Durable localhost control panel for setup and live HIVE operations."""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from hive.agent.personas import PERSONAS
from hive.config import load_settings
from hive.logging_setup import get_logger
from hive.provisioning import EnvStore, TelethonLoginManager
from hive.runtime_manager import ActiveSessionsError, RuntimeNotReadyError
from hive.webpanel.assets import LOGO_PATH
from hive.webpanel.setup_app import panel_page, register_setup_routes

log = get_logger(__name__)
VALID_PERSONAS = set(PERSONAS)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def _session_summary(peer_id, session) -> dict:
    return {
        "peer_id": peer_id,
        "persona": session.persona,
        "phase": session.phase.value if hasattr(session.phase, "value") else str(session.phase),
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "hvis": len(session.hvis),
        "sandbox": len(session.sandbox_results),
    }


def _session_detail(peer_id, session) -> dict:
    detail = _session_summary(peer_id, session)
    detail["messages"] = [{"role": m.role, "text": m.text, "ts": m.ts} for m in session.messages]
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
    def index() -> str:
        return panel_page(token)

    @app.get("/logo.png")
    @app.get("/favicon.png")
    def logo() -> FileResponse:
        return FileResponse(LOGO_PATH, media_type="image/png")

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

    @app.post("/api/runtime/start", dependencies=[Depends(auth)])
    async def runtime_start() -> dict[str, object]:
        try:
            return await runtime.start()
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
            return await runtime.restart(force=bool(payload.get("force", False)))
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
            return await runtime.stop(force=bool(payload.get("force", False)))
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
