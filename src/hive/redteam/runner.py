"""Reproducible red-team conversations through the complete HIVE pipeline."""

from __future__ import annotations

import csv
import json
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hive.guardrails.injection import screen
from hive.llm.client import LLMClient
from hive.logging_setup import get_logger
from hive.redteam.scammer import Archetype, scammer_reply
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.state import Message
from hive.vault.package import evidence_package_path, verify_evidence_package

log = get_logger(__name__)
_BOT_LANGUAGE = re.compile(
    r"\b(bot|chatbot|ai|artificial intelligence|chatgpt|automated|robot)\b",
    re.IGNORECASE,
)
_BOT_DETECTION = re.compile(
    r"\b(?:you are|you're|this is|talking to|obviously|definitely)\b.{0,24}"
    r"\b(?:a\s+)?(?:bot|chatbot|ai|robot|automated)\b",
    re.IGNORECASE,
)
DETECTION_DEFINITION = (
    "A run is detected only when a scammer message explicitly asserts that the "
    "counterparty is a bot, AI, automated system, or robot. Questions and generic "
    "bot probes are counted separately and do not by themselves count as detection."
)


class EvaluationSandboxRunner:
    """Deterministic no-network browser seam for repeatable pipeline tests."""

    def run(self, url: str) -> RawFindings:
        return RawFindings(
            final_url=url,
            redirect_chain=[url],
            has_password_field=True,
            title="Controlled evaluation landing page",
            body_len=2048,
            dest_ip="203.0.113.10",
        )


@dataclass
class ExtractionMetrics:
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float


@dataclass
class RunResult:
    archetype: str
    persona: str
    scenario: str = ""
    language: str = "unspecified"
    transcript: list[tuple[str, str]] = field(default_factory=list)
    turns: int = 0
    exchanges: int = 0
    duration_s: float = 0.0
    agent_tiers: list[str] = field(default_factory=list)
    hvi_items: list[dict[str, Any]] = field(default_factory=list)
    additional_hvi_items: list[dict[str, Any]] = field(default_factory=list)
    scored_message_ids: list[int] = field(default_factory=lambda: [0])
    extraction: ExtractionMetrics | None = None
    verdict: str = "inconclusive"
    verdict_score: float = 0.0
    expected_verdict: str = "likely_scam"
    verdict_correct: bool = False
    sandbox_runs: int = 0
    sandbox_signals: list[str] = field(default_factory=list)
    sandbox_mode: str = "deterministic_stub"
    bot_probes: int = 0
    bot_detected: bool = False
    detection_definition: str = DETECTION_DEFINITION
    chain_valid: bool = False
    guardrail_flags: int = 0
    termination_reason: str = "max_exchanges"
    evidence_package: str | None = None
    evidence_verified: bool | None = None
    evidence_checks: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _indicator_pairs(items: Iterable[Any]) -> set[tuple[str, str]]:
    return {(str(item.kind), str(item.value).strip().lower()) for item in items}


