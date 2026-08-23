"""Live verification for every configured HIVE model role."""

from __future__ import annotations

import json
import time
from typing import Any

from hive.config import Settings, load_settings
from hive.llm.client import ChatBackend, OllamaBackend, extract_chat_text


def verify(settings: Settings, backend: ChatBackend) -> dict[str, Any]:
    roles = {
        "cheap": settings.llm_model_cheap,
        "strong": settings.llm_model_strong,
        "light": settings.llm_model_light,
        "vision": settings.vision_model,
    }
    models: dict[str, dict[str, Any]] = {}
    for model in dict.fromkeys(roles.values()):
        started = time.perf_counter()
        try:
            raw = backend.chat(
                model,
                [{"role": "user", "content": "Reply with exactly READY."}],
                temperature=0.0,
                max_tokens=512,
            )
            text = extract_chat_text(raw)
            raw_usage = raw.get("usage")
            usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
            models[model] = {
                "ok": True,
                "latency_s": round(time.perf_counter() - started, 3),
                "response_chars": len(text),
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
            }
        except Exception as exc:  # noqa: BLE001 - report every configured model
            models[model] = {
                "ok": False,
                "latency_s": round(time.perf_counter() - started, 3),
                "error": str(exc),
            }
    return {
        "ok": all(row["ok"] for row in models.values()),
        "roles": roles,
        "models": models,
    }


def main() -> None:
    settings = load_settings()
    backend = OllamaBackend(settings.llm_base_url, settings.llm_api_key)
    try:
        result = verify(settings, backend)
    finally:
        backend.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
