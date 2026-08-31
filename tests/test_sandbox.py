"""L4 forensic sandbox tests (fyp.txt L4), fully offline (fake runner)."""

from hive.sandbox.analyzer import analyze_url
from hive.sandbox.runner import RawFindings


class FakeRunner:
    def __init__(self, findings: RawFindings):
        self._f = findings

    def run(self, url: str) -> RawFindings:
        return self._f


def test_rejects_non_http_scheme():
    r = analyze_url("ftp://evil.co/file", FakeRunner(RawFindings()))
    assert r["verdict_signal"] == "error"


def test_credential_page_after_cross_domain_is_malicious():
    f = RawFindings(
        final_url="http://phish-login.ru/signin",
        redirect_chain=["http://bit.ly/x", "http://phish-login.ru/signin"],
        has_password_field=True,
        title="Bank Login",
        body_len=5000,
    )
    r = analyze_url("http://my-bank.example/promo", FakeRunner(f))
    assert r["verdict_signal"] == "malicious"
    assert r["cloaking_suspected"] is False


def test_password_field_same_domain_is_suspicious():
    f = RawFindings(
        final_url="http://site.example/login",
        has_password_field=True,
        body_len=3000,
        title="Login",
    )
    r = analyze_url("http://site.example/home", FakeRunner(f))
    assert r["verdict_signal"] == "suspicious"


def test_many_redirects_is_suspicious():
    f = RawFindings(
        final_url="http://site.example/end",
        redirect_chain=["a", "b", "c"],
        body_len=3000,
        title="Ok",
    )
    r = analyze_url("http://site.example/start", FakeRunner(f))
    assert r["verdict_signal"] == "suspicious"


def test_empty_page_flags_cloaking():
    f = RawFindings(final_url="http://site.example/", body_len=50, title="")
    r = analyze_url("http://site.example/", FakeRunner(f))
    assert r["cloaking_suspected"] is True
    assert r["verdict_signal"] == "suspicious"


def test_clean_page():
    f = RawFindings(final_url="http://site.example/", body_len=8000, title="Welcome")
    r = analyze_url("http://site.example/", FakeRunner(f))
    assert r["verdict_signal"] == "clean"
    assert r["cloaking_suspected"] is False


def test_unsolved_challenge_is_inconclusive_not_clean():
    f = RawFindings(
        final_url="https://short.example/x",
        body_len=8000,
        title="Just a moment...",
        http_status=200,
        fetcher="scrapling_stealthy",
        access_state="challenge",
        challenge_detected=True,
        challenge_provider="cloudflare",
        error="sandbox timed out after 90s",
    )
    r = analyze_url("https://short.example/x", FakeRunner(f))

    assert r["verdict_signal"] == "inconclusive"
    assert r["access_state"] == "challenge"
    assert r["challenge_detected"] is True
    assert r["challenge_provider"] == "cloudflare"
    assert r["fetcher"] == "scrapling_stealthy"
    assert r["error"] == "sandbox timed out after 90s"


def test_blocked_http_response_is_inconclusive_not_clean():
    f = RawFindings(
        final_url="https://protected.example/",
        body_len=4000,
        title="Forbidden",
        http_status=403,
        fetcher="scrapling_stealthy",
        access_state="blocked",
    )
    r = analyze_url("https://protected.example/", FakeRunner(f))

    assert r["verdict_signal"] == "inconclusive"
    assert r["access_state"] == "blocked"


def test_runner_error_propagates():
    r = analyze_url("http://x.example/", FakeRunner(RawFindings(error="timeout")))
    assert r["verdict_signal"] == "error"
    assert r["error"] == "timeout"


def test_result_shape_is_sandbox_entry():
    f = RawFindings(
        final_url="https://x.example/",
        body_len=8000,
        title="Hi",
        dest_ip="1.2.3.4",
        http_status=200,
        certificate_age_days=17,
        runtime_ms=1234,
        fetcher="scrapling_stealthy",
    )
    r = analyze_url("http://x.example/", FakeRunner(f))
    # keys the Verdict Engine / Evidence Vault rely on
    for key in (
        "url",
        "verdict_signal",
        "redirect_chain",
        "dest_ip",
        "screenshot_path",
        "screenshot_error",
        "blocked_requests",
        "http_status",
        "certificate_age_days",
        "certificate_error",
        "runtime_ms",
        "fetcher",
        "sandbox_image",
        "sandbox_image_id",
        "sandbox_contract",
        "sandbox_python_version",
        "sandbox_scrapling_version",
    ):
        assert key in r
    assert r["certificate_age_days"] == 17
    assert r["runtime_ms"] == 1234
