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
        Message(
            role="stranger",
            text="请转账到这个账户，收款人是王小明。",
            ts=time.time(),
            msg_id=2,
        ),
    ]
    s.hvis = [
        HVI(kind="bank_account", value="1234567890", source_msg_id=0, confidence=0.9),
        HVI(kind="account_holder", value="王小明", source_msg_id=2, confidence=0.85),
    ]
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


def test_bundle_failure_does_not_publish_partial_files(tmp_path, keypair, monkeypatch):
    import hive.vault.bundle as bundle

    priv, _pub = keypair
    session, chain = _populated_session()
    out = tmp_path / "bundle.pdf"

    def fail_sign(_data, _key_path):
        raise RuntimeError("signer unavailable")

    monkeypatch.setattr(bundle, "sign_bytes", fail_sign)

    with pytest.raises(RuntimeError, match="signer unavailable"):
        bundle.build_bundle(session, chain, str(out), priv)

    assert not out.exists()
    assert not out.with_suffix(".pdf.sig").exists()
    assert list(tmp_path.glob(".*.tmp*")) == []


def test_bundle_registers_a_unicode_font_for_mandarin():
    from reportlab.pdfbase import pdfmetrics

    from hive.vault.bundle import _font_pair

    regular, bold = _font_pair()

    assert regular not in {"Helvetica", "Times-Roman"}
    assert bold not in {"Helvetica-Bold", "Times-Bold"}
    assert pdfmetrics.stringWidth("请转账到这个账户", regular, 10) > 0


def test_s90a_certificate_contains_operator():
    from hive.vault.bundle import s90a_certificate

    session, _ = _populated_session()
    cert = s90a_certificate(session, "Tho Kai Syuen")
    assert "Tho Kai Syuen" in cert
    assert "90A" in cert


def test_bundle_paths_are_unique_and_reject_traversal(tmp_path):
    from hive.vault.paths import new_bundle_path, parse_bundle_name

    session, _ = _populated_session()
    first = new_bundle_path(tmp_path, session)
    second = new_bundle_path(tmp_path, session)

    assert first != second
    assert parse_bundle_name(first.name)[0] == session.peer_id
    assert parse_bundle_name("bundle_99.pdf") == (99, None)
    assert parse_bundle_name("../bundle_99_1.pdf") is None
