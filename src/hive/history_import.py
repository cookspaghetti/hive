"""Import a prepared takeover-history JSON record into the configured store."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from hive.audit import configure_audit
from hive.config import load_settings
from hive.history import build_history_store


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args(argv)
    record = json.loads(args.record.read_text(encoding="utf-8"))
    settings = load_settings()
    audit_path = Path(settings.audit_path).with_name("replays.jsonl")
    configure_audit(audit_path, settings.database_url)
    store = build_history_store("evidence/history", settings.database_url)
    imported = store.import_record(record)
    print(imported["id"])
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
