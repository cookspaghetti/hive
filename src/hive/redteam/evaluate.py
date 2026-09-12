"""Run the versioned HIVE red-team matrix and retain appendix-ready outputs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.agent.personas import PERSONAS
from hive.config import load_settings
from hive.redteam.character import RUBRIC_VERSION
from hive.redteam.integrity import MANIFEST_FILENAME, finalize_result_directory
from hive.redteam.runner import (
    EvaluationSandboxRunner,
    ExtractionMetrics,
    RunResult,
    aggregate_results,
    run_conversation,
    write_results,
)
from hive.redteam.scammer import ARCHETYPES
from hive.redteam.scenarios import DEFAULT_SCENARIOS, EvaluationScenario
from hive.runtime import build_engine
from hive.threat_intelligence import build_synthetic_threat_intelligence_service
from hive.vault.signer import signing_key_details

PROGRESS_FILENAME = "redteam_progress.json"
METADATA_FILENAME = "redteam_metadata.json"
RUNS_FILENAME = "redteam_runs.json"


def timestamped_output_directory(root: str | Path, created: datetime) -> Path:
    """Return an append-only run directory beneath the configured result root."""
    run_id = created.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return Path(root) / run_id


def _atomic_json(path: Path, payload: Any) -> None:
    """Publish one campaign control file without exposing partial JSON."""
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _result_from_payload(payload: dict[str, Any]) -> RunResult:
    """Rehydrate a checkpointed run while rejecting unexpected result fields."""
    allowed = {item.name for item in fields(RunResult)}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"checkpoint run contains unexpected fields: {unexpected}")
    values = dict(payload)
    transcript = values.get("transcript")
    if isinstance(transcript, list):
        if not all(
            isinstance(turn, (list, tuple))
            and len(turn) == 2
            and all(isinstance(value, str) for value in turn)
            for turn in transcript
        ):
            raise ValueError("checkpoint transcript is invalid")
        values["transcript"] = [tuple(turn) for turn in transcript]
    extraction = values.get("extraction")
    if isinstance(extraction, dict):
        values["extraction"] = ExtractionMetrics(**extraction)
    elif extraction is not None and not isinstance(extraction, ExtractionMetrics):
        raise ValueError("checkpoint extraction metrics are invalid")
    return RunResult(**values)


def load_checkpointed_results(output: str | Path) -> list[RunResult]:
    """Load only complete rows from a partial campaign checkpoint."""
    path = Path(output) / RUNS_FILENAME
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("checkpoint redteam_runs.json must be an array of objects")
    return [_result_from_payload(row) for row in payload]


def pending_matrix_jobs(
    selected: list[EvaluationScenario],
    personas: list[str],
    repeat_count: int,
    completed: set[tuple[str, str, int]],
) -> list[tuple[int, EvaluationScenario, int, str, int]]:
    """Return stable matrix coordinates not already present in the checkpoint."""
    jobs: list[tuple[int, EvaluationScenario, int, str, int]] = []
    for scenario_index, scenario in enumerate(selected, start=1):
        for persona_index, persona in enumerate(personas, start=1):
            for replicate in range(1, repeat_count + 1):
                key = (scenario.key, persona, replicate)
                if key not in completed:
                    jobs.append((scenario_index, scenario, persona_index, persona, replicate))
    return jobs


def _load_resume_campaign(
    parser: argparse.ArgumentParser,
    output: Path,
) -> tuple[dict[str, Any], list[EvaluationScenario], list[str], list[RunResult]]:
    if output.is_symlink() or not output.is_dir():
        parser.error("--resume must name an existing non-symlink result directory")
    if (output / MANIFEST_FILENAME).exists():
        parser.error("the requested campaign is already finalized and cannot be resumed")
    metadata_path = output / METADATA_FILENAME
    if not metadata_path.is_file():
        parser.error("the requested campaign has no resumable metadata")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        results = load_checkpointed_results(output)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(f"the requested campaign checkpoint is invalid: {exc}")
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 3:
        parser.error("the requested campaign predates resumable metadata schema 3")

    scenario_map = {scenario.key: scenario for scenario in DEFAULT_SCENARIOS}
    scenario_keys = metadata.get("scenarios")
    personas = metadata.get("personas")
    if not isinstance(scenario_keys, list) or not scenario_keys:
        parser.error("resume metadata does not declare scenarios")
    if not isinstance(personas, list) or not personas:
        parser.error("resume metadata does not declare personas")
    if any(key not in scenario_map for key in scenario_keys):
        parser.error("resume metadata references a scenario missing from this source revision")
    if any(persona not in PERSONAS for persona in personas):
        parser.error("resume metadata references a persona missing from this source revision")
    selected = [scenario_map[key] for key in scenario_keys]

    expected = {
        (scenario.key, persona, replicate)
        for scenario in selected
        for persona in personas
        for replicate in range(1, int(metadata.get("repeat_count") or 0) + 1)
    }
    actual = [(row.scenario, row.persona, row.replicate) for row in results]
    if len(actual) != len(set(actual)):
        parser.error("resume checkpoint contains duplicate matrix rows")
    if not set(actual).issubset(expected):
        parser.error("resume checkpoint contains rows outside its declared matrix")
    if int(metadata.get("runs") or 0) != len(results):
        parser.error("resume metadata run count does not match the checkpoint")
    return metadata, selected, list(personas), results


def _write_progress(
    output: Path,
    metadata: dict[str, Any],
    state: str,
    *,
    current: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    _atomic_json(
        output / PROGRESS_FILENAME,
        {
            "schema": "hive.redteam.progress",
            "schema_version": 1,
            "run_id": metadata["run_id"],
            "state": state,
            "created_utc": metadata["created_utc"],
            "updated_utc": datetime.now(UTC).isoformat(),
            "expected_runs": metadata["expected_runs"],
            "completed_runs": metadata["runs"],
            "current": current,
            "error": error,
        },
    )


def _new_metadata(
    *,
    output: Path,
    created: datetime,
    selected: list[EvaluationScenario],
    personas: list[str],
    repeat_count: int,
    max_turns: int,
    no_character_judge: bool,
    regex_only: bool,
    live_sandbox: bool,
    settings: Any,
    signing_fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "character_rubric": RUBRIC_VERSION,
        "character_judge": (
            "disabled; human review required"
            if no_character_judge
            else "configured strong model; provisional scoring, not independent human validation"
        ),
        "created_utc": created.isoformat(),
        "updated_utc": created.isoformat(),
        "run_id": output.name,
        "output_policy": "timestamped_append_only_resumable",
        "scenario_set": "default-v2-multimodal",
        "scenario_count": len(selected),
        "scenarios": [scenario.key for scenario in selected],
        "languages": sorted({scenario.language for scenario in selected}),
        "personas": personas,
        "persona_count": len(personas),
        "repeat_count": repeat_count,
        "expected_runs": len(selected) * len(personas) * repeat_count,
        "runs": 0,
        "max_turns": max_turns,
        "ner_mode": "regex_only" if regex_only else "regex_plus_gliner",
        "sandbox_mode": "configured_live" if live_sandbox else "deterministic_stub",
        "threat_intelligence_mode": "deterministic_no_network_fixture",
        "models": {
            "cheap": settings.llm_model_cheap,
            "strong": settings.llm_model_strong,
            "light": settings.llm_model_light,
        },
        "signing_key_fingerprint": signing_fingerprint,
        "resume_count": 0,
        "resumed_utc": [],
        "message_recording": {
            "schema": "line-separated-bubbles-v1",
            "scammer_output": (
                "Each non-empty model-output line is recorded as a distinct inbound "
                "chat message and processed with adjacent lines as one burst."
            ),
            "hive_output": (
                "The actual middleware-planned outbound bubbles are recorded separately."
            ),
            "content_policy": (
                "Segmentation removes blank lines and surrounding whitespace but does "
                "not rewrite the model's words."
            ),
        },
        "limitations": (
            "Inputs and scoring are versioned, but remote generative model outputs may "
            "vary between executions. Use the offline tests for bit-for-bit deterministic "
            "regression and retain every raw live run."
        ),
    }


def _parse_args() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="evaluation/results/redteam",
        help="Result root; each new invocation creates a timestamped child directory",
    )
    parser.add_argument(
        "--resume",
        help="Resume an interrupted schema-3 timestamped result directory",
    )
    parser.add_argument("--max-turns", type=int)
    parser.add_argument(
        "--no-character-judge",
        action="store_true",
        help="Skip the provisional model judge; retain turns for human review, not as passes",
    )
    parser.add_argument("--persona", action="append", choices=sorted(PERSONAS))
    parser.add_argument("--scenario", action="append")
    parser.add_argument(
        "--language",
        action="append",
        choices=sorted({scenario.language for scenario in DEFAULT_SCENARIOS}),
        help="Limit the matrix to one or more scenario languages",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        help="Run each selected scenario/persona combination this many times",
    )
    parser.add_argument(
        "--regex-only",
        action="store_true",
        help="Skip GLiNER loading and record a regex-only run",
    )
    parser.add_argument(
        "--live-sandbox",
        action="store_true",
        help="Use the configured disposable Docker browser instead of the safe deterministic stub",
    )
    return parser, parser.parse_args()


def main() -> None:
    parser, args = _parse_args()
    resume = Path(args.resume).resolve() if args.resume else None
    if resume and any(
        (
            args.max_turns is not None,
            args.repeat is not None,
            bool(args.persona),
            bool(args.scenario),
            bool(args.language),
            args.no_character_judge,
            args.regex_only,
            args.live_sandbox,
        )
    ):
        parser.error("--resume reuses the recorded matrix and cannot be combined with run options")

    settings = load_settings()
    signing_key = Path(settings.signing_key_path)
    if not settings.llm_api_key:
        parser.error("HIVE_LLM_API_KEY is required for the model-driven red-team suite")
    if not signing_key.is_file():
        parser.error(f"signing key not found: {signing_key}")
    signing_fingerprint = str(signing_key_details(signing_key)["fingerprint"])

    if resume:
        output = resume
        metadata, selected, personas, results = _load_resume_campaign(parser, output)
        max_turns = int(metadata["max_turns"])
        repeat_count = int(metadata["repeat_count"])
        no_character_judge = str(metadata["character_judge"]).startswith("disabled")
        regex_only = metadata["ner_mode"] == "regex_only"
        live_sandbox = metadata["sandbox_mode"] != "deterministic_stub"
        current_models = {
            "cheap": settings.llm_model_cheap,
            "strong": settings.llm_model_strong,
            "light": settings.llm_model_light,
        }
        if metadata.get("models") != current_models:
            parser.error("configured model roles differ from the recorded campaign")
        if metadata.get("signing_key_fingerprint") != signing_fingerprint:
            parser.error("the evidence signing key differs from the recorded campaign")
        resumed_utc = datetime.now(UTC).isoformat()
        metadata["resume_count"] = int(metadata.get("resume_count") or 0) + 1
        metadata.setdefault("resumed_utc", []).append(resumed_utc)
        metadata["updated_utc"] = resumed_utc
    else:
        max_turns = args.max_turns if args.max_turns is not None else 20
        repeat_count = args.repeat if args.repeat is not None else 1
        if not 1 <= max_turns <= 100:
            parser.error("--max-turns must be between 1 and 100")
        if not 1 <= repeat_count <= 20:
            parser.error("--repeat must be between 1 and 20")
        selected = [
            scenario
            for scenario in DEFAULT_SCENARIOS
            if (not args.scenario or scenario.key in set(args.scenario))
            and (not args.language or scenario.language in set(args.language))
        ]
        if args.scenario and len(selected) != len(set(args.scenario)):
            available = ", ".join(scenario.key for scenario in DEFAULT_SCENARIOS)
            parser.error(f"unknown scenario; available values: {available}")
        if not selected:
            parser.error("the selected scenario and language filters produced an empty matrix")
        personas = args.persona or list(PERSONAS)
        created = datetime.now(UTC)
        output = timestamped_output_directory(args.output, created).resolve()
        output.mkdir(parents=True, exist_ok=False)
        results = []
        no_character_judge = args.no_character_judge
        regex_only = args.regex_only
        live_sandbox = args.live_sandbox
        metadata = _new_metadata(
            output=output,
            created=created,
            selected=selected,
            personas=personas,
            repeat_count=repeat_count,
            max_turns=max_turns,
            no_character_judge=no_character_judge,
            regex_only=regex_only,
            live_sandbox=live_sandbox,
            settings=settings,
            signing_fingerprint=signing_fingerprint,
        )

    evidence = output / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    metadata["runs"] = len(results)
    _atomic_json(output / METADATA_FILENAME, metadata)
    _write_progress(output, metadata, "initializing")
    print(f"Campaign directory: {output}", flush=True)
    print(
        f"Matrix: {len(selected)} scenarios x {len(personas)} personas x "
        f"{repeat_count} repeats = {metadata['expected_runs']} runs",
        flush=True,
    )
    if results:
        print(f"Resuming after {len(results)} checkpointed runs", flush=True)

    completed_keys = {(row.scenario, row.persona, row.replicate) for row in results}
    jobs = pending_matrix_jobs(selected, personas, repeat_count, completed_keys)

    try:
        evaluation_settings = settings.model_copy(
            update={"database_url": "", "use_case_similarity": False}
        )
        engine = build_engine(evaluation_settings, load_ner=not regex_only)
        engine.case_intelligence = None
        engine.threat_intelligence = build_synthetic_threat_intelligence_service()
        engine.enable_early_exit = False
        engine.max_turns = 0
        engine.max_session_minutes = 0
        if not live_sandbox:
            engine.sandbox_runner = EvaluationSandboxRunner()

        for scenario_index, scenario, persona_index, persona, replicate in jobs:
            ordinal = len(results) + 1
            current = {
                "ordinal": ordinal,
                "scenario": scenario.key,
                "persona": persona,
                "replicate": replicate,
            }
            _write_progress(output, metadata, "running", current=current)
            print(
                f"[{ordinal}/{metadata['expected_runs']}] START "
                f"{scenario.key}/{persona}/repeat-{replicate}",
                flush=True,
            )
            filename = f"bundle_{scenario_index}_{persona_index}_{replicate}.pdf"
            result = run_conversation(
                engine.agent_client,
                engine.agent_client,
                ARCHETYPES[scenario.archetype],
                persona,
                max_turns=max_turns,
                opener=scenario.opener,
                engine=engine,
                expected_hvis=scenario.expected_hvis,
                expected_verdict=scenario.expected_verdict,
                evidence_pdf=evidence / filename,
                signing_key_path=signing_key,
                scenario_key=scenario.key,
                replicate=replicate,
                language=scenario.language,
                opener_fixtures=scenario.fixture_keys,
                character_client=None if no_character_judge else engine.agent_client,
            )
            results.append(result)
            paths = write_results(results, output)
            metadata["runs"] = len(results)
            metadata["updated_utc"] = datetime.now(UTC).isoformat()
            _atomic_json(output / METADATA_FILENAME, metadata)
            _write_progress(output, metadata, "running")
            print(
                f"[{ordinal}/{metadata['expected_runs']}] DONE "
                f"verdict={result.verdict} indicators={len(result.hvi_items)} "
                f"chain={result.chain_valid} evidence={result.evidence_verified}",
                flush=True,
            )
    except BaseException as exc:
        metadata["runs"] = len(results)
        metadata["updated_utc"] = datetime.now(UTC).isoformat()
        _atomic_json(output / METADATA_FILENAME, metadata)
        _write_progress(
            output,
            metadata,
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    paths = write_results(results, output)
    metadata["runs"] = len(results)
    metadata["updated_utc"] = datetime.now(UTC).isoformat()
    _atomic_json(output / METADATA_FILENAME, metadata)
    print(json.dumps(aggregate_results(results), indent=2), flush=True)
    print(f"Raw results: {paths['runs']}", flush=True)
    print(f"CSV: {paths['csv']}", flush=True)
    print(f"Metadata: {output / METADATA_FILENAME}", flush=True)
    failed = [
        result
        for result in results
        if not (
            result.chain_valid
            and result.evidence_verified
            and result.termination_reason == "max_exchanges"
            and (result.character_assessment or {}).get("status") != "error"
        )
    ]
    if failed:
        _write_progress(
            output,
            metadata,
            "quality_failed",
            error=f"{len(failed)} completed runs failed the final quality gate",
        )
        sys.exit(1)
    _write_progress(output, metadata, "complete")
    manifest_path = finalize_result_directory(output)
    print(f"Integrity manifest: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
