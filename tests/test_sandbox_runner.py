"""Tests for the real sandbox runner's docker invocation + network setup.

Covers the review fixes: dedicated network (not default), no --user override
conflicting with the image, writable tmpfs under --read-only, and
ensure_network() create-if-missing behaviour. No Docker is executed; we assert
the command construction and mock subprocess for ensure_network.
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from hive.sandbox.runner import (
    _SCRAPLING_SCRIPT,
    ScraplingDockerRunner,
    configured_sandbox_runner,
    validate_public_url,
)


def test_docker_cmd_uses_dedicated_network_not_default():
    cmd = ScraplingDockerRunner()._docker_cmd("http://x.example/")
    assert "--network" in cmd
    assert cmd[cmd.index("--network") + 1] == "hive-sandbox-net"


def test_docker_cmd_no_conflicting_user_override():
    # The image already runs as pwuser; we must NOT force --user nobody.
    cmd = ScraplingDockerRunner()._docker_cmd("http://x.example/")
    assert "nobody" not in cmd
    assert "--user" not in cmd


def test_docker_cmd_writable_tmpfs_under_readonly():
    cmd = ScraplingDockerRunner()._docker_cmd("http://x.example/")
    assert "--read-only" in cmd
    assert "--tmpfs" in cmd
    assert any(a.startswith("/tmp") for a in cmd)
    assert "HOME=/tmp" in cmd


def test_docker_cmd_hardening_flags_present():
    cmd = ScraplingDockerRunner()._docker_cmd(
        "http://x.example/", "hive-sandbox-test", "/tmp/hive-sandbox/test"
    )
    assert "--rm" in cmd
    assert cmd[cmd.index("--name") + 1] == "hive-sandbox-test"
    mount = cmd[cmd.index("-v") + 1]
    assert mount.endswith(":/out")
    assert Path(mount.removesuffix(":/out")).is_absolute()
    assert "ALL" in cmd  # --cap-drop ALL
    assert "no-new-privileges" in cmd


def test_relative_output_directory_becomes_absolute_bind_mount(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = ScraplingDockerRunner(out_dir="evidence/sandbox")

    cmd = runner._docker_cmd(
        "http://x.example/",
        "hive-sandbox-test",
        "evidence/sandbox/hive-sandbox-test",
    )
    mount = cmd[cmd.index("-v") + 1]

    assert Path(runner.out_dir).is_absolute()
    assert mount == f"{tmp_path / 'evidence/sandbox/hive-sandbox-test'}:/out"


def test_docker_cmd_is_configurable():
    cmd = ScraplingDockerRunner(network="custom-net", dns="9.9.9.9")._docker_cmd("http://x/")
    assert cmd[cmd.index("--network") + 1] == "custom-net"
    assert cmd[cmd.index("--dns") + 1] == "9.9.9.9"


def test_docker_cmd_can_defer_cgroup_limits_to_outer_container():
    cmd = ScraplingDockerRunner(memory_limit=None, pids_limit=None)._docker_cmd("http://x/")

    assert "--memory" not in cmd
    assert "--pids-limit" not in cmd


def test_configured_runner_honours_disabled_nested_cgroup_limits(monkeypatch):
    monkeypatch.setenv("HIVE_SANDBOX_MEMORY_LIMIT", "")
    monkeypatch.setenv("HIVE_SANDBOX_PIDS_LIMIT", "")

    runner = configured_sandbox_runner(run_timeout_s=17)

    assert runner.memory_limit is None
    assert runner.pids_limit is None
    assert runner.run_timeout_s == 17


def test_configured_runner_uses_resource_limits_by_default(monkeypatch):
    monkeypatch.delenv("HIVE_SANDBOX_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("HIVE_SANDBOX_PIDS_LIMIT", raising=False)

    runner = configured_sandbox_runner()

    assert runner.memory_limit == "512m"
    assert runner.pids_limit == 128


def test_scrapling_script_enables_stealth_and_preserves_request_guards():
    assert "sys.argv[1]" in _SCRAPLING_SCRIPT
    assert "StealthyFetcher.fetch" in _SCRAPLING_SCRIPT
    assert "solve_cloudflare=True" in _SCRAPLING_SCRIPT
    assert 'page.route("**/*", guard)' in _SCRAPLING_SCRIPT
    assert "socket.getaddrinfo" in _SCRAPLING_SCRIPT
    assert "blocked_requests" in _SCRAPLING_SCRIPT
    assert 'access_state="challenge"' in _SCRAPLING_SCRIPT
    assert "request.frame == page.main_frame" in _SCRAPLING_SCRIPT
    assert 'full_page=False' in _SCRAPLING_SCRIPT
    assert "ssl.create_default_context" in _SCRAPLING_SCRIPT
    assert "ssl.cert_time_to_seconds" in _SCRAPLING_SCRIPT
    assert 'observed["certificate_age_days"]' in _SCRAPLING_SCRIPT


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
        ScraplingDockerRunner.ensure_network("hive-sandbox-net")
        assert run.call_count == 2
        assert run.call_args_list[1].args[0][:3] == ["docker", "network", "create"]


def test_ensure_network_noop_when_present():
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0)  # inspect succeeds
        ScraplingDockerRunner.ensure_network("hive-sandbox-net")
        assert run.call_count == 1  # inspect only, no create


def test_run_ensures_network_before_docker_run(tmp_path):
    runner = ScraplingDockerRunner(network="custom-net", out_dir=str(tmp_path))
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ) as ensure, mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(
            stdout=(
                '{"final_url":"https://x/","redirect_chain":[],"body_len":1000,'
                '"certificate_age_days":9,"certificate_error":""}\n'
            ),
            stderr="",
            returncode=0,
        )
        result = runner.run("https://x/")
        ensure.assert_called_once_with("custom-net")
        assert run.call_count == 1
        assert result.final_url == "https://x/"
        assert result.certificate_age_days == 9
        assert result.runtime_ms >= 0
        assert "hive-sandbox-" in result.screenshot_path


def test_run_returns_error_when_network_setup_fails(tmp_path):
    runner = ScraplingDockerRunner(network="custom-net", out_dir=str(tmp_path))
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner,
        "ensure_network",
        side_effect=subprocess.CalledProcessError(1, ["docker", "network", "create"]),
    ), mock.patch("subprocess.run") as run:
        result = runner.run("http://x/")
        assert "docker" in result.error
        run.assert_not_called()


def test_dns_preflight_failure_is_not_counted_as_a_blocked_request(tmp_path):
    runner = ScraplingDockerRunner(out_dir=str(tmp_path))
    with mock.patch(
        "hive.sandbox.runner.validate_public_url",
        side_effect=ValueError("sandbox could not resolve hostname: missing.example"),
    ), mock.patch.object(runner, "ensure_network") as ensure:
        result = runner.run("https://missing.example/")

    assert result.access_state == "preflight_failed"
    assert result.blocked_requests == []
    assert result.runtime_ms >= 0
    ensure.assert_not_called()


def test_blocked_preflight_destination_is_counted(tmp_path):
    runner = ScraplingDockerRunner(out_dir=str(tmp_path))
    with mock.patch(
        "hive.sandbox.runner.validate_public_url",
        side_effect=ValueError("sandbox blocked local hostname: localhost"),
    ):
        result = runner.run("https://localhost/")

    assert result.access_state == "preflight_failed"
    assert result.blocked_requests == ["https://localhost/"]


def test_run_reports_nonzero_container_exit_with_stderr(tmp_path):
    runner = ScraplingDockerRunner(out_dir=str(tmp_path))
    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ), mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout="", stderr="cgroup failed", returncode=125)

        result = runner.run("http://x/")

    assert "container exited 125" in result.error
    assert "cgroup failed" in result.error


def test_run_removes_named_container_after_timeout(tmp_path):
    runner = ScraplingDockerRunner(out_dir=str(tmp_path), run_timeout_s=12)
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


def test_run_preserves_challenge_checkpoint_after_timeout(tmp_path):
    runner = ScraplingDockerRunner(out_dir=str(tmp_path), run_timeout_s=12)

    def run_side_effect(command, **kwargs):
        if command[:3] == ["docker", "run", "--rm"]:
            mount = command[command.index("-v") + 1]
            output_dir = Path(mount.removesuffix(":/out"))
            (output_dir / "progress.json").write_text(
                '{"final_url":"https://x.example/","title":"Just a moment...",'
                '"access_state":"challenge","challenge_detected":true,'
                '"challenge_provider":"cloudflare","fetcher":"scrapling_stealthy"}',
                encoding="utf-8",
            )
            raise subprocess.TimeoutExpired(command, 12)
        return mock.Mock(returncode=0)

    with mock.patch("hive.sandbox.runner.validate_public_url"), mock.patch.object(
        runner, "ensure_network"
    ), mock.patch("subprocess.run", side_effect=run_side_effect):
        result = runner.run("https://x.example/")

    assert result.access_state == "challenge"
    assert result.challenge_detected is True
    assert result.challenge_provider == "cloudflare"
    assert result.error == "sandbox timed out after 12s"
