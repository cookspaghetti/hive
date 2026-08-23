"""Managed Telegram runtime lifecycle tests, fully offline."""

import asyncio

import pytest

from hive.config import Settings
from hive.runtime_manager import (
    ActiveSessionsError,
    HiveRuntimeManager,
    RuntimeNotReadyError,
    probe_llm,
)


def _run(coro):
    try:
        original = asyncio.get_event_loop()
    except RuntimeError:
        original = asyncio.new_event_loop()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(original)


class FakeUserbot:
    instances = []

    def __init__(self, api_id, api_hash, session, engine, **kwargs):
        self.args = (api_id, api_hash, session, engine)
        self.options = kwargs
        self._sessions = {}
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeControl:
    instances = []

    def __init__(self, settings, engine, userbot, **kwargs):
        self.args = (settings, engine, userbot)
        self.options = kwargs
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


def _settings(tmp_path):
    return Settings(
        llm_api_key="llm-key",
        tg_api_id=123,
        tg_api_hash="api-hash",
        tg_phone="+60123",
        tg_session_path=str(tmp_path / "user.session"),
        session_passphrase="long-passphrase",
        control_bot_token="123:" + "a" * 32,
        operator_id=456,
        signing_key_path=str(tmp_path / "signing.pem"),
        use_case_similarity=False,
    )


def _manager(tmp_path, settings):
    return HiveRuntimeManager(
        root=tmp_path,
        settings_loader=lambda: settings,
        engine_builder=lambda _settings: object(),
        session_loader=lambda path, passphrase: f"session:{path}:{passphrase}",
        llm_probe=lambda _settings: "endpoint reachable; 4 model(s) advertised",
        qdrant_probe=lambda _settings: "ready at http://localhost:6333",
        userbot_factory=FakeUserbot,
        control_factory=FakeControl,
    )


def test_runtime_starts_and_stops_both_telegram_planes(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    manager = _manager(tmp_path, settings)

    async def scenario():
        started = await manager.start()
        userbot, control = manager.userbot, manager.control
        stopped = await manager.stop()
        return started, stopped, userbot, control

    started, stopped, userbot, control = _run(scenario())
    assert started["state"] == "running"
    assert (
        started["components"]["session_store"]["detail"]
        == "encrypted session decrypted into memory"
    )
    assert "session:" not in started["components"]["session_store"]["detail"]
    assert started["components"]["llm"]["state"] == "ready"
    assert started["components"]["qdrant"]["state"] == "disabled"
    assert started["components"]["engine"]["duration_s"] is not None
    assert stopped["state"] == "stopped"
    assert userbot.started and userbot.stopped
    assert control.started and control.stopped
    assert userbot.options["checkpoint_store"] is not None


def test_runtime_reports_incomplete_setup_without_starting(tmp_path):
    settings = _settings(tmp_path)
    manager = _manager(tmp_path, settings)

    with pytest.raises(RuntimeNotReadyError) as raised:
        _run(manager.start())

    assert set(raised.value.missing) == {"telethon", "signing_key"}
    assert manager.state == "stopped"


def test_runtime_probes_qdrant_for_case_similarity(tmp_path):
    settings = _settings(tmp_path)
    settings.use_case_similarity = True
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    calls = []
    manager = _manager(tmp_path, settings)
    manager._qdrant_probe = lambda current: calls.append(current.qdrant_url) or "ready"

    result = _run(manager.start())

    assert calls == [settings.qdrant_url]
    assert result["components"]["qdrant"]["state"] == "ready"
    assert (
        result["components"]["case_intelligence"]["detail"]
        == "PostgreSQL exact edges + Qdrant scam-pattern candidates"
    )


def test_runtime_records_failed_llm_probe(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    manager = _manager(tmp_path, settings)

    def fail_probe(_settings):
        raise RuntimeError("LLM authentication failed")

    manager._llm_probe = fail_probe

    with pytest.raises(RuntimeError, match="LLM authentication failed"):
        _run(manager.start())

    assert manager.state == "error"
    assert manager.snapshot()["components"]["llm"]["state"] == "error"


def test_llm_probe_accepts_cloud_aliases_and_verifies_generation(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.vision_model = "qwen3.5:cloud"

    class ModelResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "data": [
                    {"id": "glm-5.1"},
                    {"id": "glm-5.2"},
                    {"id": "qwen3.5:397b"},
                ]
            }

    calls = []

    class FakeBackend:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def chat(self, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "READY"},
                        "finish_reason": "stop",
                    }
                ]
            }

        def close(self):
            calls.append("closed")

    monkeypatch.setattr("hive.runtime_manager.httpx.get", lambda *args, **kwargs: ModelResponse())
    monkeypatch.setattr("hive.runtime_manager.OllamaBackend", FakeBackend)

    detail = probe_llm(settings)

    assert "generation verified with glm-5.1:cloud" in detail
    assert calls[-1] == "closed"


def test_llm_probe_rejects_unadvertised_configured_model(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.llm_model_strong = "missing-model:cloud"

    class ModelResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"data": [{"id": "glm-5.1"}, {"id": "qwen3.5"}]}

    monkeypatch.setattr("hive.runtime_manager.httpx.get", lambda *args, **kwargs: ModelResponse())

    with pytest.raises(RuntimeError, match="missing-model:cloud"):
        probe_llm(settings)


def test_restart_requires_force_when_takeovers_are_active(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    manager = _manager(tmp_path, settings)

    async def scenario():
        await manager.start()
        manager.userbot._sessions[99] = (object(), object())
        with pytest.raises(ActiveSessionsError):
            await manager.restart()
        assert manager.is_running
        return await manager.restart(force=True)

    result = _run(scenario())
    assert result["state"] == "running"


def test_config_change_marks_running_runtime_for_restart(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    manager = _manager(tmp_path, settings)

    async def scenario():
        await manager.start()
        manager.mark_restart_required()
        return manager.snapshot()

    status = _run(scenario())
    assert status["state"] == "restart_required"
    assert status["running"] is True


def test_userbot_disconnects_when_control_bot_stop_fails(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "user.session").write_bytes(b"encrypted")
    (tmp_path / "signing.pem").write_text("key", encoding="utf-8")
    manager = _manager(tmp_path, settings)

    async def scenario():
        await manager.start()
        userbot = manager.userbot

        async def fail_stop():
            raise RuntimeError("control shutdown failed")

        manager.control.stop = fail_stop
        with pytest.raises(RuntimeError, match="control shutdown failed"):
            await manager.stop()
        return userbot

    userbot = _run(scenario())
    assert userbot.stopped is True
    assert manager.state == "stopped"
