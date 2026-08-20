"""Soft-signal LLM classifier for the Verdict Engine (fyp.txt S6).

Runs the LIGHT model tier over the recent conversation and returns a mapping of
soft-signal label -> confidence (0..1). Kept separate from the scorer so the
scorer stays pure/offline-testable and this piece can be swapped or mocked.

Runs every turn, so it deliberately uses the cheap/light tier (router.Tier.LIGHT).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from hive.llm.client import ChatMessage, LLMClient
from hive.llm.router import Tier
from hive.logging_setup import get_logger
from hive.state import SessionState
from hive.verdict.engine import SOFT_WEIGHTS

log = get_logger(__name__)

_HISTORY_WINDOW = 10

_SYSTEM = """You are a high-precision fraud-signal classifier for an anti-scam system.
Assess only the OTHER party's messages. Use these definitions exactly:
- urgency: explicit time pressure, repeated demands for an immediate response, a deadline,
  or fear of missing out. A greeting, short message, or ordinary follow-up is not urgency.
- authority_impersonation: a claim to represent a bank, police, government, court, tax agency,
  company support team, or another trusted authority. Calling someone an agent is insufficient.
- romance_framing: deliberate romantic affection, relationship-building, marriage, or soulmate
  framing. Polite friendliness is insufficient.
- investment_framing: a financial investment, trading, cryptocurrency, profit, return, or
  money-making opportunity. An unrelated use of the word opportunity is insufficient.
- payment_request: an explicit request or instruction to transfer money or value, pay a fee,
  deposit funds, buy a gift card, or send cryptocurrency. Asking for a phone number, bank-account
  number, or other information is not itself a payment request.
- inconsistency: two or more cited messages contain a material contradiction or changed story.
  Suspicion, vagueness, or 'trust me' alone is not inconsistency.

