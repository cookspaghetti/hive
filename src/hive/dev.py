"""Hot-reloading, panel-only development entry point."""

from __future__ import annotations

import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from hive.config import load_settings
from hive.logging_setup import configure_logging, get_logger
from hive.runtime_manager import HiveRuntimeManager
from hive.webpanel import create_app

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PANEL_HOST = "127.0.0.1"


def create_dev_app() -> FastAPI:
    """Build the control panel without automatically starting Telegram."""
    runtime = HiveRuntimeManager(root=PROJECT_ROOT)
    return create_app(
        runtime_manager=runtime,
        root=PROJECT_ROOT,
        session_token=secrets.token_urlsafe(32),
        bound_host=PANEL_HOST,
        auto_start=False,
    )


def run_panel() -> None:
    """Run one panel process; ``watchfiles`` restarts it after source changes."""
    settings = load_settings()
    configure_logging(settings.log_level)
    log.info(
        "[startup][panel] DEVELOPMENT url=http://%s:%d telegram_autostart=disabled",
        PANEL_HOST,
        settings.panel_port,
    )
    uvicorn.run(
        create_dev_app(),
        host=PANEL_HOST,
        port=settings.panel_port,
        log_level="warning",
    )


if __name__ == "__main__":
    run_panel()
