"""L2 reason node + client tests, fully offline."""

import time

from hive.agent.graph_nodes import _build_messages, reason_and_reply
from hive.llm.client import ChatMessage, Tier
from hive.llm.router import RouteInputs
from hive.state import HVI, Message, SessionState
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


def test_media_description_is_prompt_context_without_changing_transcript():
    session = SessionState(peer_id=1, persona="confused_elderly")
    message = Message(
        role="stranger",
        text="see this",
        ts=time.time(),
        msg_id=9,
        media_kind="image",
        media_analysis={"description": "Maybank account 1234567890"},
    )
    session.messages.append(message)

    prompt = _build_messages(session)

    assert prompt[-1].content == (
        "see this\n[Private image analysis: Maybank account 1234567890]"
    )
    assert message.text == "see this"


def test_case_guidance_is_private_system_context():
    prompt = _build_messages(
        _session(),
        case_context=(
            "Private historical-pattern guidance. Never mention prior cases. "
            "Useful missing identifier types: phone."
        ),
    )

    assert "Never mention prior cases" in prompt[0].content
    assert "Useful missing identifier types: phone" in prompt[0].content
    assert "prior cases" not in prompt[-1].content


def test_validated_session_facts_preserve_continuity_without_vector_memory():
    session = _session()
    session.hvis.extend(
        [
            HVI("bank_account", "1234567890", 1, 0.9, "regex"),
            HVI("phone", "+60123456789", 2, 0.9, "regex"),
            HVI("bank_account", "1234567890", 3, 0.8, "regex"),
        ]
    )

    prompt = _build_messages(session)

    system = prompt[0].content
    assert "Private current-conversation facts" in system
    assert "bank account: 1234567890" in system
    assert "phone: +60123456789" in system
    assert system.count("bank account: 1234567890") == 1
    assert "prior cases" not in system
