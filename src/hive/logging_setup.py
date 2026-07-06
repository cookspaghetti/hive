"""Central logging configuration for HIVE.

Design goals:
- One place to configure format/level, called once at startup.
- Every module gets a logger via `get_logger(__name__)`.
- Per-session correlation: use `bind_session(peer_id)` to tag records so logs
  from concurrent conversations can be told apart.
- Meaningful events (LLM calls, route decisions, HVI extraction, verdict
  changes, sandbox runs, lifecycle transitions) are logged by the modules that
  own them, using structured `extra` fields.

Note: forensic-grade, tamper-evident records live in the Evidence Vault (L5),
NOT here. These logs are for operation/debugging/evaluation.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

_LEVEL_DEFAULT = "INFO"
_configured = False

# Per-conversation correlation id, set by the orchestrator per session.
_peer_ctx: ContextVar[int | None] = ContextVar("hive_peer_id", default=None)


class _PeerFilter(logging.Filter):
    """Injects the current session's peer id into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.peer_id = _peer_ctx.get()
        return True


def configure_logging(level: str = _LEVEL_DEFAULT) -> None:
    """Idempotently configure root logging. Call once at startup."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_PeerFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s [peer=%(peer_id)s] %(name)s: %(message)s"
        )
    )
    root = logging.getLogger("hive")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("hive") else f"hive.{name}")


def bind_session(peer_id: int | None):
    """Bind the current peer id for log correlation. Returns the ctx token so
    the caller can reset it when the turn/session ends."""
    return _peer_ctx.set(peer_id)


def reset_session(token) -> None:
    _peer_ctx.reset(token)
