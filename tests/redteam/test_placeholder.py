"""Red-team harness smoke test (fyp.txt S9), fully offline."""

from hive.redteam.runner import run_conversation
from hive.redteam.scammer import ARCHETYPES
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
