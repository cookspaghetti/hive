"""ReAct reasoning step for Layer 2.

Given the conversation state, reason about the scammer's intent, choose an
action (stall / probe / elicit an HVI), and produce the raw persona reply
(before L1 middleware). Baiting strategy: stall -> build rapport ->
elicit HVI -> sustain (fyp.txt L2 / build-phase policy).
"""

from __future__ import annotations

from hive.state import SessionState


def reason_and_reply(session: SessionState, llm_client: object) -> str:
    """Return the raw (pre-middleware) reply text for the current turn.

    TODO(L2): build prompt from persona + memory recall + guardrail-cleaned
    history; call LLM; parse any tool intent (probe URL, etc.).
    """
    raise NotImplementedError
