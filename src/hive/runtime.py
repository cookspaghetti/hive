"""HIVE runtime — the turn pipeline that wires all layers together.

This is the integration seam the transports call. One `HiveEngine` instance
serves the whole process; `process_turn` handles a single inbound stranger
message and returns what to send (text + human delay), advancing the session
lifecycle (fyp.txt S8). `close_session` seals the evidence bundle (L5).

Per-turn flow (fyp.txt S6/S7 + L1-L5):
    1. S7 guardrails screen the untrusted message  -> escalation + persona defense
    2. L3 extraction harvests HVIs                 -> hard signals
    3. (URLs found) L4 sandbox analysis            -> hard signal
    4. S6 verdict update                           -> continuous score
    5. Safeguard: early-exit if likely_benign      -> graceful hand-back
    6. L2 reason (router picks model tier)         -> raw reply
    7. L1 middleware (typos + tarpit delay)        -> what to send

All external dependencies (LLM client, NER backend, sandbox runner, memory)
are injected so the engine is testable offline.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Per-layer implementations are invoked by the LangGraph nodes in
# hive.orchestrator; the engine only holds dependencies and drives the graph.
from hive.audit import audit_event
from hive.extraction.ner import NerBackend
from hive.llm.client import LLMClient
from hive.logging_setup import bind_session, get_logger, reset_session
from hive.sandbox.runner import BrowserRunner
from hive.state import Message, Phase, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


@dataclass
class TurnOutput:
    text: str | None          # reply to send (None if handing back / no reply)
    delay_s: float = 0.0      # tarpit delay before sending
    messages: tuple[str, ...] = ()  # distinct Telegram bubbles
    message_delays_s: tuple[float, ...] = ()  # delay before each bubble
    message_typing_s: tuple[float, ...] = ()  # visible typing portion of each delay
    pace: str = "normal"  # model-selected engagement pace
    handed_back: bool = False  # early-exit: conversation deemed benign
    terminated: bool = False   # budget exhausted (max_turns / max_duration)
    reason: str = ""           # "" | "benign" | "max_turns" | "max_duration"
    verdict: str = "inconclusive"


@dataclass
class HiveEngine:
    agent_client: LLMClient
    sandbox_runner: BrowserRunner
    ner_backend: NerBackend | None = None
    enable_early_exit: bool = True
    early_exit_min_turns: int = 3   # don't bail before we've seen enough
    max_turns: int = 60             # 0 disables; else terminate past this
    max_session_minutes: int = 120  # 0 disables; else terminate past this
    # Factory(peer_id) -> MemoryBackend. Defaults to the offline KeywordMemory.
    memory_factory: object = None
    _memories: dict = field(default_factory=dict)
    _compiled: object = None        # cached compiled LangGraph turn graph
    _compile_lock: Any = field(default_factory=threading.Lock, repr=False)
    _session_locks: dict[int, Any] = field(default_factory=dict, repr=False)

    def _graph(self):
        if self._compiled is None:
            with self._compile_lock:
                if self._compiled is None:
                    from hive.orchestrator import build_turn_graph

                    self._compiled = build_turn_graph(self)
        return self._compiled

    def _lock_for(self, peer_id: int) -> Any:
        lock = self._session_locks.get(peer_id)
        if lock is None:
            lock = threading.RLock()
            self._session_locks[peer_id] = lock
        return lock

    def _memory_for(self, peer_id: int):
        mem = self._memories.get(peer_id)
        if mem is None:
            from hive.agent.memory import KeywordMemory
            factory = self.memory_factory or (lambda pid: KeywordMemory(pid))
            mem = factory(peer_id)
            self._memories[peer_id] = mem
        return mem

    def forget(self, peer_id: int) -> None:
        """Drop a conversation's memory (call when a takeover ends)."""
        existed = self._memories.pop(peer_id, None) is not None
        audit_event(
            "memory",
            "session_memory_forgotten",
            component="runtime.memory",
            payload={"existed": existed},
            peer_id=peer_id,
        )

    def new_session(self, peer_id: int, persona: str) -> tuple[SessionState, HashChain]:
        s = SessionState(peer_id=peer_id, persona=persona, phase=Phase.ARMED)
        s.started_ts = time.time()
        self._lock_for(peer_id)
        self._memory_for(peer_id)  # initialise memory for this conversation
        audit_event(
            "session_lifecycle",
            "session_started",
            component="runtime",
            payload={"persona": persona},
            peer_id=peer_id,
            session_id=s.session_id,
        )
        return s, HashChain()

    def process_turn(
        self,
        session: SessionState,
        chain: HashChain,
        inbound: Message,
    ) -> TurnOutput:
        """Run one inbound message through the LangGraph turn pipeline."""
        return self.process_messages(session, chain, [inbound])

    def process_messages(
        self,
        session: SessionState,
        chain: HashChain,
        inbounds: list[Message],
        *,
        record_outbound: bool = True,
    ) -> TurnOutput:
        """Process a burst as one phone-check while preserving each inbound message."""
        if not inbounds:
            raise ValueError("at least one inbound message is required")
        latest = inbounds[-1]
        inbound = Message(
            role="stranger",
            text="\n".join(message.text for message in inbounds if message.text),
            ts=latest.ts,
            msg_id=latest.msg_id,
        )
        token = bind_session(session.peer_id, session.session_id)
        try:
            audit_event(
                "batch_processing",
                "batch_processing_started",
                component="runtime",
                payload={
                    "message_count": len(inbounds),
                    "messages": [
                        {"msg_id": item.msg_id, "text": item.text, "ts": item.ts}
                        for item in inbounds
                    ],
                    "record_outbound": record_outbound,
                },
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
            with self._lock_for(session.peer_id):
                final = self._graph().invoke(
                    {
                        "session": session,
                        "chain": chain,
                        "inbound": inbound,
                        "inbounds": inbounds,
                        "record_outbound": record_outbound,
                    }
                )
            reason = final.get("reason", "")
            output = TurnOutput(
                text=final.get("outbound"),
                delay_s=final.get("delay_s", 0.0),
                messages=tuple(final.get("outbound_messages", ())),
                message_delays_s=tuple(final.get("message_delays_s", ())),
                message_typing_s=tuple(final.get("message_typing_s", ())),
                pace=final.get("pace", "normal"),
                handed_back=(reason == "benign"),
                terminated=bool(final.get("terminate")) and reason in ("max_turns", "max_duration"),
                reason=reason,
                verdict=final.get("verdict", session.verdict),
            )
            audit_event(
                "batch_processing",
                "batch_processing_completed",
                component="runtime",
                payload={
                    "messages": list(output.messages),
                    "delays_s": list(output.message_delays_s),
                    "typing_s": list(output.message_typing_s),
                    "pace": output.pace,
                    "verdict": output.verdict,
                    "reason": output.reason,
                    "handed_back": output.handed_back,
                    "terminated": output.terminated,
                },
                peer_id=session.peer_id,
                session_id=session.session_id,
            )
            return output
        except Exception as exc:
            audit_event(
                "batch_processing",
                "batch_processing_failed",
                component="runtime",
                payload={"error": str(exc)},
                peer_id=session.peer_id,
                session_id=session.session_id,
                level="error",
            )
            raise
        finally:
            reset_session(token)

    def record_outbound(
        self,
        session: SessionState,
        chain: HashChain,
        text: str,
        *,
        ts: float | None = None,
        remember: bool = True,
    ) -> Message:
        """Record a reply bubble after its transport confirms delivery."""
        with self._lock_for(session.peer_id):
            sent_at = time.time() if ts is None else ts
            message = Message(
                role="agent",
                text=text,
                ts=sent_at,
                msg_id=session.next_agent_msg_id,
            )
            session.next_agent_msg_id -= 1
            session.messages.append(message)
            chain.append(
                {"event": "msg_out", "msg_id": message.msg_id, "text": text},
                ts=sent_at,
            )
            if remember:
                self._memory_for(session.peer_id).add("agent", text)
                audit_event(
                    "memory",
                    "memory_entry_added",
                    component="runtime.memory",
                    payload={"role": "agent", "text": text},
                    peer_id=session.peer_id,
                    session_id=session.session_id,
                )
            audit_event(
                "message",
                "outbound_recorded",
                component="runtime",
                payload={"msg_id": message.msg_id, "text": text, "ts": sent_at},
                peer_id=session.peer_id,
                session_id=session.session_id,
                ts=sent_at,
            )
            return message

    def steer_pending_reply(
        self,
        session: SessionState,
        pending_text: str,
        new_inbounds: list[Message],
    ) -> str:
        """Decide whether to send one drafted bubble or reconsider new messages."""
        from hive.llm.client import ChatMessage
        from hive.llm.router import Tier

        latest = "\n".join(f"- {message.text}" for message in new_inbounds[-4:])
        response = self.agent_client.complete(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "You are steering realistic mobile-chat timing. A person drafted a "
                        "message, but more messages arrived before they sent it. Reply with "
                        "exactly SEND_FIRST if the draft is a short, self-contained reaction "
                        "that remains sensible to send before reading/revising. Reply with "
                        "exactly CONTINUE if it may be stale, contradicted, too detailed, or "
                        "better reconsidered with the new messages. Prefer CONTINUE when unsure."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=f"DRAFT:\n{pending_text}\n\nNEW MESSAGES:\n{latest}",
                ),
            ],
            tier=Tier.LIGHT,
            temperature=0.0,
        )
        decision = "send_first" if response.text.strip().upper() == "SEND_FIRST" else "continue"
        log.info(
            "reply steer: peer=%d decision=%s new_messages=%d",
            session.peer_id,
            decision,
            len(new_inbounds),
        )
        audit_event(
            "reply_steering",
            "pending_reply_steered",
            component="runtime",
            payload={
                "decision": decision,
                "pending_text": pending_text,
                "new_messages": [
                    {"msg_id": message.msg_id, "text": message.text, "ts": message.ts}
                    for message in new_inbounds
                ],
            },
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return decision

    def close_session(
        self,
        session: SessionState,
        chain: HashChain,
        out_path: str,
        key_path: str,
        operator_name: str = "",
    ) -> str:
        """Seal the session: compile + sign the evidence bundle (L5)."""
        from hive.vault.bundle import build_bundle

        audit_event(
            "session_lifecycle",
            "session_seal_started",
            component="runtime",
            payload={"out_path": out_path, "operator_name": operator_name},
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        with self._lock_for(session.peer_id):
            previous_phase = session.phase
            session.phase = Phase.CLOSING
            try:
                path = build_bundle(session, chain, out_path, key_path, operator_name=operator_name)
            except Exception:
                session.phase = previous_phase
                audit_event(
                    "session_lifecycle",
                    "session_seal_failed",
                    component="runtime",
                    payload={"out_path": out_path},
                    peer_id=session.peer_id,
                    session_id=session.session_id,
                    level="error",
                )
                log.exception("session seal failed: peer=%d", session.peer_id)
                raise
            session.phase = Phase.SEALED
            self.forget(session.peer_id)  # release this conversation's memory
        log.info(
            "session sealed: peer=%d verdict=%s bundle=%s",
            session.peer_id,
            session.verdict,
            path,
        )
        audit_event(
            "session_lifecycle",
            "session_sealed",
            component="runtime",
            payload={"bundle": path, "verdict": session.verdict},
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return path

    def summary(self, session: SessionState) -> str:
        """Human-readable session summary for the operator (control bot)."""
        kinds = sorted({h.kind for h in session.hvis})
        return (
            f"Verdict: {session.verdict} (score {session.verdict_score:.2f})\n"
            f"Turns: {session.turn_count}\n"
            f"HVIs: {len(session.hvis)} {kinds}\n"
            f"Sandbox runs: {len(session.sandbox_results)}"
        )


def build_engine(settings, *, load_ner: bool = True) -> HiveEngine:
    """Construct a fully-wired HiveEngine from Settings.

    Loads the LLM client (Ollama Cloud), the disposable-container sandbox
    runner, and — optionally — the GLiNER NER backend (heavy first load).
    """
    import os

    from hive.agent.memory import build_memory
    from hive.extraction.ner import get_default_backend
    from hive.llm.client import build_client
    from hive.sandbox.runner import PlaywrightDockerRunner

    client = build_client(settings)
    sandbox_memory = os.getenv("HIVE_SANDBOX_MEMORY_LIMIT", "512m").strip() or None
    runner = PlaywrightDockerRunner(memory_limit=sandbox_memory)
    ner = get_default_backend() if load_ner else None
    memory_factory = lambda pid: build_memory(pid, settings)  # noqa: E731
    log.info(
        "build_engine: llm=%s ner=%s semantic_memory=%s",
        settings.llm_model_cheap, bool(ner), getattr(settings, "use_semantic_memory", False),
    )
    return HiveEngine(
        agent_client=client,
        sandbox_runner=runner,
        ner_backend=ner,
        memory_factory=memory_factory,
        max_turns=getattr(settings, "max_turns", 60),
        max_session_minutes=getattr(settings, "max_session_minutes", 120),
    )
