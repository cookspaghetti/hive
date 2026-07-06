"""ReAct reasoning step for Layer 2 (fyp.txt L2).

Given the conversation state, produce the raw persona reply (before L1
middleware). Model tier is chosen by the cost-router; memory recall is folded
into the prompt. Baiting strategy: stall -> build rapport -> elicit HVI ->
sustain.

Logging: persona, chosen tier, recall hits, and reply length are logged so a
turn can be reconstructed for debugging and the S9 evaluation.
"""

from __future__ import annotations

from hive.agent.personas import get_persona
from hive.llm.client import ChatMessage, LLMClient
from hive.llm.router import RouteInputs, Tier, route
from hive.logging_setup import get_logger
from hive.state import SessionState

log = get_logger(__name__)

# How many recent turns to include verbatim in the prompt.
_HISTORY_WINDOW = 12


def _build_messages(session: SessionState, recall: list[str]) -> list[ChatMessage]:
    persona = get_persona(session.persona)
    system = persona.system_prompt
    if recall:
        system += "\n\nThings the other person has told you so far:\n- " + "\n- ".join(recall)

    messages = [ChatMessage(role="system", content=system)]
    for m in session.messages[-_HISTORY_WINDOW:]:
        role = "user" if m.role == "stranger" else "assistant"
        messages.append(ChatMessage(role=role, content=m.text))
    return messages


def reason_and_reply(
    session: SessionState,
    client: LLMClient,
    *,
    recall: list[str] | None = None,
    route_inputs: RouteInputs | None = None,
) -> tuple[str, Tier]:
    """Return (raw_reply_text, tier_used) for the current turn.

    `recall` is memory recall (mem0/Qdrant) injected by the caller so this
    function stays pure and testable. `route_inputs` carries the S6/S7 signals
    that drive model escalation.
    """
    recall = recall or []
    tier = route(route_inputs or RouteInputs())
    messages = _build_messages(session, recall)

    log.info(
        "reason: persona=%s tier=%s history=%d recall=%d",
        session.persona,
        tier.value,
        min(len(session.messages), _HISTORY_WINDOW),
        len(recall),
    )
    resp = client.complete(messages, tier=tier)
    log.info("reason: reply_len=%d", len(resp.text))
    return resp.text, tier
