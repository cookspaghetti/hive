"""Forensic Sandbox orchestration (fyp.txt L4).

Validates a suspect URL, runs it through a disposable browser runner, derives a
verdict signal + cloaking flag from the raw findings, and returns a result dict
that drops straight into session.sandbox_results (so the Verdict Engine can
read `verdict_signal == "malicious"` as a near-decisive hard signal).

Limitations documented in the design (fyp.txt L4):
- Cloaking: phishing kits fingerprint headless browsers, so a benign screenshot
  is NOT proof of safety; we flag suspected cloaking rather than trusting it.
- Illegal-content risk: size/type limits are enforced by the runner; suspect
  content is flagged, not archived.
"""

from __future__ import annotations

from urllib.parse import urlparse

from hive.logging_setup import get_logger
from hive.sandbox.runner import BrowserRunner, RawFindings

log = get_logger(__name__)


def _same_registrable_domain(a: str, b: str) -> bool:
    """Crude eTLD+1 comparison (last two labels). Good enough for a signal."""
    ha, hb = urlparse(a).hostname or "", urlparse(b).hostname or ""
    return ha.split(".")[-2:] == hb.split(".")[-2:] and bool(ha)


def _derive(url: str, f: RawFindings) -> tuple[str, bool]:
    """Return (verdict_signal, cloaking_suspected)."""
    if f.challenge_detected or f.access_state in {"challenge", "blocked"}:
        return "inconclusive", False
    if f.error:
        return "error", False

    cross_domain = bool(f.final_url) and not _same_registrable_domain(url, f.final_url)
    # Credential-harvesting page reached, especially after a cross-domain hop.
    if f.has_password_field and cross_domain:
        return "malicious", False
    if f.has_password_field or len(f.redirect_chain) >= 3:
        return "suspicious", False

    # Reached a 200-ish page but it's empty / titleless: likely cloaked away
    # from the headless browser.
    cloaking = f.body_len < 200 and not f.title and not f.error
    return ("suspicious" if cloaking else "clean"), cloaking


def analyze_url(url: str, runner: BrowserRunner) -> dict:
    """Analyse a URL in the sandbox and return a session.sandbox_results entry."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        log.warning("L4 sandbox: refusing non-http(s) url scheme=%s", scheme)
        return {"url": url, "verdict_signal": "error", "error": "unsupported scheme"}

    findings = runner.run(url)
    signal, cloaking = _derive(url, findings)
    log.info(
        "L4 sandbox: url=%s signal=%s cloaking=%s redirects=%d final=%s",
        url,
        signal,
        cloaking,
        len(findings.redirect_chain),
        findings.final_url,
    )
    return {
        "url": url,
        "final_url": findings.final_url,
        "redirect_chain": findings.redirect_chain,
        "dest_ip": findings.dest_ip,
        "screenshot_path": findings.screenshot_path,
        "screenshot_error": findings.screenshot_error,
        "title": findings.title,
        "has_password_field": findings.has_password_field,
        "blocked_requests": findings.blocked_requests,
        "http_status": findings.http_status,
        "fetcher": findings.fetcher,
        "access_state": findings.access_state,
        "challenge_detected": findings.challenge_detected,
        "challenge_provider": findings.challenge_provider,
        "verdict_signal": signal,
        "cloaking_suspected": cloaking,
        "error": findings.error,
    }
