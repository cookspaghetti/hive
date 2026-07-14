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

import time
from dataclasses import dataclass, field

# Per-layer implementations are invoked by the LangGraph nodes in
# hive.orchestrator; the engine only holds dependencies and drives the graph.
from hive.extraction.ner import NerBackend
from hive.llm.client import LLMClient
from hive.logging_setup import bind_session, get_logger, reset_session
from hive.sandbox.runner import BrowserRunner
from hive.state import Phase, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


@dataclass
class TurnOutput:
    text: str | None          # reply to send (None if handing back / no reply)
    delay_s: float = 0.0      # tarpit delay before sending
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

    def _graph(self):
        if self._compiled is None:
            from hive.orchestrator import build_turn_graph
            self._compiled = build_turn_graph(self)
        return self._compiled

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
        self._memories.pop(peer_id, None)

    def new_session(self, peer_id: int, persona: str) -> tuple[SessionState, HashChain]:
        s = SessionState(peer_id=peer_id, persona=persona, phase=Phase.ARMED)
        s.started_ts = time.time()
        self._memory_for(peer_id)  # initialise memory for this conversation
        return s, HashChain()

    def process_turn(
        self,
        session: SessionState,
        chain: HashChain,
        inbound: Message,
    ) -> TurnOutput:
        """Run one inbound message through the LangGraph turn pipeline."""
        token = bind_session(session.peer_id)
        try:
            final = self._graph().invoke({"session": session, "chain": chain, "inbound": inbound})
            reason = final.get("reason", "")
            return TurnOutput(
                text=final.get("outbound"),
                delay_s=final.get("delay_s", 0.0),
                handed_back=(reason == "benign"),
                terminated=bool(final.get("terminate")) and reason in ("max_turns", "max_duration"),
                reason=reason,
                verdict=final.get("verdict", session.verdict),
            )
        finally:
            reset_session(token)

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

        session.phase = Phase.CLOSING
        path = build_bundle(session, chain, out_path, key_path, operator_name=operator_name)
        session.phase = Phase.SEALED
        self.forget(session.peer_id)  # release this conversation's memory
        log.info("session sealed: peer=%d verdict=%s bundle=%s", session.peer_id, session.verdict, path)
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
    from hive.agent.memory import build_memory
    from hive.extraction.ner import get_default_backend
    from hive.llm.client import build_client
    from hive.sandbox.runner import PlaywrightDockerRunner

    client = build_client(settings)
    runner = PlaywrightDockerRunner()
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
