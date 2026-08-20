"""Tests for the real sandbox runner's docker invocation + network setup.

Covers the review fixes: dedicated network (not default), no --user override
conflicting with the image, writable tmpfs under --read-only, and
ensure_network() create-if-missing behaviour. No Docker is executed; we assert
the command construction and mock subprocess for ensure_network.
"""

import subprocess
from unittest import mock

import pytest

from hive.sandbox.runner import (
    _PLAYWRIGHT_SCRIPT,
    PlaywrightDockerRunner,
    validate_public_url,
)


def test_docker_cmd_uses_dedicated_network_not_default():
    cmd = PlaywrightDockerRunner()._docker_cmd("http://x.example/")
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "hive-sandbox-net"


def test_docker_cmd_no_conflicting_user_override():
    # The image already runs as pwuser; we must NOT force --user nobody.
    cmd = PlaywrightDockerRunner()._docker_cmd("http://x.example/")
    assert "nobody" not in cmd
    assert "--user" not in cmd


def test_docker_cmd_writable_tmpfs_under_readonly():
    cmd = PlaywrightDockerRunner()._docker_cmd("http://x.example/")
    assert "--read-only" in cmd
    assert "--tmpfs" in cmd
    assert any(a.startswith("/tmp") for a in cmd)
    assert "HOME=/tmp" in cmd


def test_docker_cmd_hardening_flags_present():
    cmd = PlaywrightDockerRunner()._docker_cmd(
        "http://x.example/", "hive-sandbox-test"
    )
    assert "--rm" in cmd
    assert cmd[cmd.index("--name") + 1] == "hive-sandbox-test"
    assert "ALL" in cmd  # --cap-drop ALL
    assert "no-new-privileges" in cmd


def test_docker_cmd_is_configurable():
    cmd = PlaywrightDockerRunner(network="custom-net", dns="9.9.9.9")._docker_cmd("http://x/")
    assert cmd[cmd.index("--network") + 1] == "custom-net"
    assert cmd[cmd.index("--dns") + 1] == "9.9.9.9"


def test_docker_cmd_can_defer_memory_limit_to_outer_container():
    cmd = PlaywrightDockerRunner(memory_limit=None)._docker_cmd("http://x/")

    assert "--memory" not in cmd
    assert "--pids-limit" in cmd


def test_playwright_script_reads_node_eval_argument_and_avoids_networkidle():
    assert "process.argv[1]" in _PLAYWRIGHT_SCRIPT
    assert "process.argv[2]" not in _PLAYWRIGHT_SCRIPT
    assert "domcontentloaded" in _PLAYWRIGHT_SCRIPT
    assert "networkidle" not in _PLAYWRIGHT_SCRIPT
    assert "context.route" in _PLAYWRIGHT_SCRIPT
    assert "dns.lookup" in _PLAYWRIGHT_SCRIPT
    assert "blocked_requests" in _PLAYWRIGHT_SCRIPT


def test_public_url_validation_rejects_local_and_private_targets():
    for url in (
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ):
        with pytest.raises(ValueError, match="blocked"):
            validate_public_url(url)


def test_public_url_validation_accepts_global_literal():
    assert validate_public_url("https://1.1.1.1/") == ["1.1.1.1"]


def test_ensure_network_creates_when_missing():
    with mock.patch("subprocess.run") as run:
        # inspect -> rc 1 (missing); create -> rc 0
        run.side_effect = [mock.Mock(returncode=1), mock.Mock(returncode=0)]
        PlaywrightDockerRunner.ensure_network("hive-sandbox-net")
        assert run.call_count == 2
        assert run.call_args_list[1].args[0][:3] == ["docker", "network", "create"]


def test_ensure_network_noop_when_present():
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0)  # inspect succeeds
        PlaywrightDockerRunner.ensure_network("hive-sandbox-net")
        assert run.call_count == 1  # inspect only, no create


def test_run_ensures_network_before_docker_run():
    runner = PlaywrightDockerRunner(network="custom-net")
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ) as ensure, mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(
            stdout='{"final_url":"http://x/","redirect_chain":[],"body_len":1000}\n',
            stderr="",
            returncode=0,
        )
        result = runner.run("http://x/")
        ensure.assert_called_once_with("custom-net")
        assert run.call_count == 1
        assert result.final_url == "http://x/"


def test_run_returns_error_when_network_setup_fails():
    runner = PlaywrightDockerRunner(network="custom-net")
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner,
        "ensure_network",
        side_effect=subprocess.CalledProcessError(1, ["docker", "network", "create"]),
    ), mock.patch("subprocess.run") as run:
        result = runner.run("http://x/")
        assert "docker" in result.error
        run.assert_not_called()


def test_run_reports_nonzero_container_exit_with_stderr():
    runner = PlaywrightDockerRunner()
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ), mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout="", stderr="cgroup failed", returncode=125)

        result = runner.run("http://x/")

    assert "container exited 125" in result.error
    assert "cgroup failed" in result.error


def test_run_removes_named_container_after_timeout():
    runner = PlaywrightDockerRunner(run_timeout_s=12)
    timeout = subprocess.TimeoutExpired(["docker", "run"], 12)
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ), mock.patch("subprocess.run") as run:
        run.side_effect = [timeout, mock.Mock(returncode=0)]

        result = runner.run("http://x.example/")

    assert result.error == "sandbox timed out after 12s"
    cleanup = run.call_args_list[1].args[0]
    assert cleanup[:3] == ["docker", "rm", "-f"]
    assert cleanup[3].startswith("hive-sandbox-")
