"""Red-team harness smoke test (fyp.txt S9), fully offline."""

import json

from hive.extraction.engine import extract_hvis
from hive.redteam.runner import (
    aggregate_results,
    run_conversation,
    split_model_messages,
    write_results,
)
from hive.redteam.scammer import ARCHETYPES
from hive.redteam.scenarios import DEFAULT_SCENARIOS
from hive.vault.signer import generate_keypair
from tests.fakes import fake_client


def test_run_conversation_offline():
    result = run_conversation(
        agent_client=fake_client("hello ah, who is this?"),
        scammer_client=fake_client("transfer to Maybank 1234567890 now!"),
        archetype=ARCHETYPES["investment"],
        persona="confused_elderly",
        max_turns=3,
    )
    assert result.turns == 3
    # transcript alternates scammer/victim, 2 entries per turn
    assert len(result.transcript) == 6
    assert result.agent_tiers == ["cheap", "cheap", "cheap"]


def test_model_lines_are_recorded_as_distinct_chat_messages():
    assert split_model_messages("first\n\n second \n- third") == (
        "first",
        "second",
        "- third",
    )
    result = run_conversation(
        agent_client=fake_client("one thought\n\nsecond thought"),
        scammer_client=fake_client("follow up one\nfollow up two"),
        archetype=ARCHETYPES["investment"],
        persona="confused_elderly",
        max_turns=2,
        opener="opening one\n\nopening two",
    )

    assert result.turns == 4
    assert result.exchanges == 2
    assert result.scored_message_ids == [0, 1]
    assert [speaker for speaker, _ in result.transcript] == [
        "scammer",
        "scammer",
        "victim",
        "victim",
        "scammer",
        "scammer",
        "victim",
        "victim",
    ]
    assert all("\n" not in text and "\r" not in text for _, text in result.transcript)


def test_run_conversation_exercises_pipeline_metrics_and_evidence(tmp_path):
    private_key = tmp_path / "evaluation-key.pem"
    generate_keypair(str(private_key))
    result = run_conversation(
        agent_client=fake_client(
            '{"urgency": 0.95, "payment_request": 0.95, '
            '"authority_impersonation": 0.8}'
        ),
        scammer_client=fake_client(
            "You are definitely a bot. Transfer to Maybank 1234567890 now!"
        ),
        archetype=ARCHETYPES["impersonation"],
        persona="confused_elderly",
        max_turns=2,
        opener=(
            "Are you a bot? Transfer to Maybank 1234567890 and open "
            "https://bad.example/login now"
        ),
        expected_hvis={
            ("bank_account", "1234567890"),
            ("url", "https://bad.example/login"),
        },
        evidence_pdf=tmp_path / "bundle_1.pdf",
        signing_key_path=private_key,
    )

    assert result.turns == 2
    assert result.exchanges == 2
    assert result.extraction is not None
    assert result.extraction.true_positive == 2
    assert result.verdict == "likely_scam"
    assert result.verdict_correct is True
    assert result.sandbox_runs == 1
    assert result.bot_detected is True and result.bot_probes == 2
    assert result.chain_valid is True
    assert result.evidence_verified is True
    assert all(result.evidence_checks.values())

    summary = aggregate_results([result])
    assert summary["runs"] == 1
    assert summary["verdict_accuracy"] == 1.0
    assert summary["bot_detection_rate"] == 1.0
    paths = write_results([result], tmp_path / "results")
    assert json.loads(paths["summary"].read_text(encoding="utf-8"))["runs"] == 1
    assert paths["csv"].read_text(encoding="utf-8").startswith("archetype,persona")


def test_all_required_scam_archetypes_are_defined():
    assert {
        "investment",
        "job",
        "parcel",
        "impersonation",
        "romance",
        "ecommerce",
        "mixed",
    }.issubset(ARCHETYPES)


def test_versioned_scenario_labels_match_deterministic_extraction():
    assert len(DEFAULT_SCENARIOS) == 15
    assert {scenario.language for scenario in DEFAULT_SCENARIOS} == {
        "English",
        "Mandarin",
        "Manglish",
    }
    for scenario in DEFAULT_SCENARIOS:
        predicted = {(item.kind, item.value) for item in extract_hvis(scenario.opener, 1)}
        assert predicted == set(scenario.expected_hvis), scenario.key
