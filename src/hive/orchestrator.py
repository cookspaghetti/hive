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

from hive.audit import audit_event
from hive.extraction.engine import extract_contextual_hvis, extract_hvis, merge_hvis
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
    inbounds: list[Message]
    record_outbound: bool
    # working values passed between nodes
    screen_flagged: bool
    hvis: list
    recall: list
    verdict: str
    reply: str
    tier: str
    # outputs / control
    outbound: str | None
    outbound_messages: list[str]
    message_delays_s: list[float]
    message_typing_s: list[float]
    pace: str
    delay_s: float
    terminate: bool
    reason: str  # "" | "benign" | "max_turns" | "max_duration"


def build_turn_graph(engine: HiveEngine):
    """Compile the per-turn graph, binding nodes to `engine`'s dependencies."""

    def n_ingress(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbounds = state.get("inbounds") or [state["inbound"]]
        session.phase = Phase.ACTIVE
        for inbound in inbounds:
            session.messages.append(inbound)
            engine._memory_for(session.peer_id).add("stranger", inbound.text)
            audit_event(
                "memory",
                "memory_entry_added",
                component="orchestrator.memory",
                payload={"role": "stranger", "text": inbound.text},
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
            chain.append(
                {"event": "msg_in", "msg_id": inbound.msg_id, "text": inbound.text},
                ts=inbound.ts,
            )
            audit_event(
                "message",
                "inbound_recorded",
                component="orchestrator.ingress",
                payload={
                    "msg_id": inbound.msg_id,
                    "text": inbound.text,
                    "ts": inbound.ts,
                    "media_kind": inbound.media_kind,
                },
                peer_id=session.peer_id,
                session_id=session.session_id,
                ts=inbound.ts,
            )
        session.turn_count += len(inbounds)
        session.exchange_count += 1

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
            audit_event(
                "budget",
                "session_budget_exhausted",
                component="orchestrator.ingress",
                payload={"reason": reason, "turn_count": session.turn_count},
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
            return {"terminate": True, "reason": reason, "outbound": None}
        return {"terminate": False, "reason": ""}

    def n_guardrails(state: TurnState) -> TurnState:
        result = screen(state["inbound"].text)
        session = state["session"]
        audit_event(
            "guardrail",
            "message_screened",
            component="orchestrator.guardrails",
            payload={
                "flagged": result.flagged,
                "category": result.category,
                "matched": result.matched,
            },
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return {"screen_flagged": result.flagged}

    def n_extract(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbounds = state.get("inbounds") or [state["inbound"]]
        hvis = [
            hvi
            for inbound in inbounds
            for hvi in extract_hvis(
                inbound.text,
                inbound.msg_id,
                ner_backend=engine.ner_backend,
            )
        ]
        hvis.extend(
            extract_contextual_hvis(
                session.messages,
                {inbound.msg_id for inbound in inbounds},
            )
        )
        hvis.extend(item for inbound in inbounds for item in inbound.media_hvis)
        known_media = {
            int(item.get("source_msg_id") or 0)
            for item in session.media_analysis
            if isinstance(item, dict)
        }
        for inbound in inbounds:
            if inbound.media_analysis and inbound.msg_id not in known_media:
                session.media_analysis.append(dict(inbound.media_analysis))
                known_media.add(inbound.msg_id)
        accepted = merge_hvis(session.hvis, hvis)
        for h in accepted:
            chain.append({"event": "hvi", "kind": h.kind, "value": h.value}, ts=time.time())
        audit_event(
            "extraction",
            "indicators_extracted",
            component="orchestrator.extraction",
            payload={
                "discovered": [
                    {
                        "kind": item.kind,
                        "value": item.value,
                        "source_msg_id": item.source_msg_id,
                        "confidence": item.confidence,
                        "extractor": item.extractor,
                    }
                    for item in hvis
                ],
                "accepted_count": len(accepted),
            },
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return {"hvis": hvis}

    def n_sandbox(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        for h in [x for x in state.get("hvis", []) if x.kind == "url"]:
            session.phase = Phase.PROBING
            result = analyze_url(h.value, engine.sandbox_runner)
            session.sandbox_results.append(result)
            chain.append(
                {
                    "event": "sandbox",
                    "url": h.value,
                    "signal": result.get("verdict_signal"),
                },
                ts=time.time(),
            )
            audit_event(
                "sandbox",
                "url_analysis_failed" if result.get("error") else "url_analysis_completed",
                component="orchestrator.sandbox",
                payload=result,
                peer_id=session.peer_id,
                session_id=session.session_id,
                level="error" if result.get("error") else "info",
            )
        session.phase = Phase.ACTIVE
        return {}

    def n_verdict(state: TurnState) -> TurnState:
        session = state["session"]
        inbounds = state.get("inbounds") or [state["inbound"]]
        soft = classify_soft(session, engine.agent_client)
        verdict = update_verdict(
            session,
            soft=soft.scores,
            source_messages=inbounds,
            soft_evidence=soft.evidence,
        )
        audit_event(
            "verdict",
            "verdict_updated",
            component="orchestrator.verdict",
            payload={
                "verdict": verdict,
                "score": session.verdict_score,
                "soft_signals": soft.scores,
                "soft_evidence": soft.evidence,
                "signal_trail": session.signal_trail[-1:] or [],
            },
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        # Safeguard: benign early-exit hand-back.
        if (
            engine.enable_early_exit
            and verdict == "likely_benign"
            and session.exchange_count >= engine.early_exit_min_turns
        ):
            session.phase = Phase.CLOSING
            log.info("Safeguard: early-exit, handing conversation back (benign)")
            audit_event(
                "session_lifecycle",
                "benign_handback_requested",
                component="orchestrator.verdict",
                payload={"verdict": verdict, "exchanges": session.exchange_count},
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
            return {"verdict": verdict, "terminate": True, "reason": "benign", "outbound": None}
        return {"verdict": verdict, "terminate": False}

    def n_reason(state: TurnState) -> TurnState:
        from hive.agent.graph_nodes import reason_and_reply
        from hive.case_intelligence import build_probe_context

        session = state["session"]
        inbound = state["inbound"]
        memory = engine._memory_for(session.peer_id)
        recall = memory.recall(inbound.text)
        audit_event(
            "memory",
            "memory_recalled",
            component="orchestrator.memory",
            payload={"query": inbound.text, "results": recall},
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        route_inputs = RouteInputs(injection_flagged=state.get("screen_flagged", False))
        defense = persona_defense_note(screen(inbound.text)) if state.get("screen_flagged") else ""
        should_retrieve_cases = bool(
            engine.case_intelligence
            and session.verdict_score >= 0.55
            and (
                not session.related_cases
                or session.turn_count - session.case_retrieved_at_turn >= 3
                or len(session.hvis) > session.case_indicator_count_at_retrieval
            )
        )
        if should_retrieve_cases:
            context, matches = build_probe_context(session, engine.case_intelligence)
            session.case_probe_context = context
            session.related_cases = matches
            session.case_retrieved_at_turn = session.turn_count
            session.case_indicator_count_at_retrieval = len(session.hvis)
            audit_event(
                "semantic_case_retrieval",
                "case_guidance_prepared",
                component="orchestrator.reason",
                payload={"matches": matches, "context": context},
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
        reply, tier = reason_and_reply(
            session,
            engine.agent_client,
            recall=recall,
            route_inputs=route_inputs,
            defense_note=defense,
            case_context=session.case_probe_context,
        )
        return {"reply": reply, "tier": tier.value, "recall": recall}

    def n_middleware(state: TurnState) -> TurnState:
        session = state["session"]
        chain = state["chain"]
        inbound = state["inbound"]
        mw = apply_middleware(
            state["reply"],
            session.persona,
            incoming_len=len(inbound.text),
            incoming_text=inbound.text,
        )
        messages = mw.messages or ((mw.text,) if mw.text else ())
        session.reply_pace = mw.pace
        audit_event(
            "reply_plan",
            "reply_plan_created",
            component="orchestrator.middleware",
            payload={
                "raw_reply": state["reply"],
                "messages": list(messages),
                "delays_s": list(mw.message_delays_s),
                "typing_s": list(mw.message_typing_s),
                "pace": mw.pace,
            },
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        if state.get("record_outbound", True):
            for text in messages:
                engine.record_outbound(session, chain, text, remember=False)
            engine._memory_for(session.peer_id).add("agent", state["reply"])
            audit_event(
                "memory",
                "memory_entry_added",
                component="orchestrator.memory",
                payload={"role": "agent", "text": state["reply"]},
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
        log.info(
            "turn done: tier=%s verdict=%s delay=%.1fs",
            state.get("tier"),
            state.get("verdict"),
            mw.delay_s,
        )
        return {
            "outbound": mw.text,
            "outbound_messages": list(messages),
            "message_delays_s": list(mw.message_delays_s),
            "message_typing_s": list(mw.message_typing_s),
            "pace": mw.pace,
            "delay_s": mw.delay_s,
        }

    def _after_ingress(state: TurnState) -> str:
        return "end" if state.get("terminate") else "continue"

    def _after_verdict(state: TurnState) -> str:
        return "end" if state.get("terminate") else "continue"

    g: StateGraph = StateGraph(TurnState)
    for name, fn in [
        ("ingress", n_ingress), ("guardrails", n_guardrails), ("extract", n_extract),
        ("sandbox", n_sandbox),
        ("verdict", n_verdict),
        ("reason", n_reason),
        ("middleware", n_middleware),
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
