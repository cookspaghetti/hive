"""Conversation runner: red-team scammer vs HIVE agent (fyp.txt S9).

Drives a full simulated conversation and returns a transcript plus basic
engagement metrics. HVI extraction/verdict accuracy hook in once L3/S6 land;
for now it exercises the L2 agent + router end-to-end and logs cost signals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hive.agent.graph_nodes import reason_and_reply
from hive.llm.client import LLMClient
from hive.llm.router import RouteInputs
from hive.logging_setup import bind_session, get_logger, reset_session
from hive.redteam.scammer import Archetype, scammer_reply
from hive.state import Message, SessionState

log = get_logger(__name__)


@dataclass
class RunResult:
    archetype: str
    transcript: list[tuple[str, str]] = field(default_factory=list)
    turns: int = 0
    agent_tiers: list[str] = field(default_factory=list)


def run_conversation(
    agent_client: LLMClient,
    scammer_client: LLMClient,
    archetype: Archetype,
    persona: str,
    max_turns: int = 8,
    opener: str = "Hello, I have a special offer for you today!",
) -> RunResult:
    """Run scammer<->agent for up to `max_turns` exchanges."""
    peer_id = hash(archetype.key) & 0xFFFF
    token = bind_session(peer_id)
    result = RunResult(archetype=archetype.key)
    try:
        session = SessionState(peer_id=peer_id, persona=persona)
        session.started_ts = time.time()
        history: list[tuple[str, str]] = []

        scam_msg = opener
        for turn in range(max_turns):
            history.append(("scammer", scam_msg))
            session.messages.append(
                Message(role="stranger", text=scam_msg, ts=time.time(), msg_id=turn * 2)
            )
            session.turn_count += 1

            reply, tier = reason_and_reply(
                session, agent_client, route_inputs=RouteInputs()
            )
            history.append(("victim", reply))
            session.messages.append(
                Message(role="agent", text=reply, ts=time.time(), msg_id=turn * 2 + 1)
            )
            result.agent_tiers.append(tier.value)

            scam_msg = scammer_reply(scammer_client, archetype, history)

        result.transcript = history
        result.turns = session.turn_count
        log.info("run[%s] complete: turns=%d", archetype.key, result.turns)
        return result
    finally:
        reset_session(token)
