"""Authoritative sealed-case profiles and exact cross-case relationships."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from hive.audit import audit_event
from hive.state import SessionState

_EXACT_KINDS = frozenset(
    {"bank_account", "phone", "url", "crypto", "telegram_id", "email"}
)
_METHOD_LABELS = {
    "urgency": "urgency / pressure",
    "authority_impersonation": "authority impersonation",
    "romance_framing": "romance grooming",
    "investment_framing": "investment pitch",
    "payment_request": "payment request",
    "inconsistency": "inconsistent narrative",
}
_RELATION_WEIGHTS = {
    "bank_account": 0.95,
    "crypto": 0.95,
    "url": 0.9,
    "phone": 0.85,
    "telegram_id": 0.85,
    "email": 0.75,
}


def normalize_indicator(kind: str, value: str) -> str:
    value = value.strip()
    if kind in {"bank_account", "phone"}:
        return re.sub(r"\D", "", value)
    if kind == "url":
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        return (parsed.hostname or "").lower().removeprefix("www.")
    if kind == "telegram_id":
        return value.lower().removeprefix("@")
    return value.lower()


def _valid_network_indicator(kind: str, normalized: str) -> bool:
    if kind == "bank_account":
        return 8 <= len(normalized) <= 20
    if kind == "phone":
        return 8 <= len(normalized) <= 15
    if kind == "url":
        return "." in normalized and len(normalized) <= 253
    if kind == "crypto":
        return 20 <= len(normalized) <= 128
    if kind == "telegram_id":
        return 5 <= len(normalized) <= 32
    if kind == "email":
        return "@" in normalized and len(normalized) <= 254
    return False


def _analysis_outputs(
    history_record: dict[str, Any],
    analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    return analysis or history_record


def build_case_profile(
    history_record: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic profile from validated, message-grounded findings."""
    outputs = _analysis_outputs(history_record, analysis)
    metadata = analysis or history_record.get("analysis") or {}
    indicator_map: dict[tuple[str, str], dict[str, Any]] = {}
    for item in outputs.get("hvi_items") or []:
        kind = str(item.get("kind") or "")
        confidence = float(item.get("confidence") or 0)
        normalized = normalize_indicator(kind, str(item.get("value") or ""))
        if (
            kind not in _EXACT_KINDS
            or confidence < 0.6
            or not _valid_network_indicator(kind, normalized)
        ):
            continue
        candidate = {
            "kind": kind,
            "value": str(item.get("value") or ""),
            "normalized_value": normalized,
            "confidence": confidence,
            "source_msg_id": item.get("source_msg_id"),
            "extractor": str(item.get("extractor") or "unknown"),
        }
        key = (kind, normalized)
        if confidence > float(indicator_map.get(key, {}).get("confidence") or -1):
            indicator_map[key] = candidate

    indicators = list(indicator_map.values())

    methods: dict[str, float] = {}
    for signal in outputs.get("signal_trail") or []:
        for contribution in signal.get("contributions") or []:
            reason = str(contribution.get("reason") or "")
            if not reason.startswith("soft:"):
                continue
            key = reason.removeprefix("soft:")
            confidence = float(contribution.get("confidence") or 0)
            if confidence >= 0.5:
                methods[key] = max(methods.get(key, 0), confidence)

    script = "\n".join(
        str(message.get("text") or "")
        for message in history_record.get("messages") or []
        if message.get("role") == "stranger" and message.get("text")
    )[:12000]
    payment_flow = [
        item
        for item in indicators
        if item["kind"] in {"bank_account", "crypto", "url"}
    ]
    method_labels = [
        _METHOD_LABELS.get(key, key.replace("_", " "))
        for key in sorted(methods, key=lambda item: methods[item], reverse=True)
    ]
    embedding_parts = [
        f"Verdict: {outputs.get('verdict', 'inconclusive')}",
        f"Methods: {', '.join(method_labels) or 'unknown'}",
        f"Script:\n{script}",
        "Verified indicators: "
        + ", ".join(f"{item['kind']}={item['value']}" for item in indicators),
    ]
    return {
        "case_id": str(history_record["id"]),
        "history_id": str(history_record["id"]),
        "analysis_run_id": str(metadata.get("id") or ""),
        "analysis_schema_version": metadata.get("schema_version"),
        "transcript_sha256": str(metadata.get("transcript_sha256") or ""),
        "peer_id": int(history_record["peer_id"]),
        "created_ts": float(metadata.get("created_ts") or history_record.get("ended_ts") or 0),
        "verdict": str(outputs.get("verdict") or "inconclusive"),
        "score": float(outputs.get("score") or 0),
        "methods": [
            {"key": key, "label": _METHOD_LABELS.get(key, key), "confidence": methods[key]}
            for key in sorted(methods, key=lambda item: methods[item], reverse=True)
        ],
        "external_script": script,
        "indicators": indicators,
        "payment_flow": payment_flow,
        "sandbox_results": list(outputs.get("sandbox_results") or []),
        "embedding_text": "\n".join(embedding_parts),
    }


