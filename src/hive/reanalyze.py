"""CLI for transcript-preserving re-analysis of archived takeovers."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hive.analysis_runs import (
    analysis_run_record,
    build_analysis_run_store,
    model_manifest,
)
from hive.audit import configure_audit
from hive.case_intelligence import build_case_intelligence_store, build_case_profile
from hive.config import load_settings
from hive.extraction.media import describe_image
from hive.history import build_history_store
from hive.llm.client import build_vision_client
from hive.logging_setup import configure_logging
from hive.replay import replay_history_record
from hive.runtime import build_engine


def _archived_records(history, selector: str) -> list[dict[str, Any]]:
    if selector.lower() == "all":
        records = [
            record
            for row in history.list()
            if (record := history.get(str(row["id"]))) is not None
            and not record.get("replay_of")
        ]
        if not records:
            raise LookupError("no archived takeovers found")
        return records
    direct = history.get(selector)
    if direct is not None:
        return [direct]
    try:
        peer_id = int(selector)
    except ValueError as exc:
        raise LookupError(f"no archived takeover found for {selector}") from exc
    matches = [row for row in history.list() if int(row.get("peer_id") or 0) == peer_id]
    if not matches:
        raise LookupError(f"no archived takeover found for peer {peer_id}")
    record = history.get(str(matches[0]["id"]))
    if record is None:
        raise LookupError(f"archived takeover {matches[0]['id']} disappeared")
    return [record]


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
    parser.add_argument("selector", help="history UUID, legacy ID, peer ID, or 'all'")
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
    replay_audit_path = live_audit_path.with_name("replays.jsonl")
    configure_audit(replay_audit_path, settings.database_url)
    configure_logging(settings.log_level)
    history = build_history_store(args.history_root, settings.database_url)
    records = _archived_records(history, args.selector)
    attachments = _attachments(args.attach_image)
    if attachments and len(records) != 1:
        raise ValueError("attachments require one specific archived case")
    descriptions: dict[int, str] = {}
    if attachments and not args.no_vision:
        vision = build_vision_client(settings)
        descriptions = {
            message_id: describe_image(str(path), vision)
            for message_id, path in attachments.items()
        }
    engine = build_engine(settings, load_ner=not args.no_ner)
    migrated = (
        history.migrate_legacy_ids()
        if args.migrate_legacy_ids and hasattr(history, "migrate_legacy_ids")
        else {}
    )
    analysis_store = build_analysis_run_store(
        args.history_root / "analysis_runs",
        settings.database_url,
    )
    case_store = build_case_intelligence_store(
        args.history_root.parent / "cases",
        settings.database_url,
        qdrant_url=settings.qdrant_url,
        enable_semantic=settings.use_case_similarity,
        similarity_threshold=settings.case_similarity_threshold,
        embedding_model=settings.case_embedding_model,
    )
    outputs = []
    for record in records:
        replayed = replay_history_record(
            record,
            engine,
            media_paths=attachments,
            media_descriptions=descriptions,
        )
        _persist_media(replayed, attachments, Path(settings.media_path))
        analysis = analysis_run_record(
            record,
            replayed,
            models=model_manifest(settings),
        )
        analysis_store.create(analysis)
        case_store.index(build_case_profile(record, analysis))
        outputs.append(
            {
                "history_id": record["id"],
                "analysis_run_id": analysis["id"],
                "peer_id": replayed.peer_id,
                "verdict": replayed.verdict,
                "score": round(replayed.verdict_score, 4),
                "turns": replayed.turn_count,
                "exchanges": replayed.exchange_count,
                "indicators": analysis["hvi_items"],
                "media_analysis": replayed.media_analysis,
            }
        )
    payload = outputs[0] if len(outputs) == 1 else {"count": len(outputs), "analyses": outputs}
    payload["migrated_history_ids"] = migrated
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
