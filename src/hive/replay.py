"""Deterministic re-analysis of an archived takeover transcript.

Replay preserves every recorded message and reruns extraction, URL sandboxing,
and verdict classification. It deliberately does not generate replacement
agent replies.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from hive.audit import audit_event
from hive.extraction.engine import extract_contextual_hvis, extract_hvis, merge_hvis
from hive.extraction.regex_rules import extract_regex
from hive.runtime import HiveEngine
from hive.sandbox.analyzer import analyze_url
from hive.state import Message, Phase, SessionState
from hive.verdict.classifier import classify_soft
from hive.verdict.engine import update_verdict


def _message(
    raw: dict[str, Any],
    media_paths: dict[int, Path],
) -> Message:
    message_id = int(raw.get("msg_id") or 0)
    path = media_paths.get(message_id)
    media_name = path.name if path else raw.get("media_name")
    return Message(
        role=str(raw.get("role") or "stranger"),
        text=str(raw.get("text") or ""),
        ts=float(raw.get("ts") or 0),
        msg_id=message_id,
        media_kind="image" if path else raw.get("media_kind"),
        media_name=media_name,
        media_mime=(mimetypes.guess_type(media_name)[0] if path and media_name else None)
        or raw.get("media_mime"),
        media_size=path.stat().st_size if path else raw.get("media_size"),
        media_path=str(path) if path else None,
        media_sha256=(hashlib.sha256(path.read_bytes()).hexdigest() if path else None),
        captured_ts=(float(raw["captured_ts"]) if raw.get("captured_ts") is not None else None),
        platform=str(raw.get("platform") or "telegram"),
        pre_takeover=bool(raw.get("pre_takeover", False)),
    )


def replay_history_record(
    record: dict[str, Any],
    engine: HiveEngine,
    *,
    media_paths: dict[int, str | Path] | None = None,
    media_descriptions: dict[int, str] | None = None,
) -> SessionState:
    """Rerun intelligence analysis over one immutable archived transcript."""
    resolved_media = {
        int(message_id): Path(path).resolve()
        for message_id, path in (media_paths or {}).items()
    }
    descriptions = {int(key): value for key, value in (media_descriptions or {}).items()}
    session = SessionState(
        peer_id=int(record["peer_id"]),
        persona=str(record.get("persona") or "confused_elderly"),
        phase=Phase.ACTIVE,
        started_ts=float(record.get("started_ts") or 0) or None,
        replay_of=str(record.get("id") or "") or None,
    )
    identity = record.get("peer_identity") or {}
    session.peer_display_name = str(identity.get("display_name") or "")
    session.peer_username = str(identity.get("username") or "")
    session.identity_observed_ts = (
        float(identity["observed_ts"]) if identity.get("observed_ts") is not None else None
    )
    batch: list[Message] = []
    sandboxed_urls: set[str] = set()
    analyzed_turns = 0
    turn_boundaries = {
        int(item["turn"])
        for item in record.get("signal_trail", [])
        if isinstance(item, dict) and isinstance(item.get("turn"), int)
    }

    def analyze_batch() -> None:
        nonlocal analyzed_turns
        if not batch:
            return
        batch_message_ids = {inbound.msg_id for inbound in batch}
        batch_hvis = []
        for inbound in batch:
            session.messages.append(inbound)
            hvis = extract_hvis(
                inbound.text,
                inbound.msg_id,
                ner_backend=engine.ner_backend,
            )
            description = descriptions.get(inbound.msg_id)
            if description:
                session.media_analysis.append(
                    {
                        "source_msg_id": inbound.msg_id,
                        "description": description,
                        "source": "vision",
                    }
                )
                visual_hvis = extract_regex(description, inbound.msg_id)
                for item in visual_hvis:
                    item.extractor = "vision"
                hvis.extend(visual_hvis)
            batch_hvis.extend(hvis)
        batch_hvis.extend(extract_contextual_hvis(session.messages, batch_message_ids))
        accepted = merge_hvis(session.hvis, batch_hvis)
        for item in accepted:
            if item.kind != "url" or item.value in sandboxed_urls:
                continue
            result = analyze_url(item.value, engine.sandbox_runner)
            session.sandbox_results.append(result)
            sandboxed_urls.add(item.value)
            audit_event(
                "sandbox",
                "url_analysis_failed" if result.get("error") else "url_analysis_completed",
                component="replay.sandbox",
                payload=result,
                peer_id=session.peer_id,
                session_id=session.session_id,
                level="error" if result.get("error") else "info",
            )
        session.turn_count += len(batch)
        analyzed_turns = session.turn_count
        session.exchange_count += 1
        assessment = classify_soft(session, engine.agent_client)
        update_verdict(
            session,
            soft=assessment.scores,
            source_messages=list(batch),
            soft_evidence=assessment.evidence,
        )
        batch.clear()

    for raw in record.get("messages", []):
        message = _message(raw, resolved_media)
        if message.role == "stranger":
            batch.append(message)
            if analyzed_turns + len(batch) in turn_boundaries:
                analyze_batch()
            continue
        if not turn_boundaries:
            analyze_batch()
        session.messages.append(message)
    analyze_batch()
    session.phase = Phase.SEALED
    audit_event(
        "takeover_replay",
        "takeover_history_reanalyzed",
        component="replay",
        payload={
            "replay_of": session.replay_of,
            "turns": session.turn_count,
            "exchanges": session.exchange_count,
            "verdict": session.verdict,
            "score": session.verdict_score,
            "indicators": [
                {
                    "kind": item.kind,
                    "value": item.value,
                    "source_msg_id": item.source_msg_id,
                    "extractor": item.extractor,
                }
                for item in session.hvis
            ],
        },
        peer_id=session.peer_id,
        session_id=session.session_id,
    )
    return session
