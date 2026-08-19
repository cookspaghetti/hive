"""Disposable browser runners for the Forensic Sandbox (fyp.txt L4).

A runner navigates a suspect URL and returns raw findings. The real runner
launches a fresh, isolated, non-privileged Docker container running headless
Playwright, then destroys it. The runner is injected into `analyze_url` so the
analysis logic is testable offline with a fake.

Egress containment is best-effort at the docker level (dedicated bridge, no host
mounts, dropped caps); full LAN/host denial additionally requires host firewall
rules — see EGRESS_FIREWALL_HINT.

Reference: OpenClaw Playwright/CDP sandbox; Hermes egress-isolation
(reference-mapping.md L4).
"""

from __future__ import annotations

import json
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
    "Add a DOCKER-USER iptables DROP rule for RFC1918 destinations from the "
    "hive-sandbox-net subnet to enforce LAN/host isolation."
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
    error: str = ""


class BrowserRunner(Protocol):
    def run(self, url: str) -> RawFindings: ...


# The in-container Playwright script (kept as data; executed inside the
# disposable container, never in the host process).
_PLAYWRIGHT_SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[1];
  const chain = [];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('response', r => {
    if ([301,302,303,307,308].includes(r.status())) chain.push(r.url());
  });
  try {
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
    }));
  } catch (e) {
    console.log(JSON.stringify({ error: String(e) }));
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

    IMPORTANT — egress scope: docker flags alone give the container a private
    bridge but do NOT by themselves block reaching the host's LAN/RFC1918
    ranges (the default bridge masquerades outbound traffic). True LAN/host
    denial requires host firewall rules on the dedicated network's subnet.
    `ensure_network()` creates the network; `EGRESS_FIREWALL_HINT` documents the
    iptables rules the operator must add. We therefore claim *containment*, not
    full network lockdown, and say so honestly in the report.
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
            self.ensure_network(self.network)
            proc = subprocess.run(
                self._docker_cmd(url), capture_output=True, text=True, timeout=45
            )
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
            return RawFindings(error=data["error"])
        return RawFindings(
            final_url=data.get("final_url", ""),
            redirect_chain=data.get("redirect_chain", []),
            dest_ip=data.get("dest_ip", ""),
            screenshot_path=f"{self.out_dir}/shot.png",
            title=data.get("title", ""),
            has_password_field=bool(data.get("has_password_field")),
            body_len=int(data.get("body_len", 0)),
        )
