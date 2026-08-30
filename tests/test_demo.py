"""Isolated live-demo lifecycle tests, fully offline."""

from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass

from hive.audit import DurableAuditLedger, audit_event, audit_scope
from hive.demo import MODES, SCENARIOS, DemoService
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.scenario_media import FIXTURES, validate_fixtures
from hive.vault.signer import generate_keypair
from tests.fakes import fake_client


class NeverUsedSandbox:
    def run(self, url: str) -> RawFindings:  # pragma: no cover - replaced by demo seam
        raise AssertionError(url)


class RecordingSandbox:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def run(self, url: str) -> RawFindings:
        self.urls.append(url)
        return RawFindings(
            final_url=url,
            redirect_chain=[url],
            dest_ip="1.1.1.1",
            title="Public security test",
            body_len=1200,
            http_status=200,
            certificate_age_days=120,
            runtime_ms=25,
            fetcher="test_disposable_container",
            access_state="reached",
        )


class RecordingThreatIntelligence:
    configured = {
        "semak_mule": True,
        "virus_total": True,
        "abuse_ipdb": True,
        "rdap": True,
    }

    def __init__(self) -> None:
        self.force_values: list[bool] = []

    def enrich(self, session, indicators=None, messages=None, *, force=False):
        del messages
        self.force_values.append(force)
        results = [
            {
                "id": f"live-{item.source_msg_id}",
                "provider": "virus_total",
                "provider_label": "VirusTotal",
                "indicator_kind": item.kind,
                "observable": item.value,
                "source_msg_id": item.source_msg_id,
                "status": "hit",
                "risk": "malicious",
                "summary": "Public test observation.",
                "facts": {"malicious": 1},
                "checked_ts": time.time(),
                "cached": False,
            }
            for item in indicators or []
            if item.kind == "url"
        ]
        session.threat_intelligence.extend(results)
        return results


@dataclass
class Settings:
    signing_key_path: str


@dataclass
class Runtime:
    engine: HiveEngine
    settings: Settings
    is_running: bool = True


