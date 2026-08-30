"""Provider response hardening tests for the Ollama-compatible client."""

from __future__ import annotations

import httpx
import pytest

from hive.llm.client import LLMResponseError, OllamaBackend, extract_chat_text


def _response(content: str, finish_reason: str) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }


def test_backend_retries_transient_model_loading_response() -> None:
    replies = [
        _response("", "load"),
        _response("", "load"),
        _response("READY", "stop"),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=replies.pop(0))

    delays: list[float] = []
    backend = OllamaBackend(
        "https://ollama.example/v1",
        "test-key",
        max_load_retries=2,
        load_retry_s=0.25,
        sleeper=delays.append,
    )
    backend._client.close()
    backend._client = httpx.Client(
        base_url="https://ollama.example/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = backend.chat("test-model", [{"role": "user", "content": "test"}])
    finally:
        backend.close()

    assert extract_chat_text(result) == "READY"
    assert len(requests) == 3
    assert delays == [0.25, 0.5]


def test_backend_fails_when_model_never_leaves_loading_state() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response("", "load"))

    backend = OllamaBackend(
        "https://ollama.example/v1",
        "test-key",
        max_load_retries=1,
        load_retry_s=0,
    )
    backend._client.close()
    backend._client = httpx.Client(
        base_url="https://ollama.example/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(LLMResponseError, match="remained in provider loading state"):
            backend.chat("test-model", [{"role": "user", "content": "test"}])
    finally:
        backend.close()


def test_backend_retries_transient_transport_timeout() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, json=_response("RECOVERED", "stop"))

    delays: list[float] = []
    backend = OllamaBackend(
        "https://ollama.example/v1",
        "test-key",
        max_load_retries=1,
        load_retry_s=0.25,
        sleeper=delays.append,
    )
    backend._client.close()
    backend._client = httpx.Client(
        base_url="https://ollama.example/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = backend.chat("test-model", [{"role": "user", "content": "test"}])
    finally:
        backend.close()

    assert extract_chat_text(result) == "RECOVERED"
    assert calls == 2
    assert delays == [0.25]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "completion choice"),
        (_response("", "stop"), "empty assistant content"),
    ],
)
def test_extract_chat_text_rejects_unusable_success_payloads(
    payload: dict, message: str
) -> None:
    with pytest.raises(LLMResponseError, match=message):
        extract_chat_text(payload)
