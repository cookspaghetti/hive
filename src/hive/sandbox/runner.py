"""Disposable Scrapling browser runner for the Forensic Sandbox (fyp.txt L4).

A runner navigates a suspect URL and returns raw findings. The real runner
launches a fresh, isolated, non-privileged Docker container running Scrapling's
stealth browser, then destroys it. The runner is injected into `analyze_url` so
the analysis logic is testable offline with a fake.

Egress containment combines a dedicated Docker bridge with host-side and
in-browser destination validation. Host firewall rules remain recommended as a
defence-in-depth boundary — see EGRESS_FIREWALL_HINT.

Reference: OpenClaw Playwright/CDP sandbox; Hermes egress-isolation
(reference-mapping.md L4).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from hive.logging_setup import get_logger

# Operator setup: to actually deny the sandbox network access to the host and
# LAN (RFC1918), add firewall rules on the dedicated bridge subnet, e.g.:
#   iptables -I DOCKER-USER -s <hive-sandbox-net subnet> \
#            -d 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 -j DROP
# This is required for the "deny host/LAN" guarantee; docker flags alone cannot.
EGRESS_FIREWALL_HINT = (
    "Add a DOCKER-USER firewall rule denying non-public destinations from the "
    "hive-sandbox-net subnet as defence in depth."
)

log = get_logger(__name__)


@dataclass
class RawFindings:
    """What a runner observes on the page (before verdict derivation)."""

    final_url: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    dest_ip: str = ""
    screenshot_path: str = ""
    screenshot_error: str = ""
    title: str = ""
    has_password_field: bool = False
    body_len: int = 0
    blocked_requests: list[str] = field(default_factory=list)
    http_status: int = 0
    fetcher: str = ""
    access_state: str = ""
    challenge_detected: bool = False
    challenge_provider: str = ""
    error: str = ""


class BrowserRunner(Protocol):
    def run(self, url: str) -> RawFindings: ...


def validate_public_url(url: str) -> list[str]:
    """Resolve an HTTP target and reject every non-global destination address."""
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("sandbox target must be an absolute HTTP(S) URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"sandbox blocked local hostname: {hostname}")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"sandbox could not resolve hostname: {hostname}") from exc
        addresses = list({ipaddress.ip_address(item[4][0]) for item in resolved})
    blocked = sorted(str(address) for address in addresses if not address.is_global)
    if blocked:
        raise ValueError(
            f"sandbox blocked non-public destination for {hostname}: {', '.join(blocked)}"
        )
    return sorted(str(address) for address in addresses)


# The in-container Scrapling program is kept as data and executed only inside a
# disposable container. `page_setup` installs request filtering before the
# first navigation, preserving HIVE's redirect/subresource SSRF controls.
_SCRAPLING_SCRIPT = r"""
import ipaddress
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from scrapling.fetchers import StealthyFetcher

url = sys.argv[1]
blocked = []
chain = []
observed = {}
navigation_ips = {}
redirect_statuses = {301, 302, 303, 307, 308}


def ensure_public(raw):
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        return
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise ValueError("missing hostname")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"blocked local hostname: {hostname}")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        resolved = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        addresses = list({ipaddress.ip_address(item[4][0]) for item in resolved})
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError(f"blocked non-public destination: {hostname}")


def page_setup(page):
    def guard(route):
        target = route.request.url
        try:
            ensure_public(target)
        except Exception:
            blocked.append(target)
            route.abort(error_code="blockedbyclient")
        else:
            route.continue_()

    def record_response(response):
        try:
            request = response.request
            main_navigation = (
                request.is_navigation_request() and request.frame == page.main_frame
            )
            if main_navigation and response.status in redirect_statuses:
                chain.append(response.url)
            if main_navigation:
                server = response.server_addr() or {}
                navigation_ips[response.url] = server.get("ipAddress", "")
        except Exception:
            pass

    page.route("**/*", guard)
    page.on("response", record_response)
    page.on("domcontentloaded", lambda: inspect_page(page))


