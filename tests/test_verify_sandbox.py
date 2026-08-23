"""Offline tests for the live forensic sandbox verifier."""

from types import SimpleNamespace

import hive.verify_sandbox as subject
from hive.sandbox.runner import RawFindings


class FakeRunner:
    def run(self, url: str) -> RawFindings:
        if "127.0.0.1" in url:
            return RawFindings(error="destination is not public", blocked_requests=[url])
        return RawFindings(
            final_url="https://example.com/",
            http_status=200,
            access_state="reached",
            fetcher="scrapling_stealthy",
        )


def test_verify_checks_fetch_rejection_and_cleanup(monkeypatch):
    monkeypatch.setattr(
        subject,
        "configured_sandbox_runner",
        lambda **_kwargs: FakeRunner(),
    )
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = subject.verify("https://example.com")

    assert result["ok"] is True
    assert result["public_fetch"]["status"] == 200
    assert result["private_destination_rejected"] is True
    assert result["orphan_containers"] == []


def test_verify_fails_when_an_orphan_is_present(monkeypatch):
    monkeypatch.setattr(
        subject,
        "configured_sandbox_runner",
        lambda **_kwargs: FakeRunner(),
    )
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="hive-sandbox-orphan\n",
            stderr="",
        ),
    )

    result = subject.verify("https://example.com")

    assert result["ok"] is False
    assert result["orphan_containers"] == ["hive-sandbox-orphan"]
