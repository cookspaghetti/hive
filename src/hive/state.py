"""Session state schema shared across the LangGraph state machine.

One SessionState instance exists per stranger conversation, keyed by the
Telegram peer ID, so concurrent conversations stay isolated (fyp.txt S8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypedDict
from uuid import uuid4


class Phase(StrEnum):
    """Session lifecycle states (fyp.txt Section 8)."""

    IDLE = "idle"
    ARMED = "armed"          # operator triggered, awaiting Bot API confirmation
    ACTIVE = "active"        # engaging the stranger
    PROBING = "probing"      # sandbox analysing a URL
    CLOSING = "closing"      # winding down, compiling bundle
    SEALED = "sealed"        # evidence bundle signed; terminal


Verdict = Literal["likely_scam", "inconclusive", "likely_benign"]


@dataclass
class HVI:
    """A harvested High-Value Indicator (fyp.txt Layer 3)."""

    kind: str            # e.g. "bank_account", "url", "phone", "telegram_id", "crypto"
    value: str
    source_msg_id: int
    confidence: float = 1.0
    extractor: str = "unknown"


@dataclass
class Message:
    role: Literal["stranger", "agent", "system"]
    text: str
    ts: float
    msg_id: int
    media_kind: str | None = None  # "image", "qr", "voice", ...
    media_name: str | None = None
    media_mime: str | None = None
    media_size: int | None = None
    media_path: str | None = None
    media_sha256: str | None = None
    media_analysis: dict[str, Any] | None = None
    media_hvis: list[HVI] = field(default_factory=list)
    captured_ts: float | None = None
    platform: str = "telegram"
    pre_takeover: bool = False


@dataclass
class SessionState:
    peer_id: int
    persona: str
    session_id: str = field(default_factory=lambda: uuid4().hex)
    phase: Phase = Phase.IDLE
    messages: list[Message] = field(default_factory=list)
    hvis: list[HVI] = field(default_factory=list)
    indicator_reviews: list[dict[str, Any]] = field(default_factory=list)
    sandbox_results: list[dict[str, Any]] = field(default_factory=list)
    threat_intelligence: list[dict[str, Any]] = field(default_factory=list)
    verdict_score: float = 0.0
    verdict: Verdict = "inconclusive"
    turn_count: int = 0
    exchange_count: int = 0
    next_agent_msg_id: int = -1
    reply_pace: Literal["fast", "normal", "slow"] = "normal"
    started_ts: float | None = None
    signal_trail: list[dict[str, Any]] = field(default_factory=list)  # explainable verdict log
    replay_of: str | None = None
    media_analysis: list[dict[str, Any]] = field(default_factory=list)
    related_cases: list[dict[str, Any]] = field(default_factory=list)
    case_probe_context: str = ""
    case_retrieved_at_turn: int = 0
    case_indicator_count_at_retrieval: int = 0
    peer_display_name: str = ""
    peer_username: str = ""
    identity_observed_ts: float | None = None


class GraphState(TypedDict, total=False):
    """The dict LangGraph passes between nodes. Wraps SessionState plus the
    in-flight inbound message being processed this turn."""

    session: SessionState
    inbound: Message | None
    outbound_text: str | None
    stop_requested: bool
