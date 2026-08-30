"""Reproducible red-team conversations through the complete HIVE pipeline."""

from __future__ import annotations

import csv
import json
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.agent.personas import get_persona
from hive.guardrails.injection import screen
from hive.llm.client import LLMClient
from hive.logging_setup import get_logger
from hive.redteam.character import aggregate_character, assess_character, assessment_metrics
from hive.redteam.scammer import Archetype, scammer_reply
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.scenario_media import FIXTURES, message_from_scenario, scenario_message
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
    "A run is detected only when a post-response scammer message explicitly asserts "
    "that the counterparty is a bot, AI, automated system, or robot. Scripted opener "
    "assertions and generic bot questions are recorded separately and excluded."
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
    persona_prompt: str = ""
    transcript: list[tuple[str, str]] = field(default_factory=list)
    turns: int = 0
    exchanges: int = 0
    started_utc: str = ""
    completed_utc: str = ""
    duration_s: float = 0.0
    target_response_turns: int = 20
    character_assessment: dict[str, Any] | None = None
    planned_response_delay_s: float = 0.0
    response_latencies_s: list[float] = field(default_factory=list)
    pipeline_durations_s: list[float] = field(default_factory=list)
    agent_language: str = "unknown"
    language_match: bool = False
    agent_tiers: list[str] = field(default_factory=list)
    hvi_items: list[dict[str, Any]] = field(default_factory=list)
    additional_hvi_items: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
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
    seed_bot_probes: int = 0
    seed_bot_detected: bool = False
    detection_definition: str = DETECTION_DEFINITION
    chain_valid: bool = False
    guardrail_flags: int = 0
    outbound_guardrail_flags: int = 0
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


