"""Isolated live-demo lifecycle tests, fully offline."""

from __future__ import annotations

import time
from dataclasses import dataclass

from hive.audit import DurableAuditLedger, audit_event, audit_scope
from hive.demo import MODES, SCENARIOS, DemoService
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.vault.signer import generate_keypair
from tests.fakes import fake_client


class NeverUsedSandbox:
    def run(self, url: str) -> RawFindings:  # pragma: no cover - replaced by demo seam
        raise AssertionError(url)


@dataclass
class Settings:
    signing_key_path: str


@dataclass
class Runtime:
    engine: HiveEngine
    settings: Settings
    is_running: bool = True


def _service(tmp_path) -> DemoService:
    private_key = tmp_path / "signing.pem"
    public_key = tmp_path / "signing.pub.pem"
    generate_keypair(str(private_key), str(public_key))
    engine = HiveEngine(
        agent_client=fake_client(
            '{"urgency": 0.9, "payment_request": 0.9, "investment_framing": 0.8}'
        ),
        sandbox_runner=NeverUsedSandbox(),
        enable_early_exit=False,
    )
    runtime = Runtime(engine, Settings(str(private_key)))
    return DemoService(tmp_path / "demos", tmp_path, lambda: runtime)


def _wait_for(service: DemoService, run_id: str, predicate, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = service.get(run_id)
        if predicate(run):
            return run
        time.sleep(0.05)
    service.shutdown()
    raise AssertionError(f"demo did not reach expected state: {run}")


def test_scenarios_use_separate_messages_and_reserved_urls():
    assert len(SCENARIOS) >= 5
    for scenario in SCENARIOS.values():
        assert len(scenario.bursts) >= 3
        assert all("\n" not in message for burst in scenario.bursts for message in burst)
        for burst in scenario.bursts:
            for message in burst:
                if "http" in message:
                    assert ".example" in message


def test_catalog_exposes_all_three_demo_modes():
    catalog = DemoService.catalog()

    assert [item["key"] for item in catalog["modes"]] == list(MODES)
    assert catalog["modes"][0]["key"] == "scripted"
    assert catalog["modes"][0]["recommended"] is True


def test_audit_scope_keeps_synthetic_events_out_of_the_default_ledger(tmp_path):
    operational = DurableAuditLedger(tmp_path / "operational.jsonl")
    isolated = DurableAuditLedger(tmp_path / "demo.jsonl")
    with audit_scope(operational):
        audit_event("panel", "before", component="test")
        with audit_scope(isolated):
            audit_event("demo", "message", component="test", payload={"text": "synthetic"})
        audit_event("panel", "after", component="test")
    assert isolated.status()["events"] == 1
    assert operational.status()["events"] == 2


def test_demo_runs_pipeline_and_seals_isolated_evidence(tmp_path):
    service = _service(tmp_path)

    started = service.start("investment", "confused_elderly", "5x")
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    assert run["status"] == "completed", run.get("error")
    assert run["synthetic"] is True
    assert run["telegram_connected"] is False
    assert len(run["messages"]) > sum(len(burst) for burst in SCENARIOS["investment"].bursts)
    assert all("\n" not in message["text"] for message in run["messages"])
    assert {item["kind"] for item in run["hvi_items"]} >= {"url", "bank_account"}
    assert run["sandbox_results"]
    assert run["audit"]["valid"] is True
    assert run["evidence_verified"] is True
    assert service.evidence_path(run["id"]).is_file()
    assert not (tmp_path / "evaluation" / "results" / "redteam_runs.json").exists()


def test_model_driven_demo_keeps_fixed_opener_then_generates_bubbles(tmp_path):
    service = _service(tmp_path)

    started = service.start(
        "investment",
        "confused_elderly",
        "5x",
        mode="model_driven",
    )
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    stranger_messages = [
        item["text"] for item in run["messages"] if item["role"] == "stranger"
    ]
    assert run["status"] == "completed", run.get("error")
    assert run["mode"] == "model_driven"
    assert stranger_messages[:2] == list(SCENARIOS["investment"].bursts[0])
    assert all("\n" not in text for text in stranger_messages)
    assert sum(
        item["category"] == "scammer_model" for item in run["timeline"]
    ) == 2
    assert run["evidence_verified"] is True


def test_interactive_demo_accepts_one_bubble_at_a_time_and_seals(tmp_path):
    service = _service(tmp_path)

    started = service.start(
        "parcel",
        "confused_elderly",
        "normal",
        mode="interactive",
    )
    waiting = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] == "awaiting_input",
    )
    assert waiting["total_exchanges"] == 0

    service.submit_message(started["id"], "Your parcel is held.\nPay the release fee.")
    _wait_for(
        service,
        started["id"],
        lambda item: item["status"] == "awaiting_input"
        and item["current_exchange"] == 1,
    )
    service.finish(started["id"])
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    stranger_messages = [
        item["text"] for item in run["messages"] if item["role"] == "stranger"
    ]
    assert run["status"] == "completed", run.get("error")
    assert run["mode"] == "interactive"
    assert stranger_messages == ["Your parcel is held. Pay the release fee."]
    assert run["evidence_verified"] is True
