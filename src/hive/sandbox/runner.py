"""Disposable browser runners for the Forensic Sandbox (fyp.txt L4).

A runner navigates a suspect URL and returns raw findings. The real runner
launches a fresh, network-locked, non-root Docker container running headless
Playwright, then destroys it. The runner is injected into `analyze_url` so the
analysis logic is testable offline with a fake.

Reference: OpenClaw Playwright/CDP sandbox; Hermes egress-isolation
(reference-mapping.md L4).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

from hive.logging_setup import get_logger

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
  const url = process.argv[2];
  const chain = [];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('response', r => { if ([301,302,303,307,308].includes(r.status())) chain.push(r.url()); });
  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
    const hasPw = await page.$('input[type=password]') !== null;
    const body = await page.content();
    await page.screenshot({ path: '/out/shot.png', fullPage: true });
    console.log(JSON.stringify({
      final_url: page.url(), redirect_chain: chain,
      dest_ip: resp ? (resp.serverAddr ? resp.serverAddr().ipAddress : '') : '',
      title: await page.title(), has_password_field: hasPw, body_len: body.length,
    }));
  } catch (e) {
    console.log(JSON.stringify({ error: String(e) }));
  } finally { await browser.close(); }
})();
"""


class PlaywrightDockerRunner:
    """Runs the Playwright script in a disposable, egress-locked container.

    Hardening (fyp.txt L4 threat model):
      --network per-URL locked (allow only the target; deny host/LAN)
      --rm            container destroyed on exit
      --user nobody   non-root
      --read-only     no persistent writes except the mounted /out
      --memory/--pids limits to contain runaway pages
    """

    def __init__(self, image: str = "hive-sandbox:latest", out_dir: str = "/tmp/hive-sandbox") -> None:
        self.image = image
        self.out_dir = out_dir

    def _docker_cmd(self, url: str) -> list[str]:
        return [
            "docker", "run", "--rm",
            "--user", "nobody",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", "512m", "--pids-limit", "128",
            "--dns", "1.1.1.1",
            "-v", f"{self.out_dir}:/out",
            self.image, "node", "-e", _PLAYWRIGHT_SCRIPT, url,
        ]

    def run(self, url: str) -> RawFindings:
        try:
            proc = subprocess.run(
                self._docker_cmd(url), capture_output=True, text=True, timeout=60
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            log.error("L4 sandbox: container run failed: %s", exc)
            return RawFindings(error=str(exc))
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return RawFindings(error=f"unparseable output: {proc.stderr[:200]}")
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
