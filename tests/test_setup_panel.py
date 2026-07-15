"""Standalone setup panel API tests; no Telegram network calls."""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from hive.provisioning.env_store import EnvStore
from hive.webpanel.setup_app import create_setup_app

TOKEN = "local-setup-token"


@pytest.fixture(autouse=True)
def preserve_default_event_loop():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield
    asyncio.set_event_loop(loop)


class FakeLoginManager:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        return {"attempt_id": "attempt-1", "state": "code_required"}

    async def submit_code(self, attempt_id, code):
        self.calls.append(("code", attempt_id, code))
        return {"state": "password_required"}

    async def submit_password(self, attempt_id, password):
        self.calls.append(("password", attempt_id, password))
        return {"state": "ready"}

    async def cancel(self, attempt_id):
        self.calls.append(("cancel", attempt_id))

    async def close(self):
        self.closed = True


def _headers():
    return {"X-HIVE-Setup-Token": TOKEN}


def _client(tmp_path):
    manager = FakeLoginManager()
    store = EnvStore(tmp_path / ".env")
    app = create_setup_app(setup_token=TOKEN, root=tmp_path, env_store=store, login_manager=manager)
    return TestClient(app), store, manager


def test_setup_index_and_api_auth(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/").status_code == 200
    assert "HIVE Control Panel" in client.get("/").text
    assert client.get("/api/setup/status").status_code == 401


def test_setup_panel_serves_png_logo_and_favicon(tmp_path):
    client, _, _ = _client(tmp_path)

    page = client.get("/")
    logo = client.get("/logo.png")
    favicon = client.get("/favicon.png")

    assert '<img src="/logo.png"' in page.text
    assert logo.status_code == 200 and favicon.status_code == 200
    assert logo.headers["content-type"].startswith("image/png")
    assert favicon.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_config_and_status_do_not_return_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    client, store, _ = _client(tmp_path)
    response = client.put(
        "/api/setup/config",
        headers=_headers(),
        json={
            "HF_TOKEN": "hf-secret",
            "HIVE_LLM_API_KEY": "secret-key",
            "HIVE_PANEL_TOKEN": "panel-secret",
        },
    )
    assert response.status_code == 200
    status = client.get("/api/setup/status", headers=_headers())
    assert status.status_code == 200
    assert status.json()["checks"]["llm"] is True
    assert "secret-key" not in status.text and "panel-secret" not in status.text
    assert "hf-secret" not in status.text
    assert store.read()["HIVE_LLM_API_KEY"] == "secret-key"
    assert store.read()["HF_TOKEN"] == "hf-secret"
    assert os.environ["HF_TOKEN"] == "hf-secret"


def test_config_rejects_arbitrary_environment_keys(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.put("/api/setup/config", headers=_headers(), json={"PATH": "C:\\malicious"})
    assert response.status_code == 400


def test_bot_is_verified_before_credentials_are_saved(tmp_path, monkeypatch):
    client, store, _ = _client(tmp_path)

    async def fake_verify(token):
        assert token.startswith("123:")
        return {"id": 123, "username": "hive_control_bot"}

    monkeypatch.setattr("hive.webpanel.setup_app.verify_control_bot_token", fake_verify)
    token = "123:" + "a" * 32
    response = client.post(
        "/api/setup/bot/verify",
        headers=_headers(),
        json={"token": token, "operator_id": "456"},
    )
    assert response.status_code == 200
    assert response.json()["bot"]["username"] == "hive_control_bot"
    assert store.read()["HIVE_CONTROL_BOT_TOKEN"] == token
    assert store.read()["HIVE_OPERATOR_ID"] == "456"


def test_telethon_routes_drive_login_state(tmp_path):
    client, _, manager = _client(tmp_path)
    start = client.post(
        "/api/setup/telethon/start",
        headers=_headers(),
        json={
            "api_id": 123,
            "api_hash": "hash",
            "phone": "+60123",
            "passphrase": "long-passphrase",
            "session_path": "./secrets/user.session",
        },
    )
    assert start.json()["state"] == "code_required"
    args = manager.calls[0][1]
    assert args["session_path"] == str(tmp_path / "secrets" / "user.session")
    assert args["env_session_path"] == "./secrets/user.session"

    code = client.post(
        "/api/setup/telethon/code",
        headers=_headers(),
        json={"attempt_id": "attempt-1", "code": "12345"},
    )
    assert code.json()["state"] == "password_required"
    password = client.post(
        "/api/setup/telethon/password",
        headers=_headers(),
        json={"attempt_id": "attempt-1", "password": "2fa"},
    )
    assert password.json()["state"] == "ready"


def test_setup_rejects_credential_path_outside_project(tmp_path):
    client, _, _ = _client(tmp_path)
    response = client.post(
        "/api/setup/telethon/start",
        headers=_headers(),
        json={
            "api_id": 123,
            "api_hash": "hash",
            "phone": "+60123",
            "passphrase": "long-passphrase",
            "session_path": "../outside.session",
        },
    )
    assert response.status_code == 400