def build_live_case_profile(session: SessionState) -> dict[str, Any]:
    messages = [
        {
            "role": message.role,
            "text": message.text,
            "ts": message.ts,
            "msg_id": message.msg_id,
            "media_sha256": message.media_sha256,
        }
        for message in session.messages
    ]
    record = {
        "id": f"live:{session.session_id}",
        "peer_id": session.peer_id,
        "started_ts": session.started_ts,
        "ended_ts": time.time(),
        "messages": messages,
        "verdict": session.verdict,
        "score": session.verdict_score,
        "hvi_items": [
            {
                "kind": item.kind,
                "value": item.value,
                "confidence": item.confidence,
                "source_msg_id": item.source_msg_id,
                "extractor": item.extractor,
            }
            for item in session.hvis
        ],
        "signal_trail": session.signal_trail,
        "sandbox_results": session.sandbox_results,
        "analysis": {
            "id": f"live:{session.session_id}",
            "schema_version": 1,
            "created_ts": time.time(),
            "transcript_sha256": "live",
        },
    }
    return build_case_profile(record)


def build_probe_context(
    session: SessionState,
    store: CaseIntelligenceStore,
    *,
    limit: int = 3,
) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve candidates and reduce them to safe, non-attributive probing hints."""
    profile = build_live_case_profile(session)
    matches = store.match(profile, limit)
    if not matches:
        return "", []
    current_kinds = {item["kind"] for item in profile.get("indicators") or []}
    missing: set[str] = set()
    match_lines = []
    for match in matches:
        related = store.get(str(match["related_case_id"])) or {}
        missing.update(
            item["kind"]
            for item in related.get("indicators") or []
            if item["kind"] not in current_kinds
        )
        if match.get("relationship") == "shared_identifier":
            kinds = sorted({item["kind"] for item in match.get("reasons") or []})
            match_lines.append(
                f"exact shared {', '.join(kind.replace('_', ' ') for kind in kinds)}"
            )
        else:
            match_lines.append("similar script/behavior candidate only")
    targets = ", ".join(kind.replace("_", " ") for kind in sorted(missing))
    context = (
        "Private historical-pattern guidance. Never mention prior cases, matching, "
        "databases, or investigation. Similarity alone is not proof of common ownership. "
        f"Candidate signals: {'; '.join(match_lines)}. "
        + (
            f"Useful missing identifier types: {targets}. Ask naturally for at most one "
            "when it fits the conversation."
            if targets
            else "Continue naturally without forcing another identifier request."
        )
    )
    return context, matches


def exact_relationships(
    case_id: str,
    profile: dict[str, Any],
    others: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current = defaultdict(list)
    for item in profile.get("indicators") or []:
        current[(item["kind"], item["normalized_value"])].append(item)
    rows = []
    for other in others:
        if other.get("case_id") == case_id:
            continue
        reasons = []
        for item in other.get("indicators") or []:
            key = (item["kind"], item["normalized_value"])
            if key in current:
                current_items = current[key]
                reasons.append(
                    {
                        "kind": item["kind"],
                        "value": item["value"],
                        "normalized_value": item["normalized_value"],
                        "match": "exact_identifier",
                        "current_source_message_ids": sorted(
                            {
                                value["source_msg_id"]
                                for value in current_items
                                if value.get("source_msg_id") is not None
                            }
                        ),
                        "related_source_message_ids": (
                            [item["source_msg_id"]]
                            if item.get("source_msg_id") is not None
                            else []
                        ),
                    }
                )
        if not reasons:
            continue
        score = min(
            1.0,
            max(_RELATION_WEIGHTS.get(item["kind"], 0.7) for item in reasons)
            + 0.05 * (len(reasons) - 1),
        )
        rows.append(
            {
                "case_id": case_id,
                "related_case_id": str(other["case_id"]),
                "relationship": "shared_identifier",
                "score": round(score, 4),
                "reasons": reasons,
            }
        )
    return sorted(rows, key=lambda row: row["score"], reverse=True)


class CaseIntelligenceStore(Protocol):
    def index(self, profile: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, case_id: str) -> dict[str, Any] | None: ...

    def related(self, case_id: str) -> list[dict[str, Any]]: ...

    def match(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]: ...


class LocalCaseIntelligenceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def index(self, profile: dict[str, Any]) -> dict[str, Any]:
        case_id = str(profile.get("case_id") or "")
        if not case_id:
            raise ValueError("case profile requires a case id")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{case_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        _audit_index(profile, "case_intelligence.local")
        return profile

    def get(self, case_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads((self.root / f"{case_id}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def related(self, case_id: str) -> list[dict[str, Any]]:
        profile = self.get(case_id)
        if profile is None or not self.root.is_dir():
            return []
        others = [
            value
            for path in self.root.glob("*.json")
            if (value := self.get(path.stem)) is not None
        ]
        return exact_relationships(case_id, profile, others)

    def match(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        others = [
            value
            for path in self.root.glob("*.json")
            if (value := self.get(path.stem)) is not None
        ]
        return exact_relationships(str(profile["case_id"]), profile, others)[:limit]


class PostgresCaseIntelligenceStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._initialized = False

    def _connect(self) -> Any:
        import psycopg

        connection = psycopg.connect(self.database_url)
        if not self._initialized:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hive_cases (
                        case_id TEXT PRIMARY KEY,
                        peer_id BIGINT NOT NULL,
                        analysis_run_id TEXT NOT NULL,
                        updated_ts DOUBLE PRECISION NOT NULL,
                        profile JSONB NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hive_case_indicators (
                        case_id TEXT NOT NULL REFERENCES hive_cases(case_id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        normalized_value TEXT NOT NULL,
                        value TEXT NOT NULL,
                        confidence DOUBLE PRECISION NOT NULL,
                        source_msg_id BIGINT,
                        analysis_run_id TEXT NOT NULL,
                        PRIMARY KEY (case_id, kind, normalized_value)
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS hive_case_indicators_lookup_idx
                    ON hive_case_indicators (kind, normalized_value)
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hive_case_relationships (
                        case_a TEXT NOT NULL REFERENCES hive_cases(case_id) ON DELETE CASCADE,
                        case_b TEXT NOT NULL REFERENCES hive_cases(case_id) ON DELETE CASCADE,
                        relationship TEXT NOT NULL,
                        score DOUBLE PRECISION NOT NULL,
                        reasons JSONB NOT NULL,
                        updated_ts DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY (case_a, case_b, relationship)
                    )
                    """
                )
            connection.commit()
            self._initialized = True
        return connection

    def index(self, profile: dict[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        case_id = str(profile["case_id"])
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO hive_cases (case_id, peer_id, analysis_run_id, updated_ts, profile)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (case_id) DO UPDATE SET
                    peer_id = EXCLUDED.peer_id,
                    analysis_run_id = EXCLUDED.analysis_run_id,
                    updated_ts = EXCLUDED.updated_ts,
                    profile = EXCLUDED.profile
                """,
                (
                    case_id,
                    int(profile["peer_id"]),
                    profile.get("analysis_run_id", ""),
                    time.time(),
                    Jsonb(profile),
                ),
            )
            cursor.execute("DELETE FROM hive_case_indicators WHERE case_id = %s", (case_id,))
            for item in profile.get("indicators") or []:
                cursor.execute(
                    """
                    INSERT INTO hive_case_indicators
                    (case_id, kind, normalized_value, value, confidence, source_msg_id,
                     analysis_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_id,
                        item["kind"],
                        item["normalized_value"],
                        item["value"],
                        item["confidence"],
                        item.get("source_msg_id"),
                        profile.get("analysis_run_id", ""),
                    ),
                )
            cursor.execute(
                "DELETE FROM hive_case_relationships WHERE case_a = %s OR case_b = %s",
                (case_id, case_id),
            )
            cursor.execute("SELECT profile FROM hive_cases WHERE case_id <> %s", (case_id,))
            relationships = exact_relationships(
                case_id,
                profile,
                [row[0] for row in cursor.fetchall()],
            )
            for relation in relationships:
                case_a, case_b = sorted((case_id, relation["related_case_id"]))
                cursor.execute(
                    """
                    INSERT INTO hive_case_relationships
                    (case_a, case_b, relationship, score, reasons, updated_ts)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_a,
                        case_b,
                        relation["relationship"],
                        relation["score"],
                        Jsonb(relation["reasons"]),
                        time.time(),
                    ),
                )
        _audit_index(profile, "case_intelligence.postgres")
        return profile

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT profile FROM hive_cases WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def related(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT case_a, case_b, relationship, score, reasons
                FROM hive_case_relationships
                WHERE case_a = %s OR case_b = %s
                ORDER BY score DESC
                """,
                (case_id, case_id),
            )
            return [
                {
                    "case_id": case_id,
                    "related_case_id": case_b if case_a == case_id else case_a,
                    "relationship": relationship,
                    "score": score,
                    "reasons": reasons,
                }
                for case_a, case_b, relationship, score, reasons in cursor.fetchall()
            ]

    def match(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT profile FROM hive_cases WHERE case_id <> %s",
                (profile["case_id"],),
            )
            return exact_relationships(
                str(profile["case_id"]),
                profile,
                [row[0] for row in cursor.fetchall()],
            )[:limit]


def _audit_index(profile: dict[str, Any], component: str) -> None:
    audit_event(
        "case_intelligence",
        "sealed_case_indexed",
        component=component,
        payload={
            "case_id": profile["case_id"],
            "analysis_run_id": profile.get("analysis_run_id"),
            "indicator_count": len(profile.get("indicators") or []),
            "methods": profile.get("methods") or [],
        },
        peer_id=int(profile["peer_id"]),
    )


def build_case_intelligence_store(
    root: str | Path,
    database_url: str = "",
    *,
    qdrant_url: str = "",
    enable_semantic: bool = False,
    similarity_threshold: float = 0.72,
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
) -> CaseIntelligenceStore:
    relational: CaseIntelligenceStore = (
        PostgresCaseIntelligenceStore(database_url)
        if database_url
        else LocalCaseIntelligenceStore(root)
    )
    if not enable_semantic or not qdrant_url:
        return relational
    from hive.case_vectors import HybridCaseIntelligenceStore, QdrantCaseVectorIndex

    vectors = QdrantCaseVectorIndex(
        qdrant_url,
        similarity_threshold=similarity_threshold,
        embedding_model=embedding_model,
    )
    return HybridCaseIntelligenceStore(relational, vectors)
