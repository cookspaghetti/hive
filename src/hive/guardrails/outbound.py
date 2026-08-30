"""Deterministic last-line safety checks for model-generated persona replies."""

from __future__ import annotations

import re
from dataclasses import dataclass

_OTP_DISCLOSURE = re.compile(
    r"(?:(?<![a-z0-9_])(?:otp|tac)(?![a-z0-9_])|"
    r"\b(?:verification|security)\s*code\b|验证码|一次性密码)"
    r"[^\d\n]{0,28}\b\d{4,8}\b"
    r"|\b\d{4,8}\b[^\n]{0,28}(?:(?<![a-z0-9_])(?:otp|tac)"
    r"(?![a-z0-9_])|验证码|一次性密码)",
    re.IGNORECASE,
)
_CREDENTIAL_DISCLOSURE = re.compile(
    r"\b(?:my\s+)?(?:password|passcode|pin)\s*(?:is|:|=)\s*\S+"
    r"|(?:我的)?(?:密码|口令|PIN码?)\s*(?:是|:|：|=)\s*\S+",
    re.IGNORECASE,
)
_COMPLETED_SENSITIVE_ACTION = re.compile(
    r"\b(?:i|i've|i have)\s+(?:already\s+|just\s+)?"
    r"(?:sent|paid|transferred|entered|submitted|installed|logged\s+in|"
    r"registered|uploaded)\b"
    r"|\b(?:saya|aku)\s+(?:dah|sudah)\s+(?:transfer|bayar|isi|hantar)\b"
    r"|我[^。！？\n]{0,12}(?:已经|刚刚|刚才)?"
    r"(?:转了|汇了|付了|付款了|填了|输入了|提交了|安装了|登录了|注册了|上传了)"
    r"|\bmoney\s+(?:is\s+)?gone\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OutboundSafetyResult:
    text: str
    flagged: bool
    reasons: tuple[str, ...] = ()


def enforce_outbound_safety(reply: str, *, incoming_text: str = "") -> OutboundSafetyResult:
    """Replace unsafe action/credential claims with a safe in-character stall."""
    reasons: list[str] = []
    if _OTP_DISCLOSURE.search(reply):
        reasons.append("otp_disclosure")
    if _CREDENTIAL_DISCLOSURE.search(reply):
        reasons.append("credential_disclosure")
    if _COMPLETED_SENSITIVE_ACTION.search(reply):
        reasons.append("completed_sensitive_action")
    if not reasons:
        return OutboundSafetyResult(reply, False)

    context = f"{incoming_text}\n{reply}"
    has_cjk = any("\u3400" <= char <= "\u9fff" for char in context)
    fallback = (
        "[[pace:slow]] 我还没操作，刚才没弄成功。你可以再慢慢讲一次吗？"
        if has_cjk
        else "[[pace:slow]] Wait ah, I haven't done anything yet. "
        "It didn't work—can you explain it again?"
    )
    return OutboundSafetyResult(fallback, True, tuple(reasons))
