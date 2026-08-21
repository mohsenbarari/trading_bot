from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_staging_coin_inference_snapshot_publisher.sh"


def _write_fake_commands(root: Path, *, fail_verify_at: int = 0) -> tuple[Path, Path, Path]:
    fake_bin = root / "bin"
    state = root / "state"
    command_log = root / "commands.log"
    fake_bin.mkdir()
    state.mkdir()

    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        """#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >>"$FAKE_SYSTEMD_LOG"
command="$1"; shift
[[ "${1:-}" == "--quiet" ]] && shift
unit="${1:-}"
case "$command" in
  is-enabled) [[ -f "$FAKE_STATE/enabled.$unit" ]] ;;
  is-active) [[ -f "$FAKE_STATE/active.$unit" ]] && exit 0 || exit 3 ;;
  enable) touch "$FAKE_STATE/enabled.$unit" ;;
  disable) rm -f "$FAKE_STATE/enabled.$unit" ;;
  start|restart)
    if [[ "$command" == "start" \
          && "$unit" == "coin-intelligence-staging-snapshot-publish.service" \
          && "${FAKE_PUBLISHER_START_EXIT:-0}" != "0" ]]; then
      exit "$FAKE_PUBLISHER_START_EXIT"
    fi
    touch "$FAKE_STATE/active.$unit"
    ;;
  stop) rm -f "$FAKE_STATE/active.$unit" ;;
  show)
    case "$*" in
      *SuccessExitStatus*) printf '%s' "${FAKE_SUCCESS_EXIT_STATUS:-}" ;;
      *ExecStart*)
        printf '/usr/bin/python3 %s/scripts/publish_coin_intelligence_snapshot.py publish --runtime-root %s --market-store market/market.sqlite3 --snapshot staging/coin-rates.json --publish-staging-no-data-snapshot --environment staging --confirm publish-staging-no-data-snapshot\n' "$PROJECT_DIR" "$STAGING_COIN_INFERENCE_SOURCE_RUNTIME_ROOT"
        ;;
      *) exit 2 ;;
    esac
    ;;
  daemon-reload) exit 0 ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    analyze = fake_bin / "systemd-analyze"
    analyze.write_text(
        """#!/usr/bin/env bash
