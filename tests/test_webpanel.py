"""Web control panel tests (FastAPI TestClient), fully offline."""

import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from hive.state import HVI, Message, Phase, SessionState
from hive.vault.hashchain import HashChain
from hive.webpanel import create_app

TOKEN = "s3cret"


@dataclass
class FakeSettings:
    panel_token: str = TOKEN
    default_persona: str = "confused_elderly"
    signing_key_path: str = "key.pem"


class FakeEngine:
    def __init__(self):
        self.closed = []

    def close_session(self, session, chain, out_path, key_path, operator_name=""):
        self.closed.append(out_path)
        return out_path

    def summary(self, session):
        return "SUMMARY"


class FakeUserbot:
    def __init__(self):
        self._sessions = {}

    def begin_takeover(self, peer_id, persona):
        s = SessionState(peer_id=peer_id, persona=persona, phase=Phase.ACTIVE)
        self._sessions[peer_id] = (s, HashChain())

    def end_takeover(self, peer_id):
        return self._sessions.pop(peer_id, None)


@pytest.fixture
def client():
    engine, userbot = FakeEngine(), FakeUserbot()
    # seed one active session with some state
    s = SessionState(peer_id=100, persona="confused_elderly", phase=Phase.ACTIVE)
    s.turn_count = 2
    s.verdict = "likely_scam"
    s.verdict_score = 0.81
    s.messages.append(Message("stranger", "transfer to Maybank 123", time.time(), 0))
    s.hvis.append(HVI(kind="bank_account", value="123", source_msg_id=0, confidence=0.9))
    userbot._sessions[100] = (s, HashChain())
    app = create_app(engine, userbot, FakeSettings())
    c = TestClient(app)
    c._engine, c._userbot = engine, userbot
    return c


def _h():
    return {"X-HIVE-Token": TOKEN}


def test_requires_token(client):
    assert client.get("/api/sessions").status_code == 401


def test_index_is_public(client):
    r = client.get("/")
    assert r.status_code == 200 and "HIVE Control Panel" in r.text
    assert "<svg" in r.text  # inline logo present


def test_favicon_is_svg(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text


def test_list_sessions(client):
    r = client.get("/api/sessions", headers=_h())
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["peer_id"] == 100 and rows[0]["verdict"] == "likely_scam"


def test_session_detail(client):
    r = client.get("/api/sessions/100", headers=_h())
    d = r.json()
    assert d["hvi_items"][0]["value"] == "123"
    assert d["messages"][0]["role"] == "stranger"


def test_takeover_and_persona(client):
    assert client.post("/api/takeover", headers=_h(), json={"peer_id": 200, "persona": "overseas_worker"}).status_code == 200
    assert 200 in client._userbot._sessions
    r = client.post("/api/sessions/200/persona", headers=_h(), json={"persona": "small_business_owner"})
    assert r.status_code == 200
    assert client._userbot._sessions[200][0].persona == "small_business_owner"


def test_takeover_rejects_bad_persona(client):
    r = client.post("/api/takeover", headers=_h(), json={"peer_id": 201, "persona": "wizard"})
    assert r.status_code == 400


def test_stop_seals_and_removes(client):
    r = client.post("/api/sessions/100/stop", headers=_h())
    assert r.status_code == 200 and r.json()["summary"] == "SUMMARY"
    assert 100 not in client._userbot._sessions
    assert client._engine.closed == ["evidence/bundle_100.pdf"]


def test_detail_404_when_missing(client):
    assert client.get("/api/sessions/999", headers=_h()).status_code == 404
