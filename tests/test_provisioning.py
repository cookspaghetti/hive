"""Offline tests for safe credential storage and Telethon login state."""

import asyncio
import os
from pathlib import Path

import pytest

from hive.provisioning.env_store import EnvStore
from hive.provisioning.telethon_login import TelethonLoginManager


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


def test_env_store_updates_atomically_and_preserves_content(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep me\nOTHER=value\nHIVE_TG_PHONE=old\n", encoding="utf-8")
    store = EnvStore(path)

    store.save({"HIVE_TG_PHONE": "+60123", "HIVE_LLM_API_KEY": "key with spaces"})

    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "OTHER=value" in text
    assert "HIVE_TG_PHONE=+60123" in text
    assert 'HIVE_LLM_API_KEY="key with spaces"' in text
    assert store.read()["HIVE_LLM_API_KEY"] == "key with spaces"


def test_env_store_updates_duplicate_keys_consistently(tmp_path):
    path = tmp_path / ".env"
    path.write_text("HIVE_TG_PHONE=first\nHIVE_TG_PHONE=second\n", encoding="utf-8")

    EnvStore(path).save({"HIVE_TG_PHONE": "+60123"})

    assert EnvStore(path).read()["HIVE_TG_PHONE"] == "+60123"
    assert path.read_text(encoding="utf-8").count("HIVE_TG_PHONE=+60123") == 2


def test_hf_token_is_exported_for_hugging_face_clients(tmp_path, monkeypatch):
    from hive.config import load_settings

    EnvStore(tmp_path / ".env").save({"HF_TOKEN": "hf_example_token"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    load_settings()

    assert os.environ["HF_TOKEN"] == "hf_example_token"


@pytest.mark.parametrize(
    ("key", "value"),
    [("PATH", "bad"), ("HIVE_UNKNOWN", "bad"), ("HIVE_PANEL_TOKEN", "a\nb")],
)
def test_env_store_rejects_unsafe_input(tmp_path, key, value):
    with pytest.raises(ValueError):
        EnvStore(tmp_path / ".env").save({key: value})


class FakeSession:
    def save(self):
        return "telegram-session"


class FakeSentCode:
    phone_code_hash = "code-hash"


class FakeClient:
    def __init__(self, needs_password=False):
        self.session = FakeSession()
        self.needs_password = needs_password
        self.disconnected = False
        self.sign_ins = []

    async def connect(self):
        return None

    async def send_code_request(self, phone):
        self.phone = phone
        return FakeSentCode()

    async def sign_in(self, **kwargs):
        self.sign_ins.append(kwargs)
        if self.needs_password and "code" in kwargs:

            class SessionPasswordNeededError(Exception):
                pass

            raise SessionPasswordNeededError()

    async def disconnect(self):
        self.disconnected = True


def test_telethon_login_saves_only_after_authorization(tmp_path, monkeypatch):
    store = EnvStore(tmp_path / ".env")
    client = FakeClient()
    saved = []
    monkeypatch.setattr(
        "hive.provisioning.telethon_login.save_session",
        lambda session, path, passphrase: saved.append((session, path, passphrase)),
    )
    manager = TelethonLoginManager(store, client_factory=lambda _id, _hash: client)

    started = _run(
        manager.start(
            api_id=123,
            api_hash="hash",
            phone="+60123",
            passphrase="long-passphrase",
            session_path=str(tmp_path / "secrets" / "user.session"),
            env_session_path="./secrets/user.session",
        )
    )
    assert store.read() == {}

    result = _run(manager.submit_code(started["attempt_id"], "12345"))
    assert result == {"state": "ready"}
    assert saved[0][0] == "telegram-session"
    assert store.read()["HIVE_TG_SESSION_PATH"] == "./secrets/user.session"
    assert client.disconnected


def test_telethon_login_supports_2fa(tmp_path, monkeypatch):
    store = EnvStore(tmp_path / ".env")
    client = FakeClient(needs_password=True)
    monkeypatch.setattr("hive.provisioning.telethon_login.save_session", lambda *_args: None)
    manager = TelethonLoginManager(store, client_factory=lambda _id, _hash: client)
    started = _run(
        manager.start(
            api_id=123,
            api_hash="hash",
            phone="+60123",
            passphrase="long-passphrase",
            session_path=str(tmp_path / "user.session"),
        )
    )

    assert _run(manager.submit_code(started["attempt_id"], "12345")) == {
        "state": "password_required"
    }
    assert _run(manager.submit_password(started["attempt_id"], "telegram-2fa")) == {
        "state": "ready"
    }
    assert client.sign_ins[-1] == {"password": "telegram-2fa"}


def test_telethon_login_requires_strong_local_passphrase(tmp_path):
    manager = TelethonLoginManager(EnvStore(tmp_path / ".env"), client_factory=lambda *_: None)

    with pytest.raises(ValueError, match="at least 12"):
        _run(
            manager.start(
                api_id=123,
                api_hash="hash",
                phone="+60123",
                passphrase="short",
                session_path=str(Path(tmp_path) / "user.session"),
            )
        )
