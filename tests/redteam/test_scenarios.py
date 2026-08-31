"""Catalogue-level guarantees for controlled evaluation scenarios."""

from hive.extraction.regex_rules import extract_regex
from hive.redteam.scenarios import DEFAULT_SCENARIOS
from hive.scenario_media import FIXTURES, validate_fixtures

_REQUIRED_ARCHETYPES = {
    "investment",
    "job",
    "parcel",
    "impersonation",
    "romance",
    "ecommerce",
    "mixed",
}


def test_scenario_catalogue_has_exact_count_and_unique_keys():
    keys = [scenario.key for scenario in DEFAULT_SCENARIOS]

    assert len(keys) == len(set(keys)) == 30


def test_scenario_catalogue_balances_languages_and_covers_every_archetype():
    language_counts = {
        language: sum(scenario.language == language for scenario in DEFAULT_SCENARIOS)
        for language in {scenario.language for scenario in DEFAULT_SCENARIOS}
    }

    assert language_counts == {"English": 14, "Mandarin": 13, "Manglish": 3}
    assert abs(language_counts["English"] - language_counts["Mandarin"]) <= 1
    assert {scenario.archetype for scenario in DEFAULT_SCENARIOS} == _REQUIRED_ARCHETYPES
    assert all(
        sum(scenario.archetype == archetype for scenario in DEFAULT_SCENARIOS) >= 4
        for archetype in _REQUIRED_ARCHETYPES
    )


def test_scenario_ground_truth_is_non_empty_precise_and_reserved():
    for scenario in DEFAULT_SCENARIOS:
        assert scenario.expected_hvis, scenario.key
        assert len(scenario.expected_hvis) == len(set(scenario.expected_hvis)), scenario.key
        predicted = {(item.kind, item.value) for item in extract_regex(scenario.opener, 1)}
        predicted.update(
            indicator
            for fixture_key in scenario.fixture_keys
            for indicator in FIXTURES[fixture_key].hvis
        )
        assert predicted == set(scenario.expected_hvis), scenario.key
        for kind, value in scenario.expected_hvis:
            assert kind and value.strip(), scenario.key
            if kind == "url":
                assert ".example" in value, (scenario.key, value)


def test_scenario_fixture_references_are_valid_inert_and_declared():
    validated = {item["key"]: item for item in validate_fixtures()}

    for scenario in DEFAULT_SCENARIOS:
        for key in scenario.fixture_keys:
            assert key in FIXTURES, (scenario.key, key)
            assert key in validated, (scenario.key, key)
            assert validated[key]["safe_fixture"] is True
            assert set(FIXTURES[key].hvis).issubset(set(scenario.expected_hvis))
