"""CLI for transcript-preserving re-analysis of archived takeovers."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hive.audit import configure_audit
from hive.config import load_settings
from hive.extraction.media import describe_image
from hive.history import build_history_store
from hive.llm.client import build_vision_client
from hive.logging_setup import configure_logging
from hive.replay import replay_history_record
from hive.runtime import build_engine


def _archived_record(audit_path: Path, peer_id: int) -> dict[str, Any]:
    matches = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("action") == "takeover_history_archived"
            and int(event.get("peer_id") or 0) == peer_id
            and isinstance(event.get("payload", {}).get("record"), dict)
        ):
            matches.append(event["payload"]["record"])
    if not matches:
        raise LookupError(f"no archived takeover found for peer {peer_id}")
    return max(matches, key=lambda item: float(item.get("ended_ts") or 0))


def _attachments(values: list[str]) -> dict[int, Path]:
    attachments: dict[int, Path] = {}
    for value in values:
        raw_message_id, separator, raw_path = value.partition("=")
        if not separator:
            raise ValueError("attachment must use MESSAGE_ID=PATH")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        attachments[int(raw_message_id)] = path
    return attachments


def _persist_media(session, attachments: dict[int, Path], media_root: Path) -> None:
    by_id = {message.msg_id: message for message in session.messages}
    destination_root = media_root / session.session_id
    for message_id, source in attachments.items():
        destination_root.mkdir(parents=True, exist_ok=True)
        safe_name = source.name.replace("/", "_").replace("\\", "_")
        destination = destination_root / f"{message_id}_{safe_name}"
        shutil.copy2(source, destination)
        message = by_id[message_id]
        message.media_path = str(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("peer_id", type=int)
    parser.add_argument(
        "--attach-image",
        action="append",
        default=[],
        metavar="MESSAGE_ID=PATH",
    )
    parser.add_argument("--no-ner", action="store_true")
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument("--history-root", type=Path, default=Path("evidence/history"))
    parser.add_argument("--migrate-legacy-ids", action="store_true")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    live_audit_path = Path(settings.audit_path)
    record = _archived_record(live_audit_path, args.peer_id)
    replay_audit_path = live_audit_path.with_name("replays.jsonl")
    configure_audit(replay_audit_path, settings.database_url)
    configure_logging(settings.log_level)
    attachments = _attachments(args.attach_image)
    descriptions: dict[int, str] = {}
    if attachments and not args.no_vision:
        vision = build_vision_client(settings)
        descriptions = {
            message_id: describe_image(str(path), vision)
            for message_id, path in attachments.items()
        }
    isolated = settings.model_copy(update={"use_semantic_memory": False})
    engine = build_engine(isolated, load_ner=not args.no_ner)
    replayed = replay_history_record(
        record,
        engine,
        media_paths=attachments,
        media_descriptions=descriptions,
    )
    _persist_media(replayed, attachments, Path(settings.media_path))
    history = build_history_store(args.history_root, settings.database_url)
    migrated = (
        history.migrate_legacy_ids()
        if args.migrate_legacy_ids and hasattr(history, "migrate_legacy_ids")
        else {}
    )
    archived = history.archive(
        replayed,
        ended_ts=float(record.get("ended_ts") or 0) or None,
        status="reanalyzed",
    )
    print(
        json.dumps(
            {
                "history_id": archived["id"],
                "replay_of": replayed.replay_of,
                "peer_id": replayed.peer_id,
                "verdict": replayed.verdict,
                "score": round(replayed.verdict_score, 4),
                "turns": replayed.turn_count,
                "exchanges": replayed.exchange_count,
                "indicators": archived["hvi_items"],
                "media_analysis": replayed.media_analysis,
                "migrated_history_ids": migrated,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
