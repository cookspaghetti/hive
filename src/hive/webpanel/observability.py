"""Small in-memory observation buffers for the localhost control panel.

The forensic source of truth remains the evidence vault.  These buffers are
deliberately bounded, process-local, and redacted; they exist only to make the
operator UI useful while HIVE is running.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|token|password|passphrase)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:sk|hf)_[A-Za-z0-9_-]{12,}\b"),
)


def _redact(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}[redacted]" if match.lastindex else "[redacted]",
            text,
        )
    return text[:1200]


class ObservationHub(logging.Handler):
    """Capture bounded operational logs plus explicit operator events."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__(level=logging.INFO)
        self._logs: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._sequence = 0

    def _next(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def emit(self, record: logging.LogRecord) -> None:
        try:
            row = {
                "id": self._next(),
                "ts": record.created,
                "level": record.levelname.lower(),
                "logger": record.name,
                "peer_id": getattr(record, "peer_id", None),
                "message": _redact(record.getMessage()),
            }
            with self._lock:
                self._logs.append(row)
        except Exception:
            self.handleError(record)

    def event(
        self,
        category: str,
        title: str,
        detail: str = "",
        *,
        peer_id: int | None = None,
        severity: str = "info",
    ) -> None:
        row = {
            "id": self._next(),
            "ts": time.time(),
            "category": _redact(category),
            "title": _redact(title),
            "detail": _redact(detail),
            "peer_id": peer_id,
            "severity": severity if severity in {"info", "success", "warning", "error"} else "info",
        }
        with self._lock:
            self._events.append(row)

    def logs(self, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._logs if row["id"] > after]
        return rows[-max(1, min(limit, 500)) :]

    def events(self, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._events if row["id"] > after]
        return rows[-max(1, min(limit, 500)) :]


_HUB = ObservationHub()
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


def get_observation_hub() -> ObservationHub:
    global _INSTALLED
    with _INSTALL_LOCK:
        if not _INSTALLED:
            logging.getLogger("hive").addHandler(_HUB)
            _INSTALLED = True
    return _HUB
