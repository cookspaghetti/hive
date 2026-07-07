"""L5 evidence vault tests (fyp.txt L5).

Covers RSA sign/verify round-trip and full evidence-bundle generation
(PDF + detached signature) with real cryptography and reportlab.
"""

import time

import pytest

from hive.state import HVI, Message, SessionState
from hive.vault.hashchain import HashChain
from hive.vault.signer import generate_keypair, sign_bytes, verify_signature


@pytest.fixture
def keypair(tmp_path):
    priv = tmp_path / "key.pem"
    pub = tmp_path / "key.pub"
    generate_keypair(str(priv), str(pub), bits=2048)
    return str(priv), str(pub)


def test_sign_verify_roundtrip(keypair):
    priv, pub = keypair
    data = b"evidence bundle bytes"
    sig = sign_bytes(data, priv)
    assert verify_signature(data, sig, pub) is True


def test_tampered_data_fails_verification(keypair):
    priv, pub = keypair
    sig = sign_bytes(b"original", priv)
    assert verify_signature(b"tampered", sig, pub) is False


def _populated_session() -> tuple[SessionState, HashChain]:
    s = SessionState(peer_id=99, persona="confused_elderly")
    s.verdict = "likely_scam"
    s.verdict_score = 0.82
    s.turn_count = 2
    s.messages = [
        Message(role="stranger", text="transfer to Maybank 1234567890", ts=time.time(), msg_id=0),
        Message(role="agent", text="aiyo how ah", ts=time.time(), msg_id=1),
    ]
    s.hvis = [HVI(kind="bank_account", value="1234567890", source_msg_id=0, confidence=0.9)]
    s.sandbox_results = [{"url": "http://x.co", "final_url": "http://phish.ru",
                          "verdict_signal": "malicious", "dest_ip": "1.2.3.4",
                          "cloaking_suspected": False}]
    chain = HashChain()
    chain.append({"event": "msg", "text": "transfer to Maybank 1234567890"}, ts=1.0)
    chain.append({"event": "hvi", "value": "1234567890"}, ts=2.0)
    return s, chain


def test_build_bundle_creates_pdf_and_signature(tmp_path, keypair):
    from hive.vault.bundle import build_bundle

    priv, pub = keypair
    session, chain = _populated_session()
    out = str(tmp_path / "bundle.pdf")
    path = build_bundle(session, chain, out, priv, operator_name="Tho Kai Syuen")

    from pathlib import Path

    pdf = Path(path)
    sig = Path(path + ".sig")
    assert pdf.exists() and pdf.stat().st_size > 0
    assert sig.exists() and sig.stat().st_size > 0
    assert pdf.read_bytes().startswith(b"%PDF")

    # The detached signature must verify against the produced PDF.
    assert verify_signature(pdf.read_bytes(), sig.read_bytes(), pub) is True


def test_s90a_certificate_contains_operator():
    from hive.vault.bundle import s90a_certificate

    session, _ = _populated_session()
    cert = s90a_certificate(session, "Tho Kai Syuen")
    assert "Tho Kai Syuen" in cert
    assert "90A" in cert
