"""Disposable browser runners for the Forensic Sandbox (fyp.txt L4).

A runner navigates a suspect URL and returns raw findings. The real runner
launches a fresh, isolated, non-privileged Docker container running headless
Playwright, then destroys it. The runner is injected into `analyze_url` so the
analysis logic is testable offline with a fake.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

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
    title: str = ""
    has_password_field: bool = False
    body_len: int = 0
    blocked_requests: list[str] = field(default_factory=list)
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


# The in-container Playwright script (kept as data; executed inside the
# disposable container, never in the host process).
_PLAYWRIGHT_SCRIPT = r"""
const { chromium } = require('playwright');
const dns = require('node:dns').promises;
const net = require('node:net');

function isPrivateIp(raw) {
  let ip = String(raw || '').toLowerCase().split('%')[0].replace(/^\[|\]$/g, '');
  if (ip.startsWith('::ffff:')) ip = ip.slice(7);
  if (net.isIPv4(ip)) {
    const p = ip.split('.').map(Number);
    return p[0] === 0 || p[0] === 10 || p[0] === 127 || p[0] >= 224 ||
      (p[0] === 100 && p[1] >= 64 && p[1] <= 127) ||
      (p[0] === 169 && p[1] === 254) ||
      (p[0] === 172 && p[1] >= 16 && p[1] <= 31) ||
      (p[0] === 192 && p[1] === 168) ||
      (p[0] === 198 && (p[1] === 18 || p[1] === 19));
  }
  if (net.isIPv6(ip)) {
    return ip === '::' || ip === '::1' || ip.startsWith('fc') ||
      ip.startsWith('fd') || /^fe[89ab]/.test(ip) || ip.startsWith('ff');
  }
  return true;
}

async function ensurePublic(raw) {
  const parsed = new URL(raw);
  if (!['http:', 'https:'].includes(parsed.protocol)) return;
  const host = parsed.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost') ||
      host.endsWith('.local') || host.endsWith('.internal')) {
    throw new Error(`blocked local hostname: ${host}`);
  }
  const addresses = net.isIP(host) ? [{ address: host }] :
    await dns.lookup(host, { all: true, verbatim: true });
  if (!addresses.length || addresses.some(item => isPrivateIp(item.address))) {
    throw new Error(`blocked non-public destination: ${host}`);
  }
}

(async () => {
  const url = process.argv[1];
  const chain = [];
  const blocked = [];
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false });
  await context.route('**/*', async route => {
    const target = route.request().url();
    try {
      await ensurePublic(target);
      await route.continue();
    } catch (error) {
      blocked.push(target);
      await route.abort('blockedbyclient');
    }
  });
  const page = await context.newPage();
  page.on('response', r => {
    if ([301,302,303,307,308].includes(r.status())) chain.push(r.url());
  });
  try {
    try { await ensurePublic(url); }
    catch (error) { blocked.push(url); throw error; }
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForTimeout(750);
    const hasPw = await page.$('input[type=password]') !== null;
    const body = await page.content();
    await page.screenshot({ path: '/out/shot.png', fullPage: true });
    const server = resp && typeof resp.serverAddr === 'function'
      ? await resp.serverAddr().catch(() => null) : null;
    console.log(JSON.stringify({
      final_url: page.url(), redirect_chain: chain,
      dest_ip: server ? server.ipAddress : '',
      title: await page.title(), has_password_field: hasPw, body_len: body.length,
      blocked_requests: blocked,
    }));
  } catch (e) {
    console.log(JSON.stringify({ error: String(e), blocked_requests: blocked }));
  } finally { await browser.close(); }
})();
"""


class PlaywrightDockerRunner:
    """Runs the Playwright script in a disposable, isolated container.

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
    ) -> None:
        self.image = image
        self.out_dir = out_dir
        self.network = network
        self.dns = dns
        self.memory_limit = memory_limit
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

    def _docker_cmd(self, url: str) -> list[str]:
        resource_limits = ["--pids-limit", "128"]
        if self.memory_limit:
            resource_limits = ["--memory", self.memory_limit, *resource_limits]
        return [
            "docker", "run", "--rm",
            "--network", self.network,
            "--read-only",
            "--tmpfs", "/tmp:rw,size=256m",
            "-e", "HOME=/tmp",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            *resource_limits,
            "--dns", self.dns,
            "-v", f"{self.out_dir}:/out",
            self.image, "node", "-e", _PLAYWRIGHT_SCRIPT, url,
        ]

    def run(self, url: str) -> RawFindings:
        try:
            validate_public_url(url)
            self.ensure_network(self.network)
            proc = subprocess.run(
                self._docker_cmd(url), capture_output=True, text=True, timeout=45
            )
        except ValueError as exc:
            log.warning("L4 sandbox: destination rejected: %s", exc)
            return RawFindings(error=str(exc), blocked_requests=[url])
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as exc:
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
        if "error" in data:
            return RawFindings(
                error=data["error"],
                blocked_requests=list(data.get("blocked_requests") or []),
            )
        return RawFindings(
            final_url=data.get("final_url", ""),
            redirect_chain=data.get("redirect_chain", []),
            dest_ip=data.get("dest_ip", ""),
            screenshot_path=f"{self.out_dir}/shot.png",
            title=data.get("title", ""),
            has_password_field=bool(data.get("has_password_field")),
            body_len=int(data.get("body_len", 0)),
            blocked_requests=list(data.get("blocked_requests") or []),
        )
