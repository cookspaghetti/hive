"""Offline fakes for tests (no network, no API key)."""

from __future__ import annotations

from hive.llm.client import LLMClient
from hive.llm.router import Tier


class FakeBackend:
    """Deterministic chat backend. Echoes a canned reply and fake usage."""

    def __init__(self, reply: str = "ok lah, who is this ah?") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, model: str, messages: list[dict], **kw) -> dict:
        self.calls.append({"model": model, "messages": messages, **kw})
        return {
            "choices": [{"message": {"role": "assistant", "content": self.reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


def fake_client(reply: str = "ok lah, who is this ah?") -> LLMClient:
    models = {Tier.CHEAP: "glm-5.1:cloud", Tier.STRONG: "glm-5.2:cloud", Tier.LIGHT: "glm-5.1:cloud"}
    return LLMClient(FakeBackend(reply), models)
