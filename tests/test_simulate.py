"""Focused tests for the transport-free conversation simulator."""

from io import StringIO
from pathlib import Path

import pytest

from hive.runtime import HiveEngine, TurnOutput
from hive.simulate import (
    SimulationHarness,
    configure_console_encoding,
    parse_burst,
    render_turn,
)
from tests.fakes import fake_client


class _CleanRunner:
    def run(self, url):
        raise AssertionError(f"sandbox should not run for this message: {url}")


def _harness(reply: str = "wait ah ||| which account?") -> SimulationHarness:
    engine = HiveEngine(
        agent_client=fake_client(reply),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=False,
    )
    return SimulationHarness(
        engine,
        peer_id=-123,
        persona="confused_elderly",
        clock=lambda: 100.0,
    )


def test_harness_sends_a_burst_through_one_real_exchange():
    harness = _harness()

    output = harness.send(["hello", "are you there?"])

    assert harness.session.turn_count == 2
    assert harness.session.exchange_count == 1
    assert [message.text for message in harness.session.messages[:2]] == [
        "hello",
        "are you there?",
    ]
    assert output.messages
    assert harness.chain.verify()
    assert "inbound=2 exchanges=1" in harness.status()


def test_harness_assigns_unique_synthetic_inbound_ids_across_exchanges():
    harness = _harness("ok")

    harness.send(["first"])
    harness.send(["second", "third"])

    inbound_ids = [
        message.msg_id for message in harness.session.messages if message.role == "stranger"
    ]
    assert inbound_ids == [1, 2, 3]
    assert harness.session.exchange_count == 2


def test_parse_burst_uses_explicit_phone_check_separator():
    assert parse_burst(" hello ||| are you there? ||| pay now ") == [
        "hello",
        "are you there?",
        "pay now",
    ]
    with pytest.raises(ValueError, match="at least one"):
        parse_burst(" ||| ")


def test_render_turn_shows_planned_delays_without_sleeping():
    stream = StringIO()
    output = TurnOutput(
        text="first second",
        messages=("first", "second"),
        message_delays_s=(2.0, 0.8),
    )

    render_turn(output, stream)

    assert stream.getvalue().splitlines() == [
        "HIVE [normal] (+2.0s planned, 0.0s typing): first",
        "HIVE [normal] (+0.8s planned, 0.0s typing): second",
    ]


def test_console_streams_are_reconfigured_for_multilingual_replies(monkeypatch):
    class _Stream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    stdout = _Stream()
    stderr = _Stream()
    monkeypatch.setattr("hive.simulate.sys.stdout", stdout)
    monkeypatch.setattr("hive.simulate.sys.stderr", stderr)

    configure_console_encoding()

    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_taskfile_exposes_simulator():
    taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text()
    assert "uv run python -m hive.simulate {{.CLI_ARGS}}" in taskfile
