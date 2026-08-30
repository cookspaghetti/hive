"""Run the versioned HIVE red-team matrix and retain appendix-ready outputs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from hive.agent.personas import PERSONAS
from hive.config import load_settings
from hive.redteam.character import RUBRIC_VERSION
from hive.redteam.runner import (
    EvaluationSandboxRunner,
    aggregate_results,
    run_conversation,
    write_results,
)
from hive.redteam.scammer import ARCHETYPES
from hive.redteam.scenarios import DEFAULT_SCENARIOS
from hive.runtime import build_engine


def timestamped_output_directory(root: str | Path, created: datetime) -> Path:
    """Return an append-only run directory beneath the configured result root."""
    run_id = created.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return Path(root) / run_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="evaluation/results/redteam",
        help="Result root; each invocation creates a timestamped child directory",
    )
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument(
        "--no-character-judge", action="store_true",
        help="Skip the provisional model judge; retain turns for human review, not as passes",
    )
    parser.add_argument("--persona", action="append", choices=sorted(PERSONAS))
    parser.add_argument("--scenario", action="append")
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
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 100:
        parser.error("--max-turns must be between 1 and 100")

    settings = load_settings()
    signing_key = Path(settings.signing_key_path)
    if not settings.llm_api_key:
        parser.error("HIVE_LLM_API_KEY is required for the model-driven red-team suite")
    if not signing_key.is_file():
        parser.error(f"signing key not found: {signing_key}")

    selected = [
        scenario
        for scenario in DEFAULT_SCENARIOS
        if not args.scenario or scenario.key in set(args.scenario)
    ]
    if args.scenario and len(selected) != len(set(args.scenario)):
        available = ", ".join(scenario.key for scenario in DEFAULT_SCENARIOS)
        parser.error(f"unknown scenario; available values: {available}")
    personas = args.persona or list(PERSONAS)
    created = datetime.now(UTC)
    output = timestamped_output_directory(args.output, created)
    evidence = output / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    # Evaluation must not index synthetic cases into the operational case store.
    evaluation_settings = settings.model_copy(
        update={"database_url": "", "use_case_similarity": False}
    )
    engine = build_engine(evaluation_settings, load_ner=not args.regex_only)
    engine.case_intelligence = None
    engine.enable_early_exit = False
    engine.max_turns = 0  # The harness counts response exchanges, not inbound bubbles.
    engine.max_session_minutes = 0
    if not args.live_sandbox:
        engine.sandbox_runner = EvaluationSandboxRunner()

    results = []
    for scenario_index, scenario in enumerate(selected, start=1):
        for persona_index, persona in enumerate(personas, start=1):
            filename = f"bundle_{scenario_index}_{persona_index}.pdf"
            result = run_conversation(
                engine.agent_client,
                engine.agent_client,
                ARCHETYPES[scenario.archetype],
                persona,
                max_turns=args.max_turns,
                opener=scenario.opener,
                engine=engine,
                expected_hvis=scenario.expected_hvis,
                expected_verdict=scenario.expected_verdict,
                evidence_pdf=evidence / filename,
                signing_key_path=signing_key,
                scenario_key=scenario.key,
                language=scenario.language,
                opener_fixtures=scenario.fixture_keys,
                character_client=None if args.no_character_judge else engine.agent_client,
            )
            results.append(result)
            # Preserve every finished run even if a later model/evidence operation fails.
            write_results(results, output)

    paths = write_results(results, output)
    metadata = {
        "schema_version": 2,
        "character_rubric": RUBRIC_VERSION,
        "character_judge": (
            "disabled; human review required" if args.no_character_judge
            else "configured strong model; provisional scoring, not independent human validation"
        ),
        "created_utc": created.isoformat(),
        "run_id": output.name,
        "output_policy": "timestamped_append_only",
        "scenario_set": "default-v2-multimodal",
        "scenario_count": len(selected),
        "personas": personas,
        "runs": len(results),
        "max_turns": args.max_turns,
        "ner_mode": "regex_only" if args.regex_only else "regex_plus_gliner",
        "sandbox_mode": (
            type(engine.sandbox_runner).__name__
            if args.live_sandbox
            else "deterministic_stub"
        ),
        "models": {
            "cheap": settings.llm_model_cheap,
            "strong": settings.llm_model_strong,
            "light": settings.llm_model_light,
        },
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
    metadata_path = output / "redteam_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(aggregate_results(results), indent=2))
    print(f"Raw results: {paths['runs']}")
    print(f"CSV: {paths['csv']}")
    print(f"Metadata: {metadata_path}")
    if not all(
        result.chain_valid and result.evidence_verified
        and result.termination_reason == "max_exchanges"
        and (result.character_assessment or {}).get("status") != "error"
        for result in results
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()
