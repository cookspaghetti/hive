"""Tests for the real sandbox runner's docker invocation + network setup.

Covers the review fixes: dedicated network (not default), no --user override
conflicting with the image, writable tmpfs under --read-only, and
ensure_network() create-if-missing behaviour. No Docker is executed; we assert
the command construction and mock subprocess for ensure_network.
"""

import subprocess
from unittest import mock

from hive.sandbox.runner import PlaywrightDockerRunner


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
    cmd = PlaywrightDockerRunner()._docker_cmd("http://x.example/")
    assert "--rm" in cmd
    assert "ALL" in cmd  # --cap-drop ALL
    assert "no-new-privileges" in cmd


def test_docker_cmd_is_configurable():
    cmd = PlaywrightDockerRunner(network="custom-net", dns="9.9.9.9")._docker_cmd("http://x/")
    assert cmd[cmd.index("--network") + 1] == "custom-net"
    assert cmd[cmd.index("--dns") + 1] == "9.9.9.9"


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
    with mock.patch.object(runner, "ensure_network") as ensure, mock.patch("subprocess.run") as run:
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
    with mock.patch.object(
        runner,
        "ensure_network",
        side_effect=subprocess.CalledProcessError(1, ["docker", "network", "create"]),
    ), mock.patch("subprocess.run") as run:
        result = runner.run("http://x/")
        assert "docker" in result.error
        run.assert_not_called()
