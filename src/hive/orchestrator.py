"""LangGraph session lifecycle state machine (fyp.txt Section 8).

This is the backbone the whole system hangs off. Nodes below are wired
end-to-end but delegate real work to the layer modules (which are stubs for
now). The graph runs with fakes so tests/test_lifecycle.py passes before any
layer is implemented.

Reference: LangGraph-native state machine; per-session isolation pattern
studied from Hermes (reference-mapping.md, Section 8).
"""

from __future__ import annotations

import time
from typing import Callable

from langgraph.graph import END, StateGraph

from hive.state import GraphState, Message, Phase, SessionState


# --- Node implementations -------------------------------------------------
# Each node takes GraphState and returns a partial GraphState update.
# Real logic lives in the layer modules; here we orchestrate the flow.

def node_arm(state: GraphState) -> GraphState:
    """IDLE -> ARMED. Operator triggered takeover; awaiting confirmation."""
    session = state["session"]
    session.phase = Phase.ARMED
    session.started_ts = time.time()
    return {"session": session}


def node_ingress(state: GraphState) -> GraphState:
    """ARMED/ACTIVE. A stranger message arrives; move to ACTIVE, record it."""
    session = state["session"]
    session.phase = Phase.ACTIVE
    inbound = state.get("inbound")
    if inbound is not None:
        session.messages.append(inbound)
        session.turn_count += 1
    return {"session": session}


def node_guardrails(state: GraphState) -> GraphState:
    """S7 prompt-injection screening of the untrusted stranger message.

    TODO: delegate to hive.guardrails.injection.screen().
    """
    return state


def node_extract(state: GraphState) -> GraphState:
    """L3. Harvest HVIs from the inbound message (text + media).

    TODO: delegate to hive.extraction.
    """
    return state


def node_reason(state: GraphState) -> GraphState:
    """L2. Deceptive ReAct step: decide the persona reply.

    TODO: delegate to hive.agent.graph_nodes.
    """
    return {"outbound_text": ""}


def node_middleware(state: GraphState) -> GraphState:
    """L1. Apply linguistic + temporal noise before sending.

    TODO: delegate to hive.middleware.
    """
    return state


def node_verdict(state: GraphState) -> GraphState:
    """S6. Update the continuous hybrid scam score.

    TODO: delegate to hive.verdict.engine.
    """
    return state


def node_close(state: GraphState) -> GraphState:
    """CLOSING -> SEALED. Compile + sign the evidence bundle.

    TODO: delegate to hive.vault.bundle.
    """
    session = state["session"]
    session.phase = Phase.SEALED
    return {"session": session}


# --- Routing --------------------------------------------------------------

def _should_close(state: GraphState) -> str:
    """Termination triggers (fyp.txt S8): stop, budget, or confident verdict."""
    session = state["session"]
    if state.get("stop_requested"):
        return "close"
    if session.turn_count >= _MAX_TURNS:
        return "close"
    return "continue"


# Budgets are injected at build time so they can be overridden in tests.
_MAX_TURNS = 60


def build_graph(max_turns: int = 60) -> "Callable":
    """Construct and compile the lifecycle graph.

    Returns the compiled LangGraph app. Set max_turns low in tests.
    """
    global _MAX_TURNS
    _MAX_TURNS = max_turns

    g: StateGraph = StateGraph(GraphState)
    g.add_node("arm", node_arm)
    g.add_node("ingress", node_ingress)
    g.add_node("guardrails", node_guardrails)
    g.add_node("extract", node_extract)
    g.add_node("reason", node_reason)
    g.add_node("middleware", node_middleware)
    g.add_node("verdict", node_verdict)
    g.add_node("close", node_close)

    g.set_entry_point("arm")
    g.add_edge("arm", "ingress")
    g.add_edge("ingress", "guardrails")
    g.add_edge("guardrails", "extract")
    g.add_edge("extract", "reason")
    g.add_edge("reason", "middleware")
    g.add_edge("middleware", "verdict")
    g.add_conditional_edges(
        "verdict",
        _should_close,
        {"continue": "ingress", "close": "close"},
    )
    g.add_edge("close", END)

    return g.compile()


def new_session(peer_id: int, persona: str) -> SessionState:
    return SessionState(peer_id=peer_id, persona=persona)