def _service(tmp_path, *, sandbox=None, threat_intelligence=None) -> DemoService:
    private_key = tmp_path / "signing.pem"
    public_key = tmp_path / "signing.pub.pem"
    generate_keypair(str(private_key), str(public_key))
    engine = HiveEngine(
        agent_client=fake_client(
            '{"urgency": 0.9, "payment_request": 0.9, "investment_framing": 0.8}'
        ),
        sandbox_runner=sandbox or NeverUsedSandbox(),
        threat_intelligence=threat_intelligence,
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
    assert len(SCENARIOS) == 30
    for scenario in SCENARIOS.values():
        assert len(scenario.bursts) >= 3
        assert all(
            "\n" not in message.text
            for burst in scenario.bursts
            for message in scenario.events(burst)
        )
        for burst in scenario.bursts:
            for message in scenario.events(burst):
                if "http" in message.text:
                    if scenario.live_services:
                        assert any(
                            allowed in message.text
                            for allowed in (
                                "testsafebrowsing.appspot.com",
                                "example.com",
                            )
                        )
                    else:
                        assert ".example" in message.text


def test_multimodal_fixtures_are_inert_and_scenarios_reference_them():
    manifest = validate_fixtures()

    assert {item["key"] for item in manifest} == set(FIXTURES)
    assert all(item["safe_fixture"] for item in manifest)
    assert {fixture.kind for fixture in FIXTURES.values()} >= {"image", "document", "file"}
    assert any(
        event.fixture
        for scenario in SCENARIOS.values()
        for burst in scenario.bursts
        for event in scenario.events(burst)
    )
    apk = FIXTURES["delivery_apk"].path.read_bytes()
    assert not apk.startswith((b"PK\x03\x04", b"dex\n", b"\x7fELF", b"MZ"))


def test_catalog_exposes_all_three_demo_modes():
    catalog = DemoService.catalog()

    assert [item["key"] for item in catalog["modes"]] == list(MODES)
    assert catalog["modes"][0]["key"] == "scripted"
    assert catalog["modes"][0]["recommended"] is True
    assert catalog["fixture_policy"]["executable_content"] is False
    live = [item for item in catalog["scenarios"] if item["live_services"]]
    assert len(live) == 3
    assert all(item["provider_mode"] == "live" for item in live)
    assert all(item["category"] for item in catalog["scenarios"])
    assert len({item["category"] for item in catalog["scenarios"]}) >= 10
    assert sum(
        item["reference"] == "Reddit r/malaysia scam-awareness post"
        for item in catalog["scenarios"]
    ) == 19
    assert all(
        "Malaysia pattern" in item["tags"]
        for item in catalog["scenarios"]
        if item["reference"] == "Reddit r/malaysia scam-awareness post"
    )
    assert catalog["live_showcase_policy"]["hardcoded_public_observables_only"] is True


def test_live_osint_showcase_uses_real_service_seams_without_creating_evidence(tmp_path):
    sandbox = RecordingSandbox()
    threat_intelligence = RecordingThreatIntelligence()
    service = _service(
        tmp_path,
        sandbox=sandbox,
        threat_intelligence=threat_intelligence,
    )

    started = service.start(
        "osint_malicious_url",
        "confused_elderly",
        "5x",
        mode="scripted",
    )
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    assert run["status"] == "completed", run.get("error")
    assert run["live_services"] is True
    assert run["provider_mode"] == "live"
    assert run["sandbox_mode"] == "disposable_container"
    assert sandbox.urls == ["http://testsafebrowsing.appspot.com/s/malware.html"]
    assert threat_intelligence.force_values and all(threat_intelligence.force_values)
    assert run["sandbox_results"][0]["fetcher"] == "test_disposable_container"
    assert run["threat_intelligence"][0]["provider"] == "virus_total"
    assert run["threat_intelligence"][0]["cached"] is False
    assert run["evidence_available"] is False
    assert run["evidence_download_url"] is None


def test_live_osint_showcase_rejects_generated_or_presenter_input(tmp_path):
    service = _service(
        tmp_path,
        sandbox=RecordingSandbox(),
        threat_intelligence=RecordingThreatIntelligence(),
    )

    for mode in ("model_driven", "interactive"):
        try:
            service.start("osint_malicious_url", "confused_elderly", "5x", mode=mode)
        except ValueError as exc:
            assert "scripted mode only" in str(exc)
        else:  # pragma: no cover - safety invariant
            raise AssertionError(f"unsafe showcase mode accepted: {mode}")


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
    assert {item["provider"] for item in run["threat_intelligence"]} >= {
        "semak_mule",
        "virus_total",
        "rdap",
    }
    assert all(
        item["status"] == "synthetic_fixture"
        and item["facts"]["external_lookup"] is False
        for item in run["threat_intelligence"]
    )
    assert run["audit"]["valid"] is True
    assert run["evidence_verified"] is True


def test_multimodal_demo_records_and_serves_fixture_metadata(tmp_path):
    service = _service(tmp_path)

    started = service.start("parcel", "confused_elderly", "5x")
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    attachments = [item for item in run["messages"] if item.get("media_kind")]
    assert run["status"] == "completed", run.get("error")
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment["media_name"] == "parcel-release-notice.png"
    assert attachment["media_sha256"]
    assert attachment["media_url"].endswith(f"/media/{attachment['msg_id']}")
    path, name, mime = service.media(run["id"], attachment["msg_id"])
    assert path.is_file()
    assert name == "parcel-release-notice.png"
    assert mime == "image/svg+xml"
    evidence = service.evidence_path(run["id"])
    assert evidence.is_file()
    with zipfile.ZipFile(evidence) as archive:
        assert "attachment_001_parcel-release-notice.png" in archive.namelist()
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

    stranger_messages = [item["text"] for item in run["messages"] if item["role"] == "stranger"]
    assert run["status"] == "completed", run.get("error")
    assert run["mode"] == "model_driven"
    assert stranger_messages[:2] == list(SCENARIOS["investment"].bursts[0])
    assert all("\n" not in text for text in stranger_messages)
    assert sum(item["category"] == "scammer_model" for item in run["timeline"]) == 2
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
        lambda item: item["status"] == "awaiting_input" and item["current_exchange"] == 1,
    )
    service.finish(started["id"])
    run = _wait_for(
        service,
        started["id"],
        lambda item: item["status"] in {"completed", "failed", "cancelled"},
    )

    stranger_messages = [item["text"] for item in run["messages"] if item["role"] == "stranger"]
    assert run["status"] == "completed", run.get("error")
    assert run["mode"] == "interactive"
    assert stranger_messages == ["Your parcel is held. Pay the release fee."]
    assert run["evidence_verified"] is True