def persist_progress():
    progress = {
        **observed,
        "redirect_chain": chain,
        "blocked_requests": list(dict.fromkeys(blocked)),
        "fetcher": "scrapling_stealthy",
    }
    temporary = Path("/out/progress.json.tmp")
    temporary.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    temporary.replace("/out/progress.json")


def inspect_page(page):
    title = page.title()
    body = page.content()
    try:
        visible_text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        visible_text = ""
    haystack = f"{title}\n{visible_text}\n{body[:20000]}".casefold()
    title_challenge = any(
        marker in title.casefold()
        for marker in (
            "just a moment",
            "attention required",
            "checking your browser",
            "security verification",
            "verify you are human",
        )
    )
    cloudflare_challenge = "cloudflare" in haystack and any(
        marker in haystack
        for marker in (
            "performing security verification",
            "verifying you are human",
            "verify you are not a bot",
            "checking your browser",
            "cf-chl-",
            "__cf_chl_",
            "cf-turnstile",
        )
    )
    challenge = title_challenge or cloudflare_challenge
    observed.update(
        final_url=page.url,
        dest_ip=navigation_ips.get(page.url, ""),
        title=title,
        has_password_field=page.query_selector("input[type=password]") is not None,
        body_len=len(body),
        screenshot_error="",
        challenge_detected=challenge,
        challenge_provider=(
            "cloudflare" if cloudflare_challenge else ("unknown" if challenge else "")
        ),
        access_state="challenge" if challenge else "reached",
    )
    persist_progress()
    try:
        page.screenshot(path="/out/shot.png", full_page=True)
    except Exception as full_page_exc:
        try:
            page.screenshot(path="/out/shot.png", full_page=False)
        except Exception as viewport_exc:
            observed["screenshot_error"] = (
                f"full-page: {full_page_exc}; viewport: {viewport_exc}"
            )
    persist_progress()


