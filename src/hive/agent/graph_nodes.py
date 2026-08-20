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


def _build_messages(
    session: SessionState,
    recall: list[str],
    defense_note: str = "",
    case_context: str = "",
) -> list[ChatMessage]:
    persona = get_persona(session.persona)
    system = persona.system_prompt
    if recall:
        system += "\n\nThings the other person has told you so far:\n- " + "\n- ".join(recall)
    if defense_note:
        # S7: appended when an injection / bot-probe was detected, to keep the
        # agent in character instead of complying.
        system += defense_note
    if case_context:
        system += f"\n\n{case_context}"

    messages = [ChatMessage(role="system", content=system)]
    for m in session.messages[-_HISTORY_WINDOW:]:
        role = "user" if m.role == "stranger" else "assistant"
        content = m.text
        if m.role == "stranger" and m.media_analysis:
            description = str(m.media_analysis.get("description") or "").strip()
            if description:
                content += f"\n[Private image analysis: {description}]"
        messages.append(ChatMessage(role=role, content=content))
    return messages


def reason_and_reply(
    session: SessionState,
    client: LLMClient,
    *,
    recall: list[str] | None = None,
    route_inputs: RouteInputs | None = None,
    defense_note: str = "",
    case_context: str = "",
) -> tuple[str, Tier]:
    """Return (raw_reply_text, tier_used) for the current turn.

    `recall` is memory recall (mem0/Qdrant) injected by the caller so this
    function stays pure and testable. `route_inputs` carries the S6/S7 signals
    that drive model escalation. `defense_note` is the S7 persona-defense
    addendum, appended to the system prompt when an injection was detected.
    """
    recall = recall or []
    tier = route(route_inputs or RouteInputs())
    messages = _build_messages(session, recall, defense_note, case_context)

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
