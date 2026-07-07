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

from hive.extraction.engine import extract_hvis
from hive.extraction.ner import NerBackend
from hive.guardrails.injection import persona_defense_note, screen
from hive.llm.client import LLMClient
from hive.llm.router import RouteInputs
from hive.logging_setup import bind_session, get_logger, reset_session
from hive.middleware.pipeline import apply as apply_middleware
from hive.sandbox.analyzer import analyze_url
from hive.sandbox.runner import BrowserRunner
from hive.state import Message, Phase, SessionState
from hive.verdict.classifier import classify_soft
from hive.verdict.engine import update_verdict
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


@dataclass
class TurnOutput:
    text: str | None          # reply to send (None if handing back / no reply)
    delay_s: float = 0.0      # tarpit delay before sending
    handed_back: bool = False  # early-exit: conversation deemed benign
    verdict: str = "inconclusive"


@dataclass
class HiveEngine:
    agent_client: LLMClient
    sandbox_runner: BrowserRunner
    ner_backend: NerBackend | None = None
    enable_early_exit: bool = True
    early_exit_min_turns: int = 3   # don't bail before we've seen enough
    # Factory(peer_id) -> MemoryBackend. Defaults to the offline KeywordMemory.
    memory_factory: object = None
    _memories: dict = field(default_factory=dict)

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
        token = bind_session(session.peer_id)
        try:
            session.phase = Phase.ACTIVE
            session.messages.append(inbound)
            session.turn_count += 1
            chain.append({"event": "msg_in", "msg_id": inbound.msg_id, "text": inbound.text}, ts=inbound.ts)

            memory = self._memory_for(session.peer_id)
            memory.add("stranger", inbound.text)

            # 1. S7 guardrails
            screen_res = screen(inbound.text)

            # 2. L3 extraction
            hvis = extract_hvis(inbound.text, inbound.msg_id, ner_backend=self.ner_backend)
            for h in hvis:
                session.hvis.append(h)
                chain.append({"event": "hvi", "kind": h.kind, "value": h.value}, ts=time.time())

            # 3. L4 sandbox for any URL HVIs
            for h in [x for x in hvis if x.kind == "url"]:
                session.phase = Phase.PROBING
                result = analyze_url(h.value, self.sandbox_runner)
                session.sandbox_results.append(result)
                chain.append({"event": "sandbox", "url": h.value, "signal": result.get("verdict_signal")}, ts=time.time())
            session.phase = Phase.ACTIVE

            # 4. S6 verdict (soft signals via light-tier classifier)
            soft = classify_soft(session, self.agent_client)
            verdict = update_verdict(session, soft=soft)

            # 5. Safeguard: early-exit hand-back if benign
            if (
                self.enable_early_exit
                and verdict == "likely_benign"
                and session.turn_count >= self.early_exit_min_turns
            ):
                session.phase = Phase.CLOSING
                log.info("Safeguard: early-exit, handing conversation back (benign)")
                return TurnOutput(text=None, handed_back=True, verdict=verdict)

            # 6. L2 reason with model-tier routing (S7 escalates on injection)
            route_inputs = RouteInputs(
                injection_flagged=screen_res.flagged,
                eliciting_hvi=False,
            )
            from hive.agent.graph_nodes import reason_and_reply

            # L2 memory: surface earlier disclosures relevant to this message.
            recall = memory.recall(inbound.text)

            # S7: if an injection/bot-probe was seen, append the persona-defense
            # note to the system prompt so the agent stays in character.
            defense = persona_defense_note(screen_res)
            reply, tier = reason_and_reply(
                session,
                self.agent_client,
                recall=recall,
                route_inputs=route_inputs,
                defense_note=defense,
            )

            # 7. L1 middleware
            mw = apply_middleware(reply, session.persona, incoming_len=len(inbound.text))
            out_msg = Message(role="agent", text=mw.text, ts=time.time(), msg_id=inbound.msg_id + 1)
            session.messages.append(out_msg)
            memory.add("agent", reply)
            chain.append({"event": "msg_out", "text": mw.text}, ts=out_msg.ts)

            log.info(
                "turn done: tier=%s verdict=%s delay=%.1fs hvis+=%d",
                tier.value, verdict, mw.delay_s, len(hvis),
            )
            return TurnOutput(text=mw.text, delay_s=mw.delay_s, verdict=verdict)
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
        agent_client=client, sandbox_runner=runner, ner_backend=ner, memory_factory=memory_factory
    )
