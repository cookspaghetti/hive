"""Ollama Cloud LLM client (OpenAI-compatible /v1/chat/completions).

Endpoint and auth verified against Ollama docs:
  base_url = https://ollama.com/v1 ; Bearer <OLLAMA_API_KEY>.

Logging: every call logs model, tier, latency, and token usage at INFO — the
data the S9 evaluation needs for cost-per-session. Failures log at ERROR.
The client is model-agnostic; the tier->model mapping is injected so tests can
run fully offline with a fake.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from hive.llm.router import Tier
from hive.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    tier: Tier
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0


class ChatBackend(Protocol):
    """Minimal transport seam so a fake backend can be injected in tests."""

    def chat(self, model: str, messages: list[dict], **kw) -> dict: ...


class OllamaBackend:
    """Real HTTP backend against Ollama Cloud's OpenAI-compatible endpoint."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 60.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def chat(self, model: str, messages: list[dict], **kw) -> dict:
        resp = self._client.post(
            "/chat/completions",
            json={"model": model, "messages": messages, **kw},
        )
        resp.raise_for_status()
        return resp.json()


class LLMClient:
    """Tier-aware chat client. Maps Tier -> concrete model, calls the backend,
    and logs the operationally meaningful details."""

    def __init__(self, backend: ChatBackend, models: dict[Tier, str]) -> None:
        self._backend = backend
        self._models = models

    def complete(
        self,
        messages: list[ChatMessage],
        tier: Tier = Tier.CHEAP,
        temperature: float = 0.8,
    ) -> LLMResponse:
        model = self._models[tier]
        payload = [{"role": m.role, "content": m.content} for m in messages]
        started = time.perf_counter()
        try:
            raw = self._backend.chat(model, payload, temperature=temperature)
        except Exception as exc:  # noqa: BLE001 - log and re-raise for caller
            log.error("LLM call failed: model=%s tier=%s err=%s", model, tier.value, exc)
            raise
        latency = time.perf_counter() - started

        text = raw["choices"][0]["message"]["content"]
        usage = raw.get("usage", {})
        pt = int(usage.get("prompt_tokens", 0))
        ct = int(usage.get("completion_tokens", 0))
        log.info(
            "LLM call ok: model=%s tier=%s latency=%.2fs prompt_tok=%d completion_tok=%d",
            model,
            tier.value,
            latency,
            pt,
            ct,
        )
        return LLMResponse(
            text=text,
            model=model,
            tier=tier,
            prompt_tokens=pt,
            completion_tokens=ct,
            latency_s=latency,
        )


def build_client(settings) -> LLMClient:
    """Construct a real Ollama-backed client from Settings."""
    backend = OllamaBackend(settings.llm_base_url, settings.llm_api_key)
    models = {
        Tier.CHEAP: settings.llm_model_cheap,
        Tier.STRONG: settings.llm_model_strong,
        Tier.LIGHT: settings.llm_model_light,
    }
    return LLMClient(backend, models)
