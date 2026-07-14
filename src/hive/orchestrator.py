"""LangGraph per-turn pipeline (fyp.txt Layer 2 / S8).

This is the real orchestration graph the runtime executes for every inbound
message. Nodes delegate to the actual layer implementations; dependencies
(LLM client, sandbox runner, NER backend, per-peer memory, budgets) are bound
by closing over the owning `HiveEngine`, so the graph stays declarative while
the engine remains the single place that holds state.

Flow (edges):

    START -> ingress -> [budget?] -> guardrails -> extract -> sandbox
          -> verdict -> [terminate?] -> reason -> middleware -> END

`ingress` enforces the turn/duration budget (Hermes IterationBudget pattern:
consume-per-turn with an explicit exit reason). `verdict` performs the benign
early-exit safeguard. Either short-circuits to END with a termination reason.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from hive.extraction.engine import extract_hvis
from hive.guardrails.injection import persona_defense_note, screen
from hive.llm.router import RouteInputs
from hive.logging_setup import get_logger
from hive.middleware.pipeline import apply as apply_middleware
from hive.sandbox.analyzer import analyze_url
from hive.state import Message, Phase
from hive.verdict.classifier import classify_soft
from hive.verdict.engine import update_verdict

if TYPE_CHECKING:
    from hive.runtime import HiveEngine

log = get_logger(__name__)


class TurnState(TypedDict, total=False):
    # inputs
    session: Any
    chain: Any
    inbound: Message
    # working values passed between nodes
    screen_flagged: bool
    hvis: list
    recall: list
    verdict: str
    reply: str
    tier: str
    # outputs / control
    outbound: str | None
    delay_s: float
    terminate: bool
    reason: str  # "" | "benign" | "max_turns" | "max_duration"


def build_turn_graph(engine: "HiveEngine"):
    """Compile the per-turn graph, binding nodes to `engine`'s dependencies."""

    def n_ingress(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbound = state["inbound"]
        session.phase = Phase.ACTIVE
        session.messages.append(inbound)
        session.turn_count += 1
        engine._memory_for(session.peer_id).add("stranger", inbound.text)
        chain.append({"event": "msg_in", "msg_id": inbound.msg_id, "text": inbound.text}, ts=inbound.ts)

        # Budget enforcement (Hermes IterationBudget-style, with a duration cap).
        reason = ""
        if engine.max_turns and session.turn_count > engine.max_turns:
            reason = "max_turns"
        elif engine.max_session_minutes and session.started_ts:
            elapsed_min = (time.time() - session.started_ts) / 60.0
            if elapsed_min >= engine.max_session_minutes:
                reason = "max_duration"
        if reason:
            session.phase = Phase.CLOSING
            log.info("budget exhausted: reason=%s turn=%d", reason, session.turn_count)
            return {"terminate": True, "reason": reason, "outbound": None}
        return {"terminate": False, "reason": ""}

    def n_guardrails(state: TurnState) -> TurnState:
        return {"screen_flagged": screen(state["inbound"].text).flagged}

    def n_extract(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbound = state["inbound"]
        hvis = extract_hvis(inbound.text, inbound.msg_id, ner_backend=engine.ner_backend)
        for h in hvis:
            session.hvis.append(h)
            chain.append({"event": "hvi", "kind": h.kind, "value": h.value}, ts=time.time())
        return {"hvis": hvis}

    def n_sandbox(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        for h in [x for x in state.get("hvis", []) if x.kind == "url"]:
            session.phase = Phase.PROBING
            result = analyze_url(h.value, engine.sandbox_runner)
            session.sandbox_results.append(result)
            chain.append({"event": "sandbox", "url": h.value, "signal": result.get("verdict_signal")}, ts=time.time())
        session.phase = Phase.ACTIVE
        return {}

    def n_verdict(state: TurnState) -> TurnState:
        session = state["session"]
        soft = classify_soft(session, engine.agent_client)
        verdict = update_verdict(session, soft=soft)
        # Safeguard: benign early-exit hand-back.
        if (
            engine.enable_early_exit
            and verdict == "likely_benign"
            and session.turn_count >= engine.early_exit_min_turns
        ):
            session.phase = Phase.CLOSING
            log.info("Safeguard: early-exit, handing conversation back (benign)")
            return {"verdict": verdict, "terminate": True, "reason": "benign", "outbound": None}
        return {"verdict": verdict, "terminate": False}

    def n_reason(state: TurnState) -> TurnState:
        from hive.agent.graph_nodes import reason_and_reply

        session = state["session"]
        inbound = state["inbound"]
        memory = engine._memory_for(session.peer_id)
        recall = memory.recall(inbound.text)
        route_inputs = RouteInputs(injection_flagged=state.get("screen_flagged", False))
        defense = persona_defense_note(screen(inbound.text)) if state.get("screen_flagged") else ""
        reply, tier = reason_and_reply(
            session, engine.agent_client, recall=recall, route_inputs=route_inputs, defense_note=defense
        )
        return {"reply": reply, "tier": tier.value, "recall": recall}

    def n_middleware(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbound = state["inbound"]
        mw = apply_middleware(state["reply"], session.persona, incoming_len=len(inbound.text))
        out_msg = Message(role="agent", text=mw.text, ts=time.time(), msg_id=inbound.msg_id + 1)
        session.messages.append(out_msg)
        engine._memory_for(session.peer_id).add("agent", state["reply"])
        chain.append({"event": "msg_out", "text": mw.text}, ts=out_msg.ts)
        log.info("turn done: tier=%s verdict=%s delay=%.1fs", state.get("tier"), state.get("verdict"), mw.delay_s)
        return {"outbound": mw.text, "delay_s": mw.delay_s}

    def _after_ingress(state: TurnState) -> str:
        return "end" if state.get("terminate") else "continue"

    def _after_verdict(state: TurnState) -> str:
        return "end" if state.get("terminate") else "continue"

    g: StateGraph = StateGraph(TurnState)
    for name, fn in [
        ("ingress", n_ingress), ("guardrails", n_guardrails), ("extract", n_extract),
        ("sandbox", n_sandbox), ("verdict", n_verdict), ("reason", n_reason), ("middleware", n_middleware),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "ingress")
    g.add_conditional_edges("ingress", _after_ingress, {"continue": "guardrails", "end": END})
    g.add_edge("guardrails", "extract")
    g.add_edge("extract", "sandbox")
    g.add_edge("sandbox", "verdict")
    g.add_conditional_edges("verdict", _after_verdict, {"continue": "reason", "end": END})
    g.add_edge("reason", "middleware")
    g.add_edge("middleware", END)
    return g.compile()
