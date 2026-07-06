"""L2 reason node + client tests, fully offline."""

import time

from hive.agent.graph_nodes import reason_and_reply
from hive.llm.client import ChatMessage, Tier
from hive.llm.router import RouteInputs
from hive.state import Message, SessionState
from tests.fakes import FakeBackend, fake_client


def _session() -> SessionState:
    s = SessionState(peer_id=1, persona="confused_elderly")
    s.messages.append(Message(role="stranger", text="you won a prize!", ts=time.time(), msg_id=0))
    return s


def test_reason_returns_reply_and_cheap_tier_by_default():
    reply, tier = reason_and_reply(_session(), fake_client("aiyo really ah?"))
    assert reply == "aiyo really ah?"
    assert tier is Tier.CHEAP


def test_reason_escalates_on_injection():
    _, tier = reason_and_reply(
        _session(), fake_client(), route_inputs=RouteInputs(injection_flagged=True)
    )
    assert tier is Tier.STRONG


def test_client_logs_usage_and_selects_model_by_tier():
    backend = FakeBackend()
    client = __import__("hive.llm.client", fromlist=["LLMClient"]).LLMClient(
        backend, {Tier.CHEAP: "glm-5.1:cloud", Tier.STRONG: "glm-5.2:cloud", Tier.LIGHT: "x"}
    )
    resp = client.complete([ChatMessage("user", "hi")], tier=Tier.STRONG)
    assert resp.model == "glm-5.2:cloud"
    assert resp.completion_tokens == 5
    assert backend.calls[0]["model"] == "glm-5.2:cloud"


def test_persona_system_prompt_present_in_call():
    backend = FakeBackend()
    from hive.llm.client import LLMClient

    client = LLMClient(backend, {Tier.CHEAP: "m", Tier.STRONG: "m", Tier.LIGHT: "m"})
    reason_and_reply(_session(), client)
    system_msg = backend.calls[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "never reveal you are an ai" in system_msg["content"].lower()