try:
    ensure_public(url)
    response = StealthyFetcher.fetch(
        url,
        headless=True,
        solve_cloudflare=True,
        timeout=60000,
        wait=750,
        network_idle=False,
        google_search=False,
        block_webrtc=True,
        retries=1,
        page_setup=page_setup,
        page_action=inspect_page,
    )
    for prior in response.history:
        prior_url = str(getattr(prior, "url", ""))
        if prior_url and prior_url not in chain:
            chain.append(prior_url)
    observed.setdefault("final_url", str(response.url))
    observed.setdefault("body_len", len(response.body))
    observed["http_status"] = int(response.status or 0)
    if observed["http_status"] in {401, 403, 407, 429, 503} and not observed.get(
        "challenge_detected"
    ):
        observed["access_state"] = "blocked"
    observed["redirect_chain"] = chain
    observed["blocked_requests"] = list(dict.fromkeys(blocked))
    observed["fetcher"] = "scrapling_stealthy"
    print(json.dumps(observed, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({
        "error": str(exc),
        "blocked_requests": list(dict.fromkeys(blocked)),
        "fetcher": "scrapling_stealthy",
        "access_state": "error",
    }, ensure_ascii=False))
"""


class ScraplingDockerRunner:
    """Runs Scrapling's stealth fetcher in a disposable, isolated container.

    Hardening actually applied by the docker flags below (fyp.txt L4):
      --rm                         container destroyed on exit
      --network <net>              attached to a dedicated bridge, not the host
      --cap-drop ALL + no-new-priv non-privileged
      --read-only + tmpfs          root FS read-only; only /tmp + /out writable
      --memory/--pids              contain runaway pages
      no host bind mounts except the /out screenshot dir

    Host-side preflight and per-request browser routing reject non-public IPs,
    including redirects and page subresources. `EGRESS_FIREWALL_HINT` documents
    the additional network-level boundary recommended for hostile content.
    """

    def __init__(
        self,
        image: str = "hive-sandbox:latest",
        out_dir: str = "/tmp/hive-sandbox",
        network: str = "hive-sandbox-net",
        dns: str = "1.1.1.1",
        memory_limit: str | None = "512m",
        pids_limit: int | None = 128,
        run_timeout_s: int = 90,
    ) -> None:
        self.image = image
        self.out_dir = out_dir
        self.network = network
        self.dns = dns
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit
        self.run_timeout_s = run_timeout_s
        Path(self.out_dir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_network(name: str = "hive-sandbox-net") -> None:
        """Create the dedicated sandbox bridge network if it does not exist."""
        exists = subprocess.run(
            ["docker", "network", "inspect", name], capture_output=True, text=True
        ).returncode == 0
        if not exists:
            subprocess.run(["docker", "network", "create", "--driver", "bridge", name], check=True)
            log.info("L4 sandbox: created network %s", name)

    def _docker_cmd(
        self,
        url: str,
        container_name: str = "",
        output_dir: str = "",
    ) -> list[str]:
        resource_limits = (
            ["--pids-limit", str(self.pids_limit)] if self.pids_limit else []
        )
        if self.memory_limit:
            resource_limits = ["--memory", self.memory_limit, *resource_limits]
        identity = ["--name", container_name] if container_name else []
        return [
            "docker", "run", "--rm",
            *identity,
            "--network", self.network,
            "--read-only",
            "--tmpfs", "/tmp:rw,size=256m",
            "-e", "HOME=/tmp",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            *resource_limits,
            "--dns", self.dns,
            "-v", f"{output_dir or self.out_dir}:/out",
            self.image, "python", "-c", _SCRAPLING_SCRIPT, url,
        ]

    @staticmethod
    def _findings_from_data(data: dict[str, Any], output_dir: Path) -> RawFindings:
        return RawFindings(
            final_url=str(data.get("final_url") or ""),
            redirect_chain=list(data.get("redirect_chain") or []),
            dest_ip=str(data.get("dest_ip") or ""),
            screenshot_path=str(output_dir / "shot.png"),
            screenshot_error=str(data.get("screenshot_error") or ""),
            title=str(data.get("title") or ""),
            has_password_field=bool(data.get("has_password_field")),
            body_len=int(data.get("body_len") or 0),
            blocked_requests=list(data.get("blocked_requests") or []),
            http_status=int(data.get("http_status") or 0),
            fetcher=str(data.get("fetcher") or "scrapling_stealthy"),
            access_state=str(data.get("access_state") or "error"),
            challenge_detected=bool(data.get("challenge_detected")),
            challenge_provider=str(data.get("challenge_provider") or ""),
            error=str(data.get("error") or ""),
        )

    def run(self, url: str) -> RawFindings:
        container_name = f"hive-sandbox-{uuid.uuid4().hex[:12]}"
        output_dir = Path(self.out_dir) / container_name
        try:
            validate_public_url(url)
            output_dir.mkdir(parents=True, exist_ok=False)
            output_dir.chmod(0o777)
            self.ensure_network(self.network)
            proc = subprocess.run(
                self._docker_cmd(url, container_name, str(output_dir)),
                capture_output=True,
                text=True,
                timeout=self.run_timeout_s,
            )
        except ValueError as exc:
            log.warning("L4 sandbox: destination rejected: %s", exc)
            return RawFindings(error=str(exc), blocked_requests=[url])
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                log.error("L4 sandbox: timed-out container cleanup did not finish")
            error = f"sandbox timed out after {self.run_timeout_s}s"
            log.error("L4 sandbox: %s", error)
            progress_path = output_dir / "progress.json"
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                progress = None
            if isinstance(progress, dict):
                progress["error"] = error
                return self._findings_from_data(progress, output_dir)
            return RawFindings(error=error)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            log.error("L4 sandbox: container run failed: %s", exc)
            return RawFindings(error=str(exc))
        if proc.returncode != 0:
            error = proc.stderr.strip() or proc.stdout.strip() or "no container output"
            log.error("L4 sandbox: container exited %d: %s", proc.returncode, error)
            return RawFindings(error=f"container exited {proc.returncode}: {error[:1000]}")
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = proc.stderr.strip() or proc.stdout.strip() or "empty output"
            return RawFindings(error=f"unparseable output: {detail[:1000]}")
        return self._findings_from_data(data, output_dir)


# Compatibility for callers that imported the original runner name.
PlaywrightDockerRunner = ScraplingDockerRunner
