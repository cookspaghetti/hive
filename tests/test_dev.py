"""Focused tests for the panel-only development entry point."""

from pathlib import Path
from types import SimpleNamespace

from hive import dev

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dev_task_uses_watchfiles_for_the_source_tree():
    taskfile = (PROJECT_ROOT / "Taskfile.yml").read_text(encoding="utf-8")

    assert "uv run watchfiles hive.dev.run_panel src" in taskfile


def test_create_dev_app_disables_telegram_autostart(monkeypatch):
    runtime = object()
    captured = {}

    monkeypatch.setattr(dev, "HiveRuntimeManager", lambda **kwargs: runtime)

    def fake_create_app(**kwargs):
        captured.update(kwargs)
        return "panel-app"

    monkeypatch.setattr(dev, "create_app", fake_create_app)

    assert dev.create_dev_app() == "panel-app"
    assert captured["runtime_manager"] is runtime
    assert captured["auto_start"] is False
    assert captured["bound_host"] == "127.0.0.1"


def test_run_panel_binds_only_to_localhost(monkeypatch):
    settings = SimpleNamespace(panel_port=9321, log_level="DEBUG")
    captured = {}

    monkeypatch.setattr(dev, "load_settings", lambda: settings)
    monkeypatch.setattr(dev, "configure_logging", lambda level: captured.setdefault("log", level))
    monkeypatch.setattr(dev, "create_dev_app", lambda: "panel-app")
    monkeypatch.setattr(
        dev.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(app=app, **kwargs),
    )

    dev.run_panel()

    assert captured == {
        "log": "DEBUG",
        "app": "panel-app",
        "host": "127.0.0.1",
        "port": 9321,
        "log_level": "warning",
    }
