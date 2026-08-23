"""Rebuild signed evaluation PDFs from retained structured run records.

This is intended for presentation-only renderer corrections. It preserves the
recorded transcript words, indicators, verdict, and run metrics while creating
a fresh synthetic hash chain and clearly marking the reseal in run metadata.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.config import load_settings
from hive.state import HVI, Message, Phase, SessionState
from hive.vault.bundle import build_bundle
from hive.vault.hashchain import HashChain
from hive.vault.package import verify_evidence_package


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _pdf_path(group: Path, record: dict[str, Any]) -> Path:
    package_name = Path(
        str(record.get("evidence_package") or "").replace("\\", "/")
    ).name
    if not package_name.endswith(".evidence.zip"):
        raise ValueError(f"run in {group} has no valid evidence-package path")
    return group / "evidence" / package_name.replace(".evidence.zip", ".pdf")


def _session_and_chain(
    record: dict[str, Any],
    *,
    started_ts: float,
) -> tuple[SessionState, HashChain]:
    archetype = str(record["archetype"])
    peer_id = int.from_bytes(archetype.encode("utf-8"), "little") % 1_000_000_000
    session = SessionState(
        peer_id=peer_id,
        persona=str(record["persona"]),
        phase=Phase.CLOSING,
    )
    session.started_ts = started_ts
    session.turn_count = int(record.get("turns") or 0)
    session.exchange_count = int(record.get("exchanges") or 0)
    session.verdict = str(record.get("verdict") or "inconclusive")
    session.verdict_score = float(record.get("verdict_score") or 0)
    scored_ids = {int(value) for value in record.get("scored_message_ids") or []}
    transcript = record.get("transcript") or []
    duration = max(0.0, float(record.get("duration_s") or 0))
    step = duration / max(1, len(transcript) - 1)
    inbound_id = 0
    outbound_id = -1
    chain = HashChain()

    for index, (speaker, text) in enumerate(transcript):
        timestamp = started_ts + index * step
        is_inbound = speaker == "scammer"
        message_id = inbound_id if is_inbound else outbound_id
        if is_inbound:
            inbound_id += 1
        else:
            outbound_id -= 1
        message = Message(
            role="stranger" if is_inbound else "agent",
            text=str(text),
            ts=timestamp,
            msg_id=message_id,
            captured_ts=timestamp if is_inbound else None,
            platform="simulation",
            pre_takeover=is_inbound and message_id in scored_ids,
        )
        session.messages.append(message)
        chain.append(
            {
                "event": "msg_in" if is_inbound else "msg_out",
                "msg_id": message_id,
                "text": message.text,
                "platform": "simulation",
                "pre_takeover": message.pre_takeover,
            },
            ts=timestamp,
        )

    session.hvis = [
        HVI(
            kind=str(item["kind"]),
            value=str(item["value"]),
            source_msg_id=int(item.get("source_msg_id") or 0),
            confidence=float(item.get("confidence") or 0),
            extractor=str(item.get("extractor") or "unknown"),
        )
        for item in record.get("hvi_items") or []
    ]
    for item in session.hvis:
        chain.append(
            {"event": "hvi", "kind": item.kind, "value": item.value},
            ts=started_ts + duration,
        )

    urls = [item.value for item in session.hvis if item.kind == "url"]
    signals = list(record.get("sandbox_signals") or [])
    for index in range(int(record.get("sandbox_runs") or 0)):
        url = urls[min(index, len(urls) - 1)] if urls else ""
        signal = str(signals[index]) if index < len(signals) else "unknown"
        finding = {
            "url": url,
            "final_url": url,
            "dest_ip": "203.0.113.10",
            "verdict_signal": signal,
            "access_state": "controlled evaluation",
            "fetcher": "deterministic_stub",
            "challenge_provider": "none",
            "cloaking_suspected": False,
        }
        session.sandbox_results.append(finding)
        chain.append(
            {"event": "sandbox", "url": url, "signal": signal},
            ts=started_ts + duration,
        )
    return session, chain


def reseal_group(group: Path, key_path: Path) -> dict[str, Any]:
    runs_path = group / "redteam_runs.json"
    records = _load(runs_path)
    if not isinstance(records, list):
        raise ValueError(f"{runs_path} must contain a list")
    verified = 0
    for record in records:
        pdf = _pdf_path(group, record)
        started_ts = max(0.0, pdf.stat().st_mtime - float(record.get("duration_s") or 0))
        session, chain = _session_and_chain(record, started_ts=started_ts)
        build_bundle(
            session,
            chain,
            str(pdf),
            str(key_path),
            operator_name="Controlled evaluation - renderer reseal",
        )
        verification = verify_evidence_package(pdf.with_suffix(".evidence.zip"))
        if not verification["ok"]:
            raise RuntimeError(f"verification failed for {pdf}: {verification['errors']}")
        record["evidence_verified"] = True
        record["evidence_checks"] = {
            str(key): bool(value) for key, value in verification["checks"].items()
        }
        verified += 1
    _write(runs_path, records)

    metadata_path = group / "redteam_metadata.json"
    metadata = _load(metadata_path)
    metadata["evidence_reseal"] = {
        "created_utc": datetime.now(UTC).isoformat(),
        "reason": "Emoji-safe transcript rendering after message-bubble segmentation.",
        "source": "Retained redteam_runs.json; transcript words and metrics unchanged.",
        "operator": "Controlled evaluation - renderer reseal",
    }
    _write(metadata_path, metadata)
    return {"group": group.name, "runs": len(records), "verified": verified}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    key_path = Path(settings.signing_key_path)
    if not key_path.is_file():
        parser.error(f"signing key not found: {key_path}")
    root = args.root.resolve()
    results = [
        reseal_group(group, key_path)
        for group in sorted(root.iterdir())
        if group.is_dir() and (group / "redteam_runs.json").is_file()
    ]
    print(json.dumps({"root": str(root), "groups": results}, indent=2))


if __name__ == "__main__":
    main()
