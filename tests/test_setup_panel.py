"""Standalone setup panel API tests; no Telegram network calls."""

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.provisioning.env_store import EnvStore
from hive.vault.signer import sign_bytes, verify_signature
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


def test_keyless_intelligence_baseline_is_ready_without_optional_keys(tmp_path):
    client, _, _ = _client(tmp_path)

    status = client.get("/api/setup/status", headers=_headers()).json()

    assert status["checks"]["intelligence"] is True
    assert status["threat_intelligence"] == {
        "semak_mule": True,
        "virus_total": False,
        "abuse_ipdb": False,
        "rdap": True,
    }


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
            "HIVE_VIRUSTOTAL_API_KEY": "vt-secret",
            "HIVE_ABUSEIPDB_API_KEY": "abuse-secret",
        },
    )
    assert response.status_code == 200
    status = client.get("/api/setup/status", headers=_headers())
    assert status.status_code == 200
    assert status.json()["checks"]["llm"] is True
    assert "secret-key" not in status.text and "panel-secret" not in status.text
    assert "hf-secret" not in status.text
    assert "vt-secret" not in status.text
    assert "abuse-secret" not in status.text
    assert status.json()["checks"]["intelligence"] is True
    assert status.json()["threat_intelligence"] == {
        "semak_mule": True,
        "virus_total": True,
        "abuse_ipdb": True,
        "rdap": True,
    }
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
        json={
            "token": token,
            "operator_id": "456",
            "operator_name": "Tho Kai Syuen",
        },
    )
    assert response.status_code == 200
    assert response.json()["bot"]["username"] == "hive_control_bot"
    assert store.read()["HIVE_CONTROL_BOT_TOKEN"] == token
    assert store.read()["HIVE_OPERATOR_ID"] == "456"
    assert store.read()["HIVE_OPERATOR_NAME"] == "Tho Kai Syuen"


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


def test_signing_key_status_exposes_public_fingerprint_not_private_material(tmp_path):
    client, _store, _ = _client(tmp_path)

    created = client.post(
        "/api/setup/signing-key",
        headers=_headers(),
        json={"path": "./secrets/signing_key.pem"},
    )
    status = client.get("/api/setup/status", headers=_headers())

    assert created.status_code == 200
    key = status.json()["signing_key"]
    assert key["valid"] is True
    assert key["bits"] == 2048
    assert len(key["fingerprint"]) == 64
    assert key["public_key_present"] is True
    assert key["public_key_matches"] is True
    assert key["rotation_allowed"] is True
    assert "PRIVATE KEY" not in status.text


def test_signing_key_rotation_is_non_overwriting_and_keeps_old_signatures_valid(tmp_path):
    client, store, _ = _client(tmp_path)
    created = client.post(
        "/api/setup/signing-key",
        headers=_headers(),
        json={"path": "./secrets/signing_key.pem"},
    ).json()
    old_path = tmp_path / created["path"]
    old_public = Path(str(old_path) + ".pub")
    old_bytes = old_path.read_bytes()
    signature = sign_bytes(b"sealed before rotation", str(old_path))

    response = client.post(
        "/api/setup/signing-key/rotate",
        headers=_headers(),
        json={
            "confirm_fingerprint": created["signing_key"]["fingerprint"],
            "reason": "scheduled test rotation",
        },
    )

    assert response.status_code == 200
    result = response.json()
    new_path = tmp_path / result["current"]["path"]
    assert result["previous"]["retained"] is True
    assert result["previous"]["fingerprint"] != result["current"]["fingerprint"]
    assert result["restart_required"] is True
    assert old_path.read_bytes() == old_bytes
    assert old_path.is_file() and old_public.is_file()
    assert new_path.is_file() and Path(str(new_path) + ".pub").is_file()
    assert store.read()["HIVE_SIGNING_KEY_PATH"] == result["current"]["path"]
    assert verify_signature(b"sealed before rotation", signature, str(old_public)) is True


def test_signing_key_rotation_rejects_stale_fingerprint(tmp_path):
    client, store, _ = _client(tmp_path)
    client.post(
        "/api/setup/signing-key",
        headers=_headers(),
        json={"path": "./secrets/signing_key.pem"},
    )

    response = client.post(
        "/api/setup/signing-key/rotate",
        headers=_headers(),
        json={"confirm_fingerprint": "0" * 64},
    )

    assert response.status_code == 409
    assert store.read()["HIVE_SIGNING_KEY_PATH"] == "./secrets/signing_key.pem"
    assert len(list((tmp_path / "secrets").glob("*.pem"))) == 1


def test_existing_invalid_signing_key_is_never_overwritten(tmp_path):
    client, _store, _ = _client(tmp_path)
    path = tmp_path / "secrets" / "signing_key.pem"
    path.parent.mkdir()
    path.write_text("not a private key", encoding="utf-8")

    response = client.post(
        "/api/setup/signing-key",
        headers=_headers(),
        json={"path": "./secrets/signing_key.pem"},
    )

    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == "not a private key"
