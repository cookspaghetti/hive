"""Offline tests for the configured model-role verifier."""

from hive.config import Settings
from hive.verify_models import verify


class FakeBackend:
    def __init__(self, failing: str = "") -> None:
        self.failing = failing
        self.calls: list[str] = []

    def chat(self, model, messages, **kwargs):
        self.calls.append(model)
        if model == self.failing:
            raise RuntimeError("provider unavailable")
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "READY"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }


def _settings() -> Settings:
    return Settings(
        llm_model_cheap="cheap-model",
        llm_model_strong="strong-model",
        llm_model_light="cheap-model",
        vision_model="vision-model",
    )


def test_verify_calls_each_unique_model_once():
    backend = FakeBackend()

    result = verify(_settings(), backend)

    assert result["ok"] is True
    assert backend.calls == ["cheap-model", "strong-model", "vision-model"]
    assert result["roles"]["light"] == "cheap-model"
    assert result["models"]["strong-model"]["completion_tokens"] == 1


def test_verify_reports_a_failing_model_without_skipping_the_rest():
    backend = FakeBackend(failing="strong-model")

    result = verify(_settings(), backend)

    assert result["ok"] is False
    assert result["models"]["strong-model"]["error"] == "provider unavailable"
    assert result["models"]["vision-model"]["ok"] is True
