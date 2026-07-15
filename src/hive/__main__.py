"""HIVE entry point: durable web panel with a managed Telegram runtime."""

from __future__ import annotations

import secrets

import uvicorn

from hive.config import load_settings
from hive.logging_setup import configure_logging, get_logger
from hive.runtime_manager import HiveRuntimeManager
from hive.webpanel import create_app

log = get_logger(__name__)


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    log.info("[startup][application] INITIALIZING HIVE control plane")
    log.info(
        "[startup][configuration] READY log_level=%s panel_bind=%s:%d",
        settings.log_level,
        settings.panel_host,
        settings.panel_port,
    )
    if settings.panel_host not in {"127.0.0.1", "localhost", "::1"}:
        if not settings.panel_allow_non_loopback:
            raise SystemExit(
                "Refusing non-loopback panel bind. Set "
                "HIVE_PANEL_ALLOW_NON_LOOPBACK=true only behind a loopback-only port mapping."
            )
        log.warning(
            "[startup][panel] WARNING non-loopback bind enabled; "
            "enforce external network controls"
        )
    session_token = secrets.token_urlsafe(32)
    log.info("[startup][security] READY ephemeral panel token generated")
    runtime = HiveRuntimeManager()
    app = create_app(
        runtime_manager=runtime,
        session_token=session_token,
        bound_host=settings.panel_host,
        auto_start=True,
    )
    display_host = "127.0.0.1" if settings.panel_host == "0.0.0.0" else settings.panel_host
    log.info(
        "[startup][panel] READY url=http://%s:%d health=http://%s:%d/health",
        display_host,
        settings.panel_port,
        display_host,
        settings.panel_port,
    )
    log.info(
        "[startup][runtime] SCHEDULED Telegram starts when the setup checklist is complete"
    )
    uvicorn.run(app, host=settings.panel_host, port=settings.panel_port, log_level="warning")


if __name__ == "__main__":
    main()