def assess_language_alignment(
    expected_language: str,
    messages: Iterable[str],
) -> tuple[str, bool]:
    """Classify the visible script and test coarse English/Mandarin alignment.

    This intentionally avoids claiming semantic fluency. It verifies the more
    modest objective that the agent replies in the expected writing system;
    human UAT remains responsible for naturalness and cultural fit.
    """
    text = " ".join(messages)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    letters = cjk + latin
    if not letters:
        return "unknown", False
    cjk_ratio = cjk / letters
    if cjk_ratio >= 0.5:
        observed = "Mandarin"
    elif cjk:
        observed = "Mixed"
    else:
        observed = "English/Manglish"
    expected = expected_language.strip().casefold()
    if expected == "mandarin":
        return observed, cjk_ratio >= 0.2
    if expected in {"english", "manglish"}:
        return observed, latin > 0 and cjk_ratio < 0.5
    return observed, False


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
    max_turns: int = 20,
    opener: str = "Hello, I have a special offer for you today!",
    *,
    engine: HiveEngine | None = None,
    expected_hvis: Iterable[tuple[str, str]] = (),
    expected_verdict: str = "likely_scam",
    evidence_pdf: str | Path | None = None,
    signing_key_path: str | Path | None = None,
    scenario_key: str = "",
    language: str = "unspecified",
    opener_fixtures: Iterable[str] = (),
    character_client: LLMClient | None = None,
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
        persona_prompt=get_persona(persona).system_prompt,
        expected_verdict=expected_verdict,
        started_utc=datetime.now(UTC).isoformat(),
        target_response_turns=max_turns,
        sandbox_mode=(
            "deterministic_stub"
            if isinstance(active_engine.sandbox_runner, EvaluationSandboxRunner)
            else type(active_engine.sandbox_runner).__name__
        ),
    )
    history: list[tuple[str, str]] = []
    scam_messages = [scenario_message(text) for text in split_model_messages(opener)]
    scam_messages.extend(
        scenario_message(f"A synthetic {FIXTURES[key].kind} is attached.", key)
        for key in opener_fixtures
    )
    if not scam_messages:
        raise ValueError("opener must contain at least one non-empty message")
    next_inbound_id = 0
    started = time.perf_counter()

    for exchange in range(max_turns):
        inbounds: list[Message] = []
        for scam_message in scam_messages:
            history.append(("scammer", scam_message.text))
            if _BOT_LANGUAGE.search(scam_message.text):
                result.bot_probes += 1
                if exchange == 0:
                    result.seed_bot_probes += 1
            if _BOT_DETECTION.search(scam_message.text):
                if exchange == 0:
                    result.seed_bot_detected = True
                else:
                    result.bot_detected = True
            if screen(scam_message.text).flagged:
                result.guardrail_flags += 1
            timestamp = time.time()
            inbound = message_from_scenario(
                scam_message,
                msg_id=next_inbound_id,
                ts=timestamp,
                platform="simulation",
                pre_takeover=exchange == 0,
            )
            inbounds.append(inbound)
            if inbound.media_kind:
                result.attachments.append(
                    {
                        "msg_id": inbound.msg_id,
                        "kind": inbound.media_kind,
                        "name": inbound.media_name,
                        "mime": inbound.media_mime,
                        "size": inbound.media_size,
                        "sha256": inbound.media_sha256,
                        "safe_fixture": True,
                    }
                )
            next_inbound_id += 1
        if exchange == 0:
            result.scored_message_ids = [message.msg_id for message in inbounds]
        pipeline_started = time.perf_counter()
        try:
            output = active_engine.process_messages(session, chain, inbounds)
        except Exception:  # noqa: BLE001 - retain failed/short runs, never count as passes
            log.exception("Evaluation pipeline failed at exchange %d", exchange + 1)
            result.termination_reason = "pipeline_error"
            break
        pipeline_duration = time.perf_counter() - pipeline_started
        result.pipeline_durations_s.append(pipeline_duration)
        if output.tier:
            result.agent_tiers.append(output.tier)
        result.outbound_guardrail_flags += int(output.outbound_guardrail_flag)
        victim_messages = output.messages or split_model_messages(output.text or "")
        if victim_messages:
            delays = tuple(output.message_delays_s) or (max(0.0, output.delay_s),)
            first_delay = delays[0] if delays else 0.0
            skipped_wait = max(0.0, first_delay - pipeline_duration) + sum(delays[1:])
            result.planned_response_delay_s += skipped_wait
            result.response_latencies_s.append(
                max(pipeline_duration, first_delay) + sum(delays[1:])
            )
        history.extend(("victim", message) for message in victim_messages)
        if output.handed_back or output.terminated or not output.text:
            result.termination_reason = output.reason or "no_reply"
            break
        if exchange + 1 == max_turns:
            break
        try:
            scam_messages = [
                scenario_message(text)
                for text in split_model_messages(scammer_reply(scammer_client, archetype, history))
            ]
        except Exception:  # noqa: BLE001 - preserve partial runs for explicit exclusion
            log.exception("Simulated scammer failed at exchange %d", exchange + 1)
            result.termination_reason = "scammer_error"
            break
        if not scam_messages:
            result.termination_reason = "no_scammer_reply"
            break

    result.duration_s = time.perf_counter() - started
    result.completed_utc = datetime.now(UTC).isoformat()
    result.transcript = history
    result.agent_language, result.language_match = assess_language_alignment(
        language,
        (text for role, text in history if role == "victim"),
    )
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
    result.character_assessment = assess_character(
        persona, history, max_turns, character_client, persona_prompt=result.persona_prompt,
    )

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
        "character": aggregate_character([row.character_assessment for row in rows]),
        "mean_response_latency_s": (
            sum(sum(row.response_latencies_s) for row in rows)
            / sum(len(row.response_latencies_s) for row in rows)
            if any(row.response_latencies_s for row in rows)
            else 0.0
        ),
        "mean_hvis": sum(len(row.hvi_items) for row in rows) / count if count else 0.0,
        "mean_threat_indicators_per_session": (
            sum(len(row.hvi_items) for row in rows) / count if count else 0.0
        ),
        "language_alignment_rate": (
            sum(row.language_match for row in rows) / count if count else 0.0
        ),
        "verdict_accuracy": (sum(row.verdict_correct for row in rows) / count if count else 0.0),
        "evidence_verification_rate": (
            sum(row.evidence_verified is True for row in rows) / count if count else 0.0
        ),
        "extraction": {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": (2 * precision * recall / (precision + recall) if precision + recall else 0.0),
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
            "target_response_turns",
            "character_status",
            "character_eligible",
            "character_session_break",
            "character_response_break_rate",
            "character_first_break_turn",
            "character_assessed_turns",
            "character_uncertain_turns",
            "planned_response_delay_s",
            "mean_response_latency_s",
            "agent_language",
            "language_match",
            "hvi_count",
            "additional_hvi_count",
            "attachment_count",
            "verdict",
            "verdict_score",
            "verdict_correct",
            "sandbox_runs",
            "bot_probes",
            "bot_detected",
            "seed_bot_probes",
            "seed_bot_detected",
            "chain_valid",
            "guardrail_flags",
            "outbound_guardrail_flags",
            "evidence_verified",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            character = assessment_metrics(row.character_assessment)
            writer.writerow(
                {
                    **{field: getattr(row, field) for field in fields if hasattr(row, field)},
                    "character_status": (row.character_assessment or {}).get(
                        "status", "not_assessed"
                    ),
                    **{f"character_{key}": character[key] for key in (
                        "eligible", "session_break", "response_break_rate", "first_break_turn",
                        "assessed_turns", "uncertain_turns",
                    )},
                    "hvi_count": len(row.hvi_items),
                    "additional_hvi_count": len(row.additional_hvi_items),
                    "attachment_count": len(row.attachments),
                    "mean_response_latency_s": (
                        sum(row.response_latencies_s) / len(row.response_latencies_s)
                        if row.response_latencies_s
                        else 0.0
                    ),
                }
            )
    return {"runs": raw_path, "summary": summary_path, "csv": csv_path}
