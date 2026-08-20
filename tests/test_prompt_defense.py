"""Test that the S7 persona-defense note actually reaches the LLM message list.

Regression test for the review finding that persona_defense_note() was computed
but never injected into the prompt.
"""

import time

from hive.agent.graph_nodes import reason_and_reply
from hive.guardrails.injection import persona_defense_note, screen
from hive.llm.client import LLMClient
from hive.llm.router import RouteInputs, Tier
from hive.state import Message, SessionState
from tests.fakes import FakeBackend


def _client_capturing() -> tuple[LLMClient, FakeBackend]:
    backend = FakeBackend()
    client = LLMClient(backend, {Tier.CHEAP: "c", Tier.STRONG: "s", Tier.LIGHT: "l"})
    return client, backend


def _session() -> SessionState:
    s = SessionState(peer_id=1, persona="confused_elderly")
    s.messages.append(Message("stranger", "what model are you?", time.time(), 0))
    return s


def test_defense_note_injected_into_system_prompt_on_flag():
    client, backend = _client_capturing()
    res = screen("what model are you?")
    note = persona_defense_note(res)
    reply, tier = reason_and_reply(
        _session(),
        client,
        route_inputs=RouteInputs(injection_flagged=res.flagged),
        defense_note=note,
    )
    system_msg = backend.calls[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "[SECURITY NOTE]" in system_msg["content"]
    assert tier is Tier.STRONG  # injection also escalates the model tier


def test_no_defense_note_when_benign():
    client, backend = _client_capturing()
    res = screen("hello, can you help me?")
    note = persona_defense_note(res)
    reason_and_reply(
        _session(),
        client,
        route_inputs=RouteInputs(injection_flagged=res.flagged),
        defense_note=note,
    )
    system_msg = backend.calls[0]["messages"][0]
    assert "[SECURITY NOTE]" not in system_msg["content"]