set -u
count=0
[[ -f "$FAKE_ANALYZE_COUNT" ]] && count="$(cat "$FAKE_ANALYZE_COUNT")"
count=$((count + 1))
printf '%s' "$count" >"$FAKE_ANALYZE_COUNT"
[[ "$count" != "${FAKE_ANALYZE_FAIL_AT:-0}" ]]
""",
        encoding="utf-8",
    )
    analyze.chmod(0o755)

    python = fake_bin / "python3"
    python.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == */scripts/publish_coin_intelligence_snapshot.py \
      && "${2:-}" == "check" ]]; then
  printf '{"status":"FRESH_NO_DATA"}\n'
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return fake_bin, state, command_log


def _installer_environment(
    root: Path,
    fake_bin: Path,
    state: Path,
    command_log: Path,
    *,
    fail_verify_at: int = 0,
) -> tuple[dict[str, str], Path, Path]:
    systemd_dir = root / "systemd"
    runtime_root = (
        root / "production-data" / "coin-intelligence" / "private-gold-live"
    )
    (runtime_root / "market").mkdir(parents=True)
    (runtime_root / "staging").mkdir()
    (runtime_root / "market" / "market.sqlite3").write_bytes(b"sqlite-fixture")
    systemd_dir.mkdir()
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REAL_PYTHON": sys.executable,
        "FAKE_STATE": str(state),
        "FAKE_SYSTEMD_LOG": str(command_log),
        "FAKE_ANALYZE_COUNT": str(root / "analyze-count"),
        "FAKE_ANALYZE_FAIL_AT": str(fail_verify_at),
        "STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_ENVIRONMENT": "staging",
        "STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_CONFIRM": (
            "install-staging-coin-inference-snapshot-publisher"
        ),
        "PROJECT_DIR": str(ROOT),
        "STAGING_COIN_INFERENCE_SOURCE_RUNTIME_ROOT": str(runtime_root),
        "STAGING_COIN_INFERENCE_SYSTEMD_DIR": str(systemd_dir),
        "STAGING_COIN_INFERENCE_SYSTEMD_BACKUP_ROOT": str(root / "backups"),
        "STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_LOCK_PATH": str(
            root / "locks" / "publisher.install.lock"
        ),
        "STAGING_COIN_INFERENCE_MAXIMUM_AGE_SECONDS": "120",
    }
    return environment, systemd_dir, runtime_root


def test_publisher_installer_requires_exact_staging_authority_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        systemd_dir = root / "systemd"
        systemd_dir.mkdir()
        result = subprocess.run(
            [str(INSTALLER)],
            env={
                **os.environ,
                "STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_ENVIRONMENT": "production",
                "STAGING_COIN_INFERENCE_PUBLISHER_INSTALL_CONFIRM": (
                    "install-staging-coin-inference-snapshot-publisher"
                ),
                "STAGING_COIN_INFERENCE_SYSTEMD_DIR": str(systemd_dir),
            },
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert "staging_environment_confirmation_required" in result.stderr
        assert list(systemd_dir.iterdir()) == []


def test_tracked_publisher_unit_and_dropin_pass_systemd_verify_after_render() -> None:
    unit_root = ROOT / "deploy/coin_intelligence/systemd"
    service_name = "coin-intelligence-staging-snapshot-publish.service"
    timer_name = "coin-intelligence-staging-snapshot-publish.timer"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "production-data/coin-intelligence/private-gold-live"
        runtime_root.mkdir(parents=True)
        service = root / service_name
        timer = root / timer_name
        dropin_dir = root / f"{service_name}.d"
        dropin_dir.mkdir()
        dropin = dropin_dir / "host-python-toman.conf"

        service.write_text(
            (unit_root / f"{service_name}.template")
            .read_text(encoding="utf-8")
            .replace("@RUNTIME_ROOT@", str(runtime_root))
            .replace(
                "@IMAGE@", "trading_bot_staging_preview:coin-intelligence-preview"
            ),
            encoding="utf-8",
        )
        shutil.copyfile(unit_root / timer_name, timer)
        dropin.write_text(
            (
                unit_root
                / f"{service_name}.d"
                / "host-python-toman.conf.template"
            )
            .read_text(encoding="utf-8")
            .replace("@PROJECT_DIR@", str(ROOT))
            .replace("@RUNTIME_ROOT@", str(runtime_root)),
            encoding="utf-8",
        )

        result = subprocess.run(
            ["systemd-analyze", "verify", str(service), str(timer)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        assert "SuccessExitStatus=3" not in dropin.read_text(encoding="utf-8")


def test_publisher_installer_is_idempotent_and_installs_safe_no_data_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin, state, command_log = _write_fake_commands(root)
        environment, systemd_dir, _ = _installer_environment(
            root, fake_bin, state, command_log
        )

        first = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert first.returncode == 0, first.stderr or first.stdout
        assert "snapshot_status=FRESH_NO_DATA" in first.stdout
        unit_paths = (
            systemd_dir / "coin-intelligence-staging-snapshot-publish.service",
            systemd_dir / "coin-intelligence-staging-snapshot-publish.timer",
            systemd_dir
            / "coin-intelligence-staging-snapshot-publish.service.d"
            / "host-python-toman.conf",
        )
        first_units = {path: path.read_bytes() for path in unit_paths}
        dropin = unit_paths[2].read_text(encoding="utf-8")
        assert dropin.count("--publish-staging-no-data-snapshot") == 1
        assert (
            "--environment staging --confirm publish-staging-no-data-snapshot"
            in dropin
        )
        assert "ProtectSystem=strict" in dropin
        assert "ReadWritePaths=" in dropin
        assert "EnvironmentFile=" not in dropin
        assert "SuccessExitStatus=3" not in dropin

        second = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert second.returncode == 0, second.stderr or second.stdout
        assert first_units == {path: path.read_bytes() for path in unit_paths}
        timer = "coin-intelligence-staging-snapshot-publish.timer"
        assert (state / f"enabled.{timer}").is_file()
        assert (state / f"active.{timer}").is_file()
        commands = command_log.read_text(encoding="utf-8")
        assert commands.count("daemon-reload") >= 2
        assert commands.count(
            "start coin-intelligence-staging-snapshot-publish.service"
        ) == 2
        backups = list((root / "backups").glob("staging-coin-snapshot-publisher.*"))
        assert len(backups) == 2


def test_publisher_installer_rolls_back_units_and_timer_state_on_verify_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin, state, command_log = _write_fake_commands(root, fail_verify_at=2)
        environment, systemd_dir, _ = _installer_environment(
            root,
            fake_bin,
            state,
            command_log,
            fail_verify_at=2,
        )
        service = "coin-intelligence-staging-snapshot-publish.service"
        timer = "coin-intelligence-staging-snapshot-publish.timer"
        dropin_dir = systemd_dir / f"{service}.d"
        dropin_dir.mkdir()
        original_paths = {
            systemd_dir / service: b"old-service\n",
            systemd_dir / timer: b"old-timer\n",
            dropin_dir / "host-python-toman.conf": b"old-dropin\n",
        }
        for path, payload in original_paths.items():
            path.write_bytes(payload)
        (state / f"enabled.{timer}").touch()
        (state / f"active.{timer}").touch()

        result = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "rolled_back" in result.stderr
        assert {path: path.read_bytes() for path in original_paths} == original_paths
        assert (state / f"enabled.{timer}").is_file()
        assert (state / f"active.{timer}").is_file()
        backups = list((root / "backups").glob("staging-coin-snapshot-publisher.*"))
        assert len(backups) == 1
        assert all((path.stat().st_mode & 0o777) == 0o600 for path in backups[0].iterdir())


def test_publisher_installer_treats_effective_exit_3_as_failure_and_rolls_back() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin, state, command_log = _write_fake_commands(root)
        environment, systemd_dir, _ = _installer_environment(
            root, fake_bin, state, command_log
        )
        environment["FAKE_SUCCESS_EXIT_STATUS"] = "3"

        result = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "publisher_nonzero_success_exit_status_rejected" in result.stderr
        assert "rolled_back" in result.stderr
        assert list(systemd_dir.iterdir()) == []


def test_publisher_installer_rolls_back_when_publisher_exits_3() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake_bin, state, command_log = _write_fake_commands(root)
        environment, systemd_dir, _ = _installer_environment(
            root, fake_bin, state, command_log
        )
        environment["FAKE_PUBLISHER_START_EXIT"] = "3"

        result = subprocess.run(
            [str(INSTALLER)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 3
        assert "rolled_back" in result.stderr
        assert list(systemd_dir.iterdir()) == []


def test_staging_activation_contract_installs_publisher_before_relay_and_deploy_check() -> None:
    docs = (ROOT / "docs/STAGING_COIN_INFERENCE_ACTIVATION.md").read_text(
        encoding="utf-8"
    )
    publisher = "scripts/install_staging_coin_inference_snapshot_publisher.sh"
    relay = "scripts/install_staging_coin_inference_snapshot_relay.sh"
    deploy = "scripts/deploy_staging.sh check"
    assert docs.index(publisher) < docs.index(relay) < docs.index(deploy)

    deploy_script = (ROOT / "scripts/deploy_staging.sh").read_text(encoding="utf-8")
    assert "FRESH_NO_DATA" in deploy_script
    assert "coin inference auto-selection must remain disabled in staging" in deploy_script
    assert "STAGING_COIN_INFERENCE_MAXIMUM_AGE_SECONDS:-120" in deploy_script
