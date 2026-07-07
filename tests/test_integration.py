"""End-to-end integration test (fyp.txt full pipeline).

Drives HiveEngine across a short scam conversation with all real layers wired
together (LLM + sandbox runner faked for offline determinism), then seals the
evidence bundle. Proves: reply produced, HVIs extracted, sandbox invoked on a
URL, verdict escalates to likely_scam, and a signed PDF bundle is written.
"""

import time

from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.state import Message
from hive.vault.signer import generate_keypair, verify_signature
from tests.fakes import fake_client


class MaliciousRunner:
    def run(self, url: str) -> RawFindings:
        return RawFindings(
            final_url="http://phish-login.ru/signin",
            redirect_chain=["http://bit.ly/x", "http://phish-login.ru/signin"],
            has_password_field=True,
            title="Bank Login",
            body_len=4000,
            dest_ip="203.0.113.9",
        )


def _engine() -> HiveEngine:
    # Agent + soft-classifier share the fake client; classifier JSON is parsed
    # from the same canned reply, so give it scam-ish soft signals.
    client = fake_client('{"urgency": 0.9, "payment_request": 0.9, "investment_framing": 0.8}')
    return HiveEngine(agent_client=client, sandbox_runner=MaliciousRunner(), enable_early_exit=False)


def test_full_scam_session(tmp_path):
    eng = _engine()
    session, chain = eng.new_session(peer_id=1234, persona="confused_elderly")

    scam_msgs = [
        "Hello! You have won our investment prize, act now!",
        "Please transfer to Maybank account 1234567890 urgently",
        "Click http://bit.ly/x to verify your identity",
    ]
    for i, text in enumerate(scam_msgs):
        out = eng.process_turn(session, chain, Message("stranger", text, time.time(), i * 2))
        assert out.text  # agent replied
        assert out.delay_s >= 2.0

    # HVIs harvested (bank account + url at least)
    kinds = {h.kind for h in session.hvis}
    assert "bank_account" in kinds
    assert "url" in kinds

    # Sandbox ran on the URL and flagged malicious
    assert any(r["verdict_signal"] == "malicious" for r in session.sandbox_results)

    # Verdict escalated to scam
    assert session.verdict == "likely_scam"
    assert session.verdict_score >= 0.7

    # Chain still intact after all events
    assert chain.verify() is True

    # Seal the bundle and verify its signature
    priv = tmp_path / "k.pem"
    pub = tmp_path / "k.pub"
    generate_keypair(str(priv), str(pub))
    out_pdf = str(tmp_path / "evidence.pdf")
    path = eng.close_session(session, chain, out_pdf, str(priv), operator_name="Tho Kai Syuen")

    from pathlib import Path

    pdf = Path(path).read_bytes()
    sig = Path(path + ".sig").read_bytes()
    assert pdf.startswith(b"%PDF")
    assert verify_signature(pdf, sig, str(pub)) is True


def test_early_exit_hands_back_on_benign(tmp_path):
    # Benign: no HVIs, soft signals all zero -> should hand back after min turns.
    client = fake_client("{}")
    eng = HiveEngine(agent_client=client, sandbox_runner=MaliciousRunner(),
                     enable_early_exit=True, early_exit_min_turns=2)
    session, chain = eng.new_session(peer_id=7, persona="small_business_owner")

    out1 = eng.process_turn(session, chain, Message("stranger", "hi is this the shop?", time.time(), 0))
    assert out1.text  # turn 1 still engages
    out2 = eng.process_turn(session, chain, Message("stranger", "ok thanks see you", time.time(), 2))
    assert out2.handed_back is True
    assert out2.text is None