def split_model_messages(text: str) -> tuple[str, ...]:
    """Turn line-separated model output into distinct mobile-chat messages.

    Generative chat models commonly format several thoughts as paragraphs or
    list rows in one response. In Telegram those are separate bubbles, not one
    multi-line message, so the evaluation harness preserves every non-empty
    line as its own message without rewriting the model's words.
    """
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _score_extraction(
    predicted: set[tuple[str, str]],
    expected: set[tuple[str, str]],
) -> ExtractionMetrics:
    true_positive = len(predicted & expected)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    precision = true_positive / (true_positive + false_positive) if predicted else 1.0
    recall = true_positive / (true_positive + false_negative) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return ExtractionMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def run_conversation(
    agent_client: LLMClient,
    scammer_client: LLMClient,
    archetype: Archetype,
    persona: str,
    max_turns: int = 8,
    opener: str = "Hello, I have a special offer for you today!",
    *,
    engine: HiveEngine | None = None,
    expected_hvis: Iterable[tuple[str, str]] = (),
    expected_verdict: str = "likely_scam",
    evidence_pdf: str | Path | None = None,
    signing_key_path: str | Path | None = None,
    scenario_key: str = "",
    language: str = "unspecified",
) -> RunResult:
    """Run a scammer against guardrails, extraction, sandbox, verdict, and persona.

    Evidence verification is included when both ``evidence_pdf`` and
    ``signing_key_path`` are supplied. ``max_turns`` means scammer/HIVE
    exchanges, while ``RunResult.turns`` retains HIVE's inbound-message count.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")
    if (evidence_pdf is None) != (signing_key_path is None):
        raise ValueError("evidence_pdf and signing_key_path must be supplied together")

    active_engine = engine or HiveEngine(
        agent_client=agent_client,
        sandbox_runner=EvaluationSandboxRunner(),
        enable_early_exit=False,
        # The harness bounds exchanges itself. One simulated exchange may now
        # contain several realistic inbound chat bubbles.
        max_turns=0,
    )
    peer_id = int.from_bytes(archetype.key.encode("utf-8"), "little") % 1_000_000_000
    session, chain = active_engine.new_session(peer_id=peer_id, persona=persona)
    result = RunResult(
        archetype=archetype.key,
        persona=persona,
        scenario=scenario_key,
        language=language,
        expected_verdict=expected_verdict,
        sandbox_mode=(
            "deterministic_stub"
            if isinstance(active_engine.sandbox_runner, EvaluationSandboxRunner)
            else type(active_engine.sandbox_runner).__name__
        ),
    )
    history: list[tuple[str, str]] = []
    scam_messages = split_model_messages(opener)
    if not scam_messages:
        raise ValueError("opener must contain at least one non-empty message")
    next_inbound_id = 0
    started = time.perf_counter()

    for exchange in range(max_turns):
        inbounds: list[Message] = []
        for scam_message in scam_messages:
            history.append(("scammer", scam_message))
            if _BOT_LANGUAGE.search(scam_message):
                result.bot_probes += 1
            if _BOT_DETECTION.search(scam_message):
                result.bot_detected = True
            if screen(scam_message).flagged:
                result.guardrail_flags += 1
            timestamp = time.time()
            inbounds.append(
                Message(
                    role="stranger",
                    text=scam_message,
                    ts=timestamp,
                    msg_id=next_inbound_id,
                    captured_ts=timestamp,
                    platform="simulation",
                    pre_takeover=exchange == 0,
                )
            )
            next_inbound_id += 1
        if exchange == 0:
            result.scored_message_ids = [message.msg_id for message in inbounds]
        output = active_engine.process_messages(
            session,
            chain,
            inbounds,
        )
        if output.tier:
            result.agent_tiers.append(output.tier)
        victim_messages = output.messages or split_model_messages(output.text or "")
        history.extend(("victim", message) for message in victim_messages)
        if output.handed_back or output.terminated or not output.text:
            result.termination_reason = output.reason or "no_reply"
            break
        scam_messages = split_model_messages(
            scammer_reply(scammer_client, archetype, history)
        )
        if not scam_messages:
            result.termination_reason = "no_scammer_reply"
            break

    result.duration_s = time.perf_counter() - started
    result.transcript = history
    result.turns = session.turn_count
    result.exchanges = session.exchange_count
    result.hvi_items = [
        {
            "kind": item.kind,
            "value": item.value,
            "confidence": item.confidence,
            "source_msg_id": item.source_msg_id,
            "extractor": item.extractor,
        }
        for item in session.hvis
    ]
    result.additional_hvi_items = [
        item for item in result.hvi_items if item["source_msg_id"] not in result.scored_message_ids
    ]
    result.extraction = _score_extraction(
        _indicator_pairs(
            item for item in session.hvis if item.source_msg_id in result.scored_message_ids
        ),
        {(kind, value.strip().lower()) for kind, value in expected_hvis},
    )
    result.verdict = session.verdict
    result.verdict_score = session.verdict_score
    result.verdict_correct = session.verdict == expected_verdict
    result.sandbox_runs = len(session.sandbox_results)
    result.sandbox_signals = [
        str(item.get("verdict_signal") or "unknown") for item in session.sandbox_results
    ]
    result.chain_valid = chain.verify()

    if evidence_pdf is not None and signing_key_path is not None:
        sealed_pdf = active_engine.close_session(
            session,
            chain,
            str(evidence_pdf),
            str(signing_key_path),
            operator_name="Controlled evaluation",
        )
        package = evidence_package_path(sealed_pdf)
        verification = verify_evidence_package(package)
        result.evidence_package = str(package)
        result.evidence_verified = bool(verification["ok"])
        result.evidence_checks = {
            str(key): bool(value) for key, value in verification["checks"].items()
        }

    log.info(
        "run[%s/%s] complete: exchanges=%d verdict=%s hvis=%d detected=%s",
        archetype.key,
        persona,
        result.exchanges,
        result.verdict,
        len(result.hvi_items),
        result.bot_detected,
    )
    return result


def aggregate_results(results: Iterable[RunResult]) -> dict[str, Any]:
    """Aggregate run-level outcomes with explicit denominators."""
    rows = list(results)
    count = len(rows)
    tp = sum(row.extraction.true_positive for row in rows if row.extraction)
    fp = sum(row.extraction.false_positive for row in rows if row.extraction)
    fn = sum(row.extraction.false_negative for row in rows if row.extraction)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "runs": count,
        "total_exchanges": sum(row.exchanges for row in rows),
        "mean_duration_s": sum(row.duration_s for row in rows) / count if count else 0.0,
        "mean_hvis": sum(len(row.hvi_items) for row in rows) / count if count else 0.0,
        "verdict_accuracy": (
            sum(row.verdict_correct for row in rows) / count if count else 0.0
        ),
        "bot_detection_rate": (
            sum(row.bot_detected for row in rows) / count if count else 0.0
        ),
        "detection_definition": DETECTION_DEFINITION,
        "evidence_verification_rate": (
            sum(row.evidence_verified is True for row in rows) / count if count else 0.0
        ),
        "extraction": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": (
                2 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            ),
        },
    }


def write_results(results: Iterable[RunResult], output_directory: str | Path) -> dict[str, Path]:
    """Write raw JSON, a flat CSV, and aggregate JSON for appendix evidence."""
    rows = list(results)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    raw_path = output / "redteam_runs.json"
    summary_path = output / "redteam_summary.json"
    csv_path = output / "redteam_runs.csv"
    raw_path.write_text(
        json.dumps([row.as_dict() for row in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(aggregate_results(rows), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "archetype",
            "persona",
            "scenario",
            "language",
            "turns",
            "exchanges",
            "duration_s",
            "hvi_count",
            "additional_hvi_count",
            "verdict",
            "verdict_score",
            "verdict_correct",
            "sandbox_runs",
            "bot_probes",
            "bot_detected",
            "chain_valid",
            "guardrail_flags",
            "evidence_verified",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{field: getattr(row, field) for field in fields if hasattr(row, field)},
                    "hvi_count": len(row.hvi_items),
                    "additional_hvi_count": len(row.additional_hvi_items),
                }
            )
    return {"runs": raw_path, "summary": summary_path, "csv": csv_path}
