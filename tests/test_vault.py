"""L5 evidence vault tests (fyp.txt L5).

Covers RSA sign/verify round-trip and full evidence-bundle generation
(PDF + detached signature) with real cryptography and reportlab.
"""

import time
import zipfile
from pathlib import Path

import pytest

from hive.state import HVI, Message, SessionState
from hive.vault.bundle import _message_xml, _sandbox_finding_xml, _xml
from hive.vault.hashchain import HashChain
from hive.vault.package import evidence_package_path, verify_evidence_package
from hive.vault.signer import (
    generate_keypair,
    public_key_fingerprint,
    sign_bytes,
    signing_key_details,
    verify_signature,
)


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


def test_signing_key_details_expose_stable_public_identity_only(keypair):
    priv, pub = keypair

    details = signing_key_details(priv)

    assert details["valid"] is True
    assert details["algorithm"] == "RSA-PSS/SHA-256"
    assert details["bits"] == 2048
    assert details["fingerprint"] == public_key_fingerprint(Path(pub).read_bytes())
    assert details["public_key_present"] is False
    assert "private" not in str(details).lower()


def test_transcript_emoji_uses_font_or_explicit_unicode_fallback():
    assert _message_xml("wait 😰 now", "EmojiFont") == (
        'wait <font name="EmojiFont">😰</font> now'
    )
    assert _message_xml("wait 😰 now", None) == "wait [emoji U+1F630] now"


def test_report_xml_preserves_zero_and_false_values():
    assert _xml(0) == "0"
    assert _xml(False) == "False"


def test_sandbox_finding_includes_actionable_error_and_missing_values():
    finding = _sandbox_finding_xml(
        1,
        {
            "url": "https://example.test/high-risk",
            "verdict_signal": "error",
            "access_state": "error",
            "fetcher": "scrapling_stealthy",
            "runtime_ms": 45055,
            "error": "Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE",
        },
    )

    assert "Error detail" in finding
    assert "ERR_HTTP_RESPONSE_CODE_FAILURE" in finding
    assert "45055 ms" in finding
    assert "Final URL:</b> Unavailable" in finding


def _populated_session() -> tuple[SessionState, HashChain]:
    s = SessionState(peer_id=99, persona="confused_elderly")
    s.peer_display_name = "Observed Sender"
    s.peer_username = "observed_sender"
    s.identity_observed_ts = 1.0
    s.verdict = "likely_scam"
    s.verdict_score = 0.82
    s.turn_count = 2
    s.messages = [
        Message(
            role="stranger",
            text="transfer to Maybank 1234567890",
            ts=time.time(),
            msg_id=0,
            captured_ts=time.time(),
            pre_takeover=True,
        ),
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
    s.threat_intelligence = [
        {
            "provider": "semak_mule",
            "provider_label": "Semak Mule",
            "indicator_kind": "bank_account",
            "observable": "1234567890",
            "status": "hit",
            "risk": "malicious",
            "summary": "PDRM records show 2 reports for this value.",
            "checked_ts": time.time(),
        }
    ]
    s.media_analysis = [
        {
            "source_msg_id": 2,
            "source": "local_ocr",
            "description": "收款人是王小明",
            "indicator_count": 1,
        }
    ]
    chain = HashChain()
    chain.append({"event": "msg", "text": "transfer to Maybank 1234567890"}, ts=1.0)
    chain.append({"event": "hvi", "value": "1234567890"}, ts=2.0)
    return s, chain


def test_build_bundle_creates_portable_verified_evidence_package(tmp_path, keypair):
    from hive.vault.bundle import build_bundle

    priv, pub = keypair
    session, chain = _populated_session()
    attachment = tmp_path / "captured-notice.txt"
    attachment.write_text("synthetic attachment", encoding="utf-8")
    session.messages[0].media_path = str(attachment)
    session.messages[0].media_name = "courier notice.txt"
    session.messages[0].media_kind = "document"
    out = str(tmp_path / "bundle.pdf")
    path = build_bundle(session, chain, out, priv, operator_name="Tho Kai Syuen")

    pdf = Path(path)
    sig = Path(path + ".sig")
    package = evidence_package_path(pdf)
    assert pdf.exists() and pdf.stat().st_size > 0
    assert sig.exists() and sig.stat().st_size > 0
    assert package.exists() and package.stat().st_size > 0
    assert pdf.read_bytes().startswith(b"%PDF")

    # The detached signature must verify against the produced PDF.
    assert verify_signature(pdf.read_bytes(), sig.read_bytes(), pub) is True
    verification = verify_evidence_package(package)
    assert verification["ok"] is True
    assert all(verification["checks"].values())
    assert verification["manifest"]["signing_key_fingerprint"] == (
        signing_key_details(priv)["fingerprint"]
    )
    assert verification["checks"]["attachment_001_checksum"] is True
    assert verification["manifest"]["attachments"][0]["source_name"] == (
        "courier notice.txt"
    )
    with zipfile.ZipFile(package) as archive:
        assert "attachment_001_courier_notice.txt" in archive.namelist()


def test_build_bundle_embeds_received_image_preview(tmp_path, keypair):
    from PIL import Image as PILImage

    from hive.vault.bundle import build_bundle

    priv, _pub = keypair
    without_image, chain = _populated_session()
    baseline = Path(
        build_bundle(without_image, chain, str(tmp_path / "without-image.pdf"), priv)
    )

    with_image, image_chain = _populated_session()
    received = tmp_path / "received-photo.png"
    PILImage.new("RGB", (320, 240), color=(229, 163, 33)).save(received)
    message = with_image.messages[0]
    message.media_path = str(received)
    message.media_name = received.name
    message.media_kind = "image"
    message.media_mime = "image/png"
    message.media_size = received.stat().st_size
    message.media_sha256 = "a" * 64
    embedded = Path(
        build_bundle(with_image, image_chain, str(tmp_path / "with-image.pdf"), priv)
    )

    assert embedded.read_bytes().count(b"/Subtype /Image") > baseline.read_bytes().count(
        b"/Subtype /Image"
    )


def test_evidence_package_verifier_rejects_tampering(tmp_path, keypair):
    from hive.vault.bundle import build_bundle

    priv, _pub = keypair
    session, chain = _populated_session()
    pdf = Path(build_bundle(session, chain, str(tmp_path / "bundle.pdf"), priv))
    package = evidence_package_path(pdf)
    tampered = tmp_path / "tampered.evidence.zip"

    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == pdf.name:
                data += b"tampered"
            target.writestr(info, data)

    verification = verify_evidence_package(tampered)
    assert verification["ok"] is False
    assert verification["checks"]["pdf_checksum"] is False
    assert verification["checks"]["pdf_signature"] is False


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
    assert not evidence_package_path(out).exists()
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
    assert "Observed Sender" in cert
    assert "observed_sender" in cert


def test_bundle_paths_are_unique_and_reject_traversal(tmp_path):
    from hive.vault.paths import new_bundle_path, parse_bundle_name

    session, _ = _populated_session()
    first = new_bundle_path(tmp_path, session)
    second = new_bundle_path(tmp_path, session)

    assert first != second
    assert parse_bundle_name(first.name)[0] == session.peer_id
    assert parse_bundle_name("bundle_99.pdf") == (99, None)
    assert parse_bundle_name("../bundle_99_1.pdf") is None
