"""Outbound guardrails prevent unsafe model claims from reaching Telegram."""

import time

from hive.guardrails.outbound import enforce_outbound_safety
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.state import Message
from tests.fakes import fake_client


class _CleanRunner:
    def run(self, url: str) -> RawFindings:
        return RawFindings(final_url=url, title="clean", body_len=100)


def test_otp_and_sensitive_action_claims_are_replaced_in_matching_language():
    unsafe = enforce_outbound_safety(
        "好，我刚刚填了OTP 123456，现在激活成功了。",
        incoming_text="把验证码填进去",
    )

    assert unsafe.flagged is True
    assert set(unsafe.reasons) == {"otp_disclosure", "completed_sensitive_action"}
    assert "123456" not in unsafe.text
    assert "还没操作" in unsafe.text


def test_harmless_stalling_question_is_not_rewritten():
    reply = "I haven't entered anything. What is the OTP for?"

    safe = enforce_outbound_safety(reply)

    assert safe.flagged is False
    assert safe.text == reply


def test_complete_pipeline_never_records_blocked_otp_reply():
    engine = HiveEngine(
        agent_client=fake_client("[[pace:fast]] I entered OTP 123456 already"),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=False,
    )
    session, chain = engine.new_session(700, "naive_young_adult")

    output = engine.process_turn(
        session,
        chain,
        Message("stranger", "Enter the OTP now", time.time(), 1),
    )

    assert output.outbound_guardrail_flag is True
    assert "123456" not in (output.text or "")
    assert all(
        "123456" not in message.text
        for message in session.messages
        if message.role == "agent"
    )
    assert chain.verify() is True
