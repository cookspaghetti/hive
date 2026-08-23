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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from hive.audit import audit_event
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

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kw: Any,
    ) -> dict[str, Any]: ...


class LLMResponseError(RuntimeError):
    """The provider replied successfully but did not return usable model text."""


def extract_chat_text(raw: dict[str, Any]) -> str:
    """Return assistant text or fail clearly on malformed/empty provider replies."""
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMResponseError("LLM response did not contain a completion choice")
    choice = choices[0]
    message = choice.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip():
        reason = str(choice.get("finish_reason") or "unknown")
        raise LLMResponseError(f"LLM returned empty assistant content (finish_reason={reason})")
    return text


def _is_loading_response(raw: dict[str, Any]) -> bool:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return choice.get("finish_reason") == "load" and not str(content or "").strip()


class OllamaBackend:
    """Real HTTP backend against Ollama Cloud's OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        *,
        max_load_retries: int = 3,
        load_retry_s: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self._max_load_retries = max(0, max_load_retries)
        self._load_retry_s = max(0.0, load_retry_s)
        self._sleeper = sleeper

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kw: Any,
    ) -> dict[str, Any]:
        for attempt in range(self._max_load_retries + 1):
            resp = self._client.post(
                "/chat/completions",
                json={"model": model, "messages": messages, **kw},
            )
            resp.raise_for_status()
            raw = cast(dict[str, Any], resp.json())
            if not _is_loading_response(raw):
                return raw
            if attempt == self._max_load_retries:
                break
            delay = self._load_retry_s * (attempt + 1)
            log.warning(
                "LLM model loading: model=%s retry=%d/%d delay=%.1fs",
                model,
                attempt + 1,
                self._max_load_retries,
                delay,
            )
            self._sleeper(delay)
        raise LLMResponseError(
            f"LLM model {model!r} remained in provider loading state after "
            f"{self._max_load_retries + 1} attempt(s)"
        )

    def close(self) -> None:
        self._client.close()


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
        audit_event(
            "llm_request",
            "chat_completion_requested",
            component="llm",
            payload={
                "model": model,
                "tier": tier.value,
                "temperature": temperature,
                "messages": payload,
            },
        )
        started = time.perf_counter()
        try:
            raw = self._backend.chat(model, payload, temperature=temperature)
        except Exception as exc:  # noqa: BLE001 - log and re-raise for caller
            audit_event(
                "llm_error",
                "chat_completion_failed",
                component="llm",
                payload={"model": model, "tier": tier.value, "error": str(exc)},
                level="error",
            )
            log.error("LLM call failed: model=%s tier=%s err=%s", model, tier.value, exc)
            raise
        latency = time.perf_counter() - started

        text = extract_chat_text(raw)
        usage = raw.get("usage", {})
        pt = int(usage.get("prompt_tokens", 0))
        ct = int(usage.get("completion_tokens", 0))
        audit_event(
            "llm_response",
            "chat_completion_received",
            component="llm",
            payload={
                "model": model,
                "tier": tier.value,
                "text": text,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "latency_s": latency,
            },
        )
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


class VisionClient:
    """Multimodal client for the L3 vision fallback (Qwen via Ollama).

    Sends an image (as a base64 data URL) plus a prompt using the OpenAI-
    compatible multimodal message shape, and returns the model's text. Shares
    the injectable backend so tests can run offline.
    """

    def __init__(self, backend: ChatBackend, model: str) -> None:
        self._backend = backend
        self._model = model

    def describe(self, data_url: str, prompt: str) -> str:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        audit_event(
            "vision_request",
            "vision_description_requested",
            component="llm.vision",
            payload={"model": self._model, "messages": messages},
        )
        try:
            raw = self._backend.chat(self._model, messages, temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            audit_event(
                "vision_error",
                "vision_description_failed",
                component="llm.vision",
                payload={"model": self._model, "error": str(exc)},
                level="error",
            )
            log.error("vision call failed: model=%s err=%s", self._model, exc)
            raise
        text = extract_chat_text(raw)
        audit_event(
            "vision_response",
            "vision_description_received",
            component="llm.vision",
            payload={"model": self._model, "text": text},
        )
        log.info("vision call ok: model=%s chars=%d", self._model, len(text))
        return str(text)


def build_client(settings: Any) -> LLMClient:
    """Construct a real Ollama-backed client from Settings."""
    backend = OllamaBackend(settings.llm_base_url, settings.llm_api_key)
    models = {
        Tier.CHEAP: settings.llm_model_cheap,
        Tier.STRONG: settings.llm_model_strong,
        Tier.LIGHT: settings.llm_model_light,
    }
    return LLMClient(backend, models)


def build_vision_client(settings: Any) -> VisionClient:
    """Construct the Ollama-backed vision client from Settings."""
    backend = OllamaBackend(settings.llm_base_url, settings.llm_api_key)
    return VisionClient(backend, settings.vision_model)
