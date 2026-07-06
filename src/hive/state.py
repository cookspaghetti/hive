"""Session state schema shared across the LangGraph state machine.

One SessionState instance exists per stranger conversation, keyed by the
Telegram peer ID, so concurrent conversations stay isolated (fyp.txt S8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypedDict


class Phase(str, Enum):
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


@dataclass
class Message:
    role: Literal["stranger", "agent", "system"]
    text: str
    ts: float
    msg_id: int
    media_kind: str | None = None  # "image", "qr", "voice", ...


@dataclass
class SessionState:
    peer_id: int
    persona: str
    phase: Phase = Phase.IDLE
    messages: list[Message] = field(default_factory=list)
    hvis: list[HVI] = field(default_factory=list)
    sandbox_results: list[dict[str, Any]] = field(default_factory=list)
    verdict_score: float = 0.0
    verdict: Verdict = "inconclusive"
    turn_count: int = 0
    started_ts: float | None = None
    signal_trail: list[dict[str, Any]] = field(default_factory=list)  # explainable verdict log


class GraphState(TypedDict, total=False):
    """The dict LangGraph passes between nodes. Wraps SessionState plus the
    in-flight inbound message being processed this turn."""

    session: SessionState
    inbound: Message | None
    outbound_text: str | None
    stop_requested: bool
