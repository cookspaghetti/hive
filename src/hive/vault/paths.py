"""Safe, collision-free evidence bundle paths."""

from __future__ import annotations

import re
import time
from pathlib import Path

from hive.state import SessionState

_BUNDLE_NAME = re.compile(r"^bundle_(-?\d+)(?:_(\d+))?\.pdf$")


def new_bundle_path(root: str | Path, session: SessionState) -> Path:
    """Return a unique output path without reusing another session's bundle."""
    return Path(root) / f"bundle_{session.peer_id}_{time.time_ns()}.pdf"


def parse_bundle_name(filename: str) -> tuple[int, int | None] | None:
    """Parse current and legacy bundle filenames without accepting paths."""
    match = _BUNDLE_NAME.fullmatch(filename)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None
