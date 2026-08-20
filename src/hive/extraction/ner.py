"""GLiNER2 zero-shot NER (fyp.txt L3).

Labels the entity types we care about with no fine-tuning. Original
contribution: financial NER is absent from both reference frameworks.

The GLiNER2 model is heavy to load, so the model is injected behind a small
`NerBackend` protocol. Production uses `GlinerBackend` (lazy import of the
`gliner` package); tests inject a fake so they run offline with no model
download.
"""

from __future__ import annotations

import time
import warnings
from typing import Protocol

from hive.logging_setup import get_logger
from hive.state import HVI

log = get_logger(__name__)

# GLiNER labels -> our HVI.kind taxonomy.
LABEL_TO_KIND: dict[str, str] = {
    "bank name": "bank_name",
    "bank account number": "bank_account",
    "phone number": "phone",
    "telegram id": "telegram_id",
    "person name": "person_name",
    "company name": "organization",
    "organization": "organization",
    "location": "location",
    "url": "url",
}

DEFAULT_LABELS = list(LABEL_TO_KIND.keys())


class NerBackend(Protocol):
    """Returns a list of (label, text, score) spans for the input."""

    def predict(self, text: str, labels: list[str]) -> list[tuple[str, str, float]]: ...


class GlinerBackend:
    """Real GLiNER2 backend. Lazily imports `gliner` so the package is only
    required in production, not in tests."""

    def __init__(
        self,
        model_name: str = "urchade/gliner_multi-v2.1",
        threshold: float = 0.5,
    ) -> None:
        from gliner import GLiNER  # lazy import

        self._model = GLiNER.from_pretrained(model_name)
        self._threshold = threshold

    def predict(self, text: str, labels: list[str]) -> list[tuple[str, str, float]]:
        ents = self._model.predict_entities(text, labels, threshold=self._threshold)
        return [(e["label"], e["text"], float(e.get("score", 1.0))) for e in ents]


_DEFAULT_BACKEND: NerBackend | None = None


def get_default_backend(model_name: str = "urchade/gliner_multi-v2.1") -> NerBackend:
    """Lazily load and cache a single GLiNER backend for the process.

    Loading the model is expensive, so this is a process-wide singleton. Call
    once at startup (build_engine) to avoid a first-turn latency spike.
    """
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        started = time.perf_counter()
        log.info(
            "[startup][ner] INITIALIZING GLiNER model=%s first_load_may_download",
            model_name,
        )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"The `resume_download` argument is deprecated.*",
                category=UserWarning,
            )
            _DEFAULT_BACKEND = GlinerBackend(model_name)
        log.info(
            "[startup][ner] READY model=%s duration=%.2fs",
            model_name,
            time.perf_counter() - started,
        )
    return _DEFAULT_BACKEND


def extract_entities(
    text: str,
    source_msg_id: int,
    backend: NerBackend,
    labels: list[str] | None = None,
) -> list[HVI]:
    """Run NER over `text` and return typed HVIs."""
    labels = labels or DEFAULT_LABELS
    hvis: list[HVI] = []
    for label, value, score in backend.predict(text, labels):
        kind = LABEL_TO_KIND.get(label.lower(), label.lower().replace(" ", "_"))
        hvis.append(
            HVI(
                kind=kind,
                value=value.strip(),
                source_msg_id=source_msg_id,
                confidence=score,
                extractor="ner",
            )
        )
    log.info("L3 NER: msg=%d entities=%d", source_msg_id, len(hvis))
    return hvis
