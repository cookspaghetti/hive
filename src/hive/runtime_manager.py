"""Managed lifecycle for the Telegram runtime behind the durable web panel."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from hive.config import Settings, load_settings
from hive.logging_setup import get_logger
from hive.runtime import build_engine
from hive.security.session_store import load_session
from hive.transports.control_bot import ControlBot
from hive.transports.userbot import UserbotTransport

log = get_logger(__name__)

_COMPONENTS = (
    "configuration",
    "session_store",
    "llm",
    "qdrant",
    "memory",
    "engine",
    "userbot",
    "control_bot",
)


def probe_llm(settings: Settings) -> str:
    """Validate LLM authentication and enumerate models without consuming tokens."""
    url = settings.llm_base_url.rstrip("/") + "/models"
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    available = {
        model_id
        for row in rows
        if (model_id := str(row.get("id", "") if isinstance(row, dict) else row))
    }
    configured = {
        settings.llm_model_cheap,
        settings.llm_model_strong,
        settings.llm_model_light,
        settings.vision_model,
    }
    missing = sorted(model for model in configured if model not in available)
    detail = f"endpoint reachable; {len(available)} model(s) advertised"
    if missing:
        detail += f"; configured model(s) not advertised: {', '.join(missing)}"
    return detail


def probe_qdrant(settings: Settings) -> str:
    """Validate the configured Qdrant readiness endpoint."""
    url = settings.qdrant_url.rstrip("/") + "/readyz"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return f"ready at {settings.qdrant_url}"


class RuntimeNotReadyError(RuntimeError):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"setup incomplete: {', '.join(missing)}")


class ActiveSessionsError(RuntimeError):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"{count} active takeover(s) must be stopped first")


class HiveRuntimeManager:
    """Start, stop, and reload the engine plus both Telegram transports."""

    def __init__(
        self,
        *,
        root: str | Path = ".",
        settings_loader: Callable[[], Settings] = load_settings,
        engine_builder: Callable[[Settings], Any] = build_engine,
        session_loader: Callable[[str, str], str] = load_session,
        llm_probe: Callable[[Settings], str] = probe_llm,
        qdrant_probe: Callable[[Settings], str] = probe_qdrant,
        userbot_factory: Callable[..., Any] = UserbotTransport,
        control_factory: Callable[..., Any] = ControlBot,
    ) -> None:
        self.root = Path(root).resolve()
        self._settings_loader = settings_loader
        self._engine_builder = engine_builder
        self._session_loader = session_loader
        self._llm_probe = llm_probe
        self._qdrant_probe = qdrant_probe
        self._userbot_factory = userbot_factory
        self._control_factory = control_factory
        self._lock = asyncio.Lock()

        self.state = "stopped"
        self.error = ""
        self.restart_required = False
        self.settings: Settings | None = None
        self.engine: Any = None
        self.userbot: Any = None
        self.control: Any = None
        self.components = {
            name: {"state": "pending", "detail": "", "duration_s": None}
            for name in _COMPONENTS
        }

    def readiness(self, settings: Settings | None = None) -> dict[str, bool]:
        current = settings or self._settings_loader()
        session_path = self._resolve(current.tg_session_path)
        signing_path = self._resolve(current.signing_key_path)
        return {
            "llm": bool(current.llm_api_key),
            "control_bot": bool(current.control_bot_token and current.operator_id > 0),
            "telethon": bool(
                current.tg_api_id
                and current.tg_api_hash
                and current.tg_phone
                and current.session_passphrase
                and session_path.is_file()
            ),
            "signing_key": signing_path.is_file(),
        }

    def snapshot(self) -> dict[str, object]:
        try:
            checks = self.readiness()
            ready = all(checks.values())
        except Exception as exc:
            checks = {}
            ready = False
            self.components["configuration"] = {
                "state": "error",
                "detail": str(exc),
                "duration_s": None,
            }
        sessions = len(self.userbot._sessions) if self.userbot is not None else 0
        visible_state = (
            "restart_required" if self.restart_required and self.is_running else self.state
        )
        return {
            "state": visible_state,
            "running": self.is_running,
            "restart_required": self.restart_required,
            "active_sessions": sessions,
            "error": self.error,
            "ready": ready,
            "checks": checks,
            "components": {name: dict(status) for name, status in self.components.items()},
        }

    @property
    def is_running(self) -> bool:
        return self.state == "running" and self.userbot is not None

    def mark_restart_required(self) -> None:
        if self.is_running:
            self.restart_required = True

    async def start(self) -> dict[str, object]:
        async with self._lock:
            if self.is_running:
                return self.snapshot()
            self.state = "starting"
            self.error = ""
            self.components = {
                name: {"state": "pending", "detail": "", "duration_s": None}
                for name in _COMPONENTS
            }
            startup_started = time.perf_counter()
            self._set_component("configuration", "initializing", "loading settings")
            settings = self._settings_loader()
            checks = self.readiness(settings)
            missing = [name for name, ready in checks.items() if not ready]
            if missing:
                self.state = "stopped"
                self._set_component(
                    "configuration", "blocked", f"missing: {', '.join(missing)}"
                )
                raise RuntimeNotReadyError(missing)
            self._set_component(
                "configuration",
                "ready",
                (
                    f"models={settings.llm_model_cheap}/{settings.llm_model_strong}; "
                    f"semantic_memory={settings.use_semantic_memory}"
                ),
                startup_started,
            )

            try:
                session_path = str(self._resolve(settings.tg_session_path))
                session_str = await self._sync_stage(
                    "session_store",
                    "decrypting encrypted Telethon session",
                    self._session_loader,
                    session_path,
                    settings.session_passphrase,
                    success_detail="encrypted session decrypted into memory",
                )
                await self._sync_stage(
                    "llm", "probing endpoint and configured models", self._llm_probe, settings
                )
                if settings.use_semantic_memory:
                    await self._sync_stage(
                        "qdrant", "probing readiness endpoint", self._qdrant_probe, settings
                    )
                    self._set_component("memory", "ready", "semantic mem0 + Qdrant")
                else:
                    self._set_component("qdrant", "disabled", "semantic memory is disabled")
                    self._set_component("memory", "ready", "offline keyword memory")
                engine = await self._sync_stage(
                    "engine",
                    "loading GLiNER, LLM client, sandbox runner, and memory factory",
                    self._engine_builder,
                    settings,
                    success_detail="engine dependencies initialized",
                )
                userbot = self._userbot_factory(
                    settings.tg_api_id, settings.tg_api_hash, session_str, engine
                )
                control = self._control_factory(settings, engine, userbot)
                await self._async_stage(
                    "userbot",
                    "connecting Telethon data plane",
                    userbot.start,
                    success_detail="Telethon data plane connected",
                )
                try:
                    await self._async_stage(
                        "control_bot",
                        "starting Bot API polling",
                        control.start,
                        success_detail="Bot API polling active",
                    )
                except Exception:
                    await userbot.stop()
                    raise
            except Exception as exc:
                self.state = "error"
                self.error = str(exc)
                log.exception("runtime: start failed")
                raise

            self.settings = settings
            self.engine = engine
            self.userbot = userbot
            self.control = control
            self.state = "running"
            self.restart_required = False
            log.info(
                "[startup][runtime] READY all components initialized in %.2fs",
                time.perf_counter() - startup_started,
            )
            return self.snapshot()

    async def stop(self, *, force: bool = False) -> dict[str, object]:
        async with self._lock:
            if not self.is_running:
                self.state = "stopped"
                return self.snapshot()
            active = len(self.userbot._sessions)
            if active and not force:
                raise ActiveSessionsError(active)
            self.state = "stopping"
            stop_error: Exception | None = None
            try:
                if self.control is not None:
                    await self.control.stop()
            except Exception as exc:
                stop_error = exc
                log.exception("runtime: control bot stop failed")
            finally:
                try:
                    if self.userbot is not None:
                        await self.userbot.stop()
                except Exception as exc:
                    stop_error = stop_error or exc
                    log.exception("runtime: userbot stop failed")
                finally:
                    self.settings = None
                    self.engine = None
                    self.userbot = None
                    self.control = None
                    self.state = "stopped"
                    self._set_component("control_bot", "stopped", "Bot API polling stopped")
                    self._set_component("userbot", "stopped", "Telethon disconnected")
                    self._set_component("engine", "stopped", "runtime released")
                    self._set_component("session_store", "locked", "encrypted at rest")
            log.info("runtime: stopped")
            if stop_error is not None:
                raise stop_error
            return self.snapshot()

    async def restart(self, *, force: bool = False) -> dict[str, object]:
        await self.stop(force=force)
        return await self.start()

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    async def _sync_stage(
        self,
        component: str,
        starting_detail: str,
        function: Callable[..., Any],
        *args: Any,
        success_detail: str | None = None,
    ) -> Any:
        started = time.perf_counter()
        self._set_component(component, "initializing", starting_detail)
        try:
            result = await asyncio.to_thread(function, *args)
        except Exception as exc:
            self._set_component(component, "error", str(exc), started)
            raise
        detail = success_detail or (str(result) if result is not None else "initialized")
        self._set_component(component, "ready", detail, started)
        return result

    async def _async_stage(
        self,
        component: str,
        starting_detail: str,
        function: Callable[..., Any],
        *,
        success_detail: str,
    ) -> Any:
        started = time.perf_counter()
        self._set_component(component, "initializing", starting_detail)
        try:
            result = await function()
        except Exception as exc:
            self._set_component(component, "error", str(exc), started)
            raise
        self._set_component(component, "ready", success_detail, started)
        return result

    def _set_component(
        self,
        component: str,
        state: str,
        detail: str,
        started: float | None = None,
    ) -> None:
        duration = round(time.perf_counter() - started, 3) if started is not None else None
        self.components[component] = {
            "state": state,
            "detail": detail,
            "duration_s": duration,
        }
        suffix = f" ({duration:.2f}s)" if duration is not None else ""
        level = logging.ERROR if state == "error" else logging.INFO
        log.log(level, "[startup][%s] %s %s%s", component, state.upper(), detail, suffix)
