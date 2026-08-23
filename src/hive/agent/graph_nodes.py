"""ReAct reasoning step for Layer 2 (fyp.txt L2).

Given the conversation state, produce the raw persona reply (before L1
middleware). Model tier is chosen by the cost-router. Recent messages and a
deterministic summary of validated in-session indicators preserve continuity.
Baiting strategy: stall -> build rapport -> elicit HVI -> sustain.

Logging: persona, chosen tier, context size, and reply length are logged so a
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
    defense_note: str = "",
    case_context: str = "",
) -> list[ChatMessage]:
    persona = get_persona(session.persona)
    system = persona.system_prompt
    session_context = _session_fact_context(session)
    if session_context:
        system += f"\n\n{session_context}"
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


def _session_fact_context(session: SessionState) -> str:
    """Build bounded continuity context without storing conversation vectors."""
    facts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in session.hvis:
        kind = str(item.kind).replace("_", " ")
        value = str(item.value).strip()
        key = (kind, value.casefold())
        if not value or key in seen:
            continue
        facts.append(f"{kind}: {value[:256]}")
        seen.add(key)
        if len(facts) >= 12:
            break
    if not facts:
        return ""
    return (
        "Private current-conversation facts, derived only from this stranger's "
        "validated messages. Use them for continuity; do not mention extraction, "
        "databases, or internal analysis:\n- "
        + "\n- ".join(facts)
    )


def reason_and_reply(
    session: SessionState,
    client: LLMClient,
    *,
    route_inputs: RouteInputs | None = None,
    defense_note: str = "",
    case_context: str = "",
) -> tuple[str, Tier]:
    """Return (raw_reply_text, tier_used) for the current turn.

    `route_inputs` carries the S6/S7 signals that drive model escalation.
    `defense_note` is the S7 persona-defense addendum, appended to the system
    prompt when an injection was detected.
    """
    tier = route(route_inputs or RouteInputs())
    messages = _build_messages(session, defense_note, case_context)

    log.info(
        "reason: persona=%s tier=%s history=%d fact_count=%d",
        session.persona,
        tier.value,
        min(len(session.messages), _HISTORY_WINDOW),
        min(len(session.hvis), 12),
    )
    resp = client.complete(messages, tier=tier)
    log.info("reason: reply_len=%d", len(resp.text))
    return resp.text, tier
