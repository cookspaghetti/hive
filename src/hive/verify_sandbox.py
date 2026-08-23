"""Live, non-destructive verification of the configured forensic sandbox."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from hive.sandbox.runner import configured_sandbox_runner


def verify(url: str, *, timeout_s: int = 60) -> dict[str, Any]:
    runner = configured_sandbox_runner(run_timeout_s=timeout_s)
    public = runner.run(url)
    private = runner.run("http://127.0.0.1/")
    containers = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=hive-sandbox-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    orphans = [name for name in containers.stdout.splitlines() if name.strip()]
    public_ok = (
        not public.error
        and 200 <= public.http_status < 400
        and public.access_state == "reached"
    )
    private_ok = bool(private.error and private.blocked_requests)
    result = {
        "ok": public_ok and private_ok and not orphans and containers.returncode == 0,
        "public_fetch": {
            "ok": public_ok,
            "status": public.http_status,
            "final_url": public.final_url,
            "access_state": public.access_state,
            "fetcher": public.fetcher,
            "error": public.error,
        },
        "private_destination_rejected": private_ok,
        "orphan_containers": orphans,
    }
    if containers.returncode != 0:
        result["docker_inventory_error"] = containers.stderr.strip()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://example.com")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    result = verify(args.url, timeout_s=max(1, args.timeout))
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
