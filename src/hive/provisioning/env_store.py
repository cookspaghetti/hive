"""Small, defensive `.env` store used by the setup panel."""

from __future__ import annotations

import errno
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

ALLOWED_KEYS = {
    "HF_TOKEN",
    "HIVE_CONTROL_BOT_TOKEN",
    "HIVE_CASE_EMBEDDING_MODEL",
    "HIVE_CASE_SIMILARITY_THRESHOLD",
    "HIVE_ABUSEIPDB_API_KEY",
    "HIVE_DEFAULT_PERSONA",
    "HIVE_LLM_API_KEY",
    "HIVE_LLM_BASE_URL",
    "HIVE_LLM_MODEL_CHEAP",
    "HIVE_LLM_MODEL_LIGHT",
    "HIVE_LLM_MODEL_STRONG",
    "HIVE_LOG_LEVEL",
    "HIVE_MAX_SESSION_MINUTES",
    "HIVE_MAX_TURNS",
    "HIVE_INBOX_DEBOUNCE_S",
    "HIVE_INBOX_MAX_WAIT_S",
    "HIVE_OPERATOR_ID",
    "HIVE_OPERATOR_NAME",
    "HIVE_PANEL_HOST",
    "HIVE_PANEL_ALLOW_NON_LOOPBACK",
    "HIVE_PANEL_PORT",
    "HIVE_PANEL_TOKEN",
    "HIVE_QDRANT_URL",
    "HIVE_RETENTION_ACTIVE_REVIEW_DAYS",
    "HIVE_RETENTION_DEMO_DAYS",
    "HIVE_RETENTION_EVALUATION_DAYS",
    "HIVE_RETENTION_MEDIA_DAYS",
    "HIVE_SESSION_PASSPHRASE",
    "HIVE_SIGNING_KEY_PATH",
    "HIVE_TG_API_HASH",
    "HIVE_TG_API_ID",
    "HIVE_TG_PHONE",
    "HIVE_TG_SESSION_PATH",
    "HIVE_THREAT_INTEL_TIMEOUT_S",
    "HIVE_USE_CASE_SIMILARITY",
    "HIVE_VIRUSTOTAL_API_KEY",
    "HIVE_VISION_MODEL",
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SENSITIVE_SUFFIXES = ("_API_HASH", "_API_KEY", "_PASSPHRASE", "_TOKEN")


class EnvStore:
    """Read and update one HIVE `.env`, atomically where the mount permits it."""

    def __init__(self, path: str | Path = ".env") -> None:
        self.path = Path(path)

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return {
            key: value or ""
            for key, value in dotenv_values(self.path, encoding="utf-8").items()
            if key and value is not None
        }

    def save(self, values: Mapping[str, object]) -> None:
        clean = {key: self._clean(key, value) for key, value in values.items()}
        lines = self._read_lines()
        remaining = dict(clean)
        output: list[str] = []

        for line in lines:
            match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=", line)
            if match and match.group(1) in clean:
                key = match.group(1)
                output.append(f"{key}={_quote(clean[key])}\n")
                remaining.pop(key, None)
            else:
                output.append(line if line.endswith("\n") else line + "\n")

        if remaining and output and output[-1].strip():
            output.append("\n")
        output.extend(f"{key}={_quote(value)}\n" for key, value in remaining.items())
        self._atomic_write("".join(output))

    def _clean(self, key: str, value: object) -> str:
        if not _KEY_RE.fullmatch(key) or key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported environment key: {key}")
        text = str(value).strip()
        if "\r" in text or "\n" in text or "\x00" in text:
            raise ValueError(f"invalid characters in {key}")
        if key.endswith(_SENSITIVE_SUFFIXES) and not text.isascii():
            raise ValueError(f"{key} must contain ASCII characters only")
        return text

    def _read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text(encoding="utf-8-sig", errors="replace").splitlines(keepends=True)

    def _atomic_write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        old_mode = self.path.stat().st_mode if self.path.exists() else None
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, old_mode & 0o777 if old_mode is not None else 0o600)
            try:
                os.replace(temp_name, self.path)
            except OSError as exc:
                # Docker cannot replace the inode of a single-file bind mount.
                # Preserve that deployment's persistence with a flushed in-place
                # write; other filesystem errors must remain visible.
                if exc.errno != errno.EBUSY or not self.path.exists():
                    raise
                with self.path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _quote(value: str) -> str:
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:+@-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
