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


def test_runner_error_propagates():
    r = analyze_url("http://x.example/", FakeRunner(RawFindings(error="timeout")))
    assert r["verdict_signal"] == "error"
    assert r["error"] == "timeout"


def test_result_shape_is_sandbox_entry():
    f = RawFindings(final_url="http://x.example/", body_len=8000, title="Hi", dest_ip="1.2.3.4")
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
    ):
        assert key in r