Return ONLY one compact JSON object containing every label. Map each label to an object with
"score" and "message_ids". message_ids must cite only THEM message IDs that directly support
that exact signal. Use {"score":0.0,"message_ids":[]} when the signal is absent or uncertain.
Never invent an ID and never cite a message merely because it is nearby. No prose."""


_LABEL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "urgency": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:urgent(?:ly)?|immediately|asap|hurry|quick(?:ly)?|right now|act now)\b",
            r"\b(?:reply|respond|answer)\s+(?:me\s+)?(?:now|quick(?:ly)?|asap)\b",
            r"\b(reply|respond|answer)(?:\s+\1)+\b",
            r"\b(?:last|final)\s+(?:chance|warning|notice)\b",
            r"\bdon'?t\s+(?:wait|delay|miss(?:\s+out)?)\b",
            r"\b(?:due|deadline|expires?)\b(?:\s+\w+){0,3}\s+(?:today|tonight)\b",
            r"\bwithin\s+\d+\s+(?:minutes?|hours?)\b",
            r"(?:快点|赶快|尽快|马上|立即|现在就|最后机会|别错过|不要错过)",
        )
    ),
    "authority_impersonation": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:i(?:'m|\s+am)|we(?:'re|\s+are)|speaking|calling|writing)\s+"
            r"(?:from|on behalf of)\s+(?:the\s+)?(?:bank|police|government|court|tax|lhdn|"
            r"customs|immigration|support|security)",
            r"\b(?:bank|police|government|court|tax|lhdn|customs|immigration|customer\s+support|"
            r"security)\s+(?:officer|official|agent|department|team|representative)\b",
            r"\b(?:official|authorised|authorized)\s+(?:representative|officer|notice)\b",
            r"(?:我是|我们是|来自|代表)(?:银行|警方|警察|政府|法院|税务局|客服|官方)",
            r"(?:银行职员|银行客服|警察|警官|政府官员|法院人员|税务人员|官方代表)",
        )
    ),
    "romance_framing": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:my\s+)?(?:darling|sweetheart|babe|baby|soulmate)\b",
            r"\b(?:i\s+love\s+you|fall(?:ing)?\s+in\s+love|marry\s+you|our\s+relationship)\b",
            r"(?:亲爱的|宝贝|爱你|爱上你|恋爱|结婚|灵魂伴侣|老公|老婆)",
        )
    ),
    "investment_framing": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:invest(?:ment|ing)?|trading?|forex|stocks?|shares?|cryptocurrency|crypto|"
            r"bitcoin|btc|ethereum|eth|usdt)\b",
            r"\b(?:guaranteed|high|daily|weekly|monthly)\s+(?:profit|return|yield|income)\b",
            r"\b(?:double|triple|multiply)\s+(?:your\s+)?(?:money|funds?|investment)\b",
            r"\b(?:passive\s+income|money[- ]making|profit\s+opportunity)\b",
            r"(?:投资|回报|收益|利润|比特币|加密货币|虚拟货币|外汇|股票|赚钱|翻倍)",
        )
    ),
    "payment_request": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:please|kindly|must|you\s+(?:need|have|must)\s+to)\s+"
            r"(?:pay|transfer|deposit|wire|remit)\b",
            r"\b(?:pay|transfer|deposit|wire|remit)\s+"
            r"(?:me|us|them|now|today|immediately|asap|to\s+(?:this|the|my)|"
            r"(?:rm|myr|usd|\$)\s*\d)",
            r"\b(?:pay|transfer|deposit|wire|remit)\b(?:\s+\w+){0,5}\s+"
            r"(?:money|cash|funds?|fee|payment|deposit|rm|myr|usd|dollars?|ringgit|"
            r"bitcoin|btc|crypto|usdt|wallet|account)\b",
            r"\b(?:send|give)\s+(?:me|us|them|him|her)?\s*(?:the|some|your)?\s*"
            r"(?:money|cash|funds?|fee|payment|deposit|rm|myr|usd|dollars?|ringgit|"
            r"bitcoin|btc|crypto|usdt)\b",
            r"\b(?:make|complete)\s+(?:the|a|your)?\s*(?:payment|transfer|deposit)\b",
            r"\b(?:bank\s+in|top\s*up|buy|purchase)\b(?:\s+\w+){0,4}\s+"
            r"(?:gift\s*cards?|voucher|crypto|bitcoin|btc|usdt)\b",
            r"(?:转账|汇款|付款|付钱|打钱|入金|充值|交费|手续费|保证金|买礼品卡|购买礼品卡)",
        )
    ),
    "inconsistency": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:actually|instead|not what i said|i changed|change of plan|forget what i said|"
            r"earlier i|before i|nope|that was wrong|i meant)\b",
            r"(?:其实|改口|我改了|改变计划|刚才说错|之前说|不是这样|算了)",
        )
    ),
}


@dataclass(frozen=True)
class SoftAssessment:
    scores: dict[str, float]
    evidence: dict[str, list[int]]


def _supports_label(label: str, text: str) -> bool:
    return any(pattern.search(text) for pattern in _LABEL_PATTERNS[label])


def _validate_evidence(
    assessment: SoftAssessment,
    messages: dict[int, str],
) -> SoftAssessment:
    """Keep only message citations that locally entail their assigned label.

    The LLM remains useful for semantic scoring, but a high-impact soft signal
    cannot enter the verdict merely because the model cited a real, nearby ID.
    These deliberately conservative checks favour precision over recall.
    """
    scores: dict[str, float] = {}
    evidence: dict[str, list[int]] = {}
    for label in SOFT_WEIGHTS:
        score = assessment.scores.get(label, 0.0)
        cited = assessment.evidence.get(label, [])
        supported = [
            message_id
            for message_id in cited
            if _supports_label(label, messages[message_id])
        ]
        if label == "inconsistency":
            # Contradictions require a comparison, not one suspicious sentence.
            supported = cited if len(set(cited)) >= 2 and any(
                _supports_label(label, messages[message_id]) for message_id in cited
            ) else []
        if score > 0 and not supported:
            log.info("S6 classifier: rejected unsupported %s signal", label)
            score = 0.0
        scores[label] = score
        evidence[label] = supported
    return SoftAssessment(scores=scores, evidence=evidence)


def _json_object(text: str) -> dict:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        raw = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        log.warning("S6 classifier: could not parse JSON from model output")
        return {}
    return raw if isinstance(raw, dict) else {}


def _message_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _parse_assessment(
    text: str,
    valid_message_ids: set[int] | None = None,
) -> SoftAssessment:
    """Parse scores plus verifiable message-level evidence.

    Numeric values remain accepted for compatibility with older model output,
    but structured positive signals are rejected when none of their cited
    message IDs exist in the supplied classifier window.
    """
    raw = _json_object(text)
    scores: dict[str, float] = {}
    evidence: dict[str, list[int]] = {}
    for label in SOFT_WEIGHTS:
        value = raw.get(label)
        ids: list[int] = []
        if isinstance(value, dict):
            score = value.get("score")
            raw_ids = value.get("message_ids")
            if isinstance(raw_ids, list):
                normalized_ids = (_message_id(item) for item in raw_ids)
                ids = list(
                    dict.fromkeys(
                        message_id
                        for message_id in normalized_ids
                        if message_id is not None
                        and (valid_message_ids is None or message_id in valid_message_ids)
                    )
                )
            if valid_message_ids is not None and isinstance(score, (int, float)) and score > 0:
                if not ids:
                    score = 0.0
        else:
            score = value
            if valid_message_ids is not None and isinstance(score, (int, float)) and score > 0:
                # Live classification requires structured, message-grounded output.
                # Numeric legacy values remain supported by _parse_scores only.
                score = 0.0
        if isinstance(score, (int, float)):
            scores[label] = max(0.0, min(1.0, float(score)))
            evidence[label] = ids
    return SoftAssessment(scores=scores, evidence=evidence)


def _parse_scores(text: str) -> dict[str, float]:
    """Best-effort parse of the model's JSON; ignore junk, clamp to [0,1]."""
    return _parse_assessment(text).scores


def classify_soft(session: SessionState, client: LLMClient) -> SoftAssessment:
    """Return soft-signal scores with validated message-level evidence."""
    window = session.messages[-_HISTORY_WINDOW:]
    convo = "\n".join(
        f"{'THEM' if m.role == 'stranger' else 'ME'} [{m.msg_id}]: {m.text}"
        for m in window
    )
    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(role="user", content=convo),
    ]
    resp = client.complete(messages, tier=Tier.LIGHT, temperature=0.0)
    stranger_messages = {
        message.msg_id: message.text for message in window if message.role == "stranger"
    }
    assessment = _parse_assessment(
        resp.text,
        set(stranger_messages),
    )
    assessment = _validate_evidence(assessment, stranger_messages)
    log.info("S6 classifier: parsed %d soft signals", len(assessment.scores))
    return assessment
