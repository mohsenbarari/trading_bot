from pathlib import Path
import os
import subprocess
import tempfile


SYSTEMD_ROOT = (
    Path(__file__).resolve().parents[1] / "deploy" / "coin_intelligence" / "systemd"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ESTIMATOR_RUNTIME_SURFACES = (
    REPOSITORY_ROOT / "apps" / "coin_rate_estimator" / "README.md",
    REPOSITORY_ROOT / "scripts" / "calibrate_morning_reopen_anchor.py",
    REPOSITORY_ROOT / "scripts" / "fair_coin_model_bakeoff_after_unit_fix.py",
    REPOSITORY_ROOT / "scripts" / "run_staging_coin_intelligence_gate.py",
    REPOSITORY_ROOT / "scripts" / "train_and_compare_coin_shadow_ml.py",
    REPOSITORY_ROOT / "scripts" / "train_residual_shadow_and_calibrate.py",
)


def _sandbox_input_installer(root: Path) -> Path:
    source = REPOSITORY_ROOT / "scripts/install_coin_intelligence_input_timers.sh"
    source_lock = root / "secure" / ".production-runtime-source.lock"
    source_lock.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    rendered = source.read_text(encoding="utf-8")
    replacements = {
        'SYSTEMD_DIR="/etc/systemd/system"': f'SYSTEMD_DIR="{root / "systemd"}"',
        'BACKUP_ROOT="/var/backups/trading-bot/systemd"': f'BACKUP_ROOT="{root / "backups"}"',
        'PRODUCTION_OPERATION_LOCK_DIR="/root/secure-envs/trading-bot/queue-cutover-artifacts"': f'PRODUCTION_OPERATION_LOCK_DIR="{root / "operation-locks"}"',
        'PRODUCTION_SOURCE_LOCK_PATH="/root/secure-envs/trading-bot/.production-runtime-source.lock"': f'PRODUCTION_SOURCE_LOCK_PATH="{source_lock}"',
    }
    for before, after in replacements.items():
        assert before in rendered
        rendered = rendered.replace(before, after)
    installer = root / "install-input-timers.test.sh"
    installer.write_text(rendered, encoding="utf-8")
    installer.chmod(0o755)
    return installer


def _write_secure_collector_inputs(market_root: Path) -> None:
    public_env = market_root / "public-market-telegram.env"
    private_env = market_root / "private-gold-telegram.env"
    public_env.write_text("COIN_MARKET_TELEGRAM_API_ID=12345\n", encoding="utf-8")
    private_env.write_text(
        "COIN_MARKET_TELEGRAM_API_ID=12345\n"
        "COIN_INTELLIGENCE_PRIVATE_GOLD_OFFER_EVENT_CHANNEL_ID=-1001111111111\n"
        "COIN_INTELLIGENCE_PRIVATE_GOLD_TRADE_EVENT_CHANNEL_ID=-1002222222222\n",
        encoding="utf-8",
    )
    for path in (
        public_env,
        private_env,
        market_root / "session" / "coin-group-event-reader.session",
        market_root / "session" / "telegram-reader.session",
    ):
        if not path.exists():
            path.write_text("session\n", encoding="utf-8")
        path.chmod(0o600)


def _collector_binding_environment(market_root: Path, group_channel: str) -> dict[str, str]:
    return {
        "COIN_GROUP_EVENT_CHANNEL_ID": group_channel,
        "COIN_INTELLIGENCE_EXPECTED_GROUP_EVENT_CHANNEL_ID": group_channel,
        "COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID": "-1001111111111",
        "COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID": "-1002222222222",
        "COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID": "12345",
        "COIN_INTELLIGENCE_EXPECTED_GROUP_SESSION_FILE": str(
            market_root / "session" / "coin-group-event-reader.session"
        ),
        "COIN_INTELLIGENCE_EXPECTED_PRIVATE_SESSION_FILE": str(
            market_root / "session" / "telegram-reader.session"
        ),
    }


def test_live_units_do_not_reference_retired_worktrees() -> None:
    retired_paths = (
        "/root/trading-bot/coin-commodity-inference-promotion",
        "/root/trading-bot/combined-staging-overtime-coin",
        "/srv/trading-bot-three-site-staging-data",
        "/srv/trading-bot-three-site",
    )
    for path in SYSTEMD_ROOT.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for retired_path in retired_paths:
            assert retired_path not in content, f"{path} references {retired_path}"


def test_estimator_runtime_surfaces_do_not_reference_retired_data_plane() -> None:
    retired_paths = (
        "/srv/trading-bot-three-site-staging-data",
        "/srv/trading-bot-three-site",
    )
    for path in ESTIMATOR_RUNTIME_SURFACES:
        content = path.read_text(encoding="utf-8")
        for retired_path in retired_paths:
            assert retired_path not in content, f"{path} references {retired_path}"


def test_estimator_dashboard_does_not_wait_for_recurring_group_collector() -> None:
    service = (
        SYSTEMD_ROOT / "coin-rate-estimator-dashboard.service.template"
    ).read_text(encoding="utf-8")

    assert (
        "After=network-online.target coin-public-market-telegram.service\n" in service
    )
    assert (
        "After=network-online.target coin-public-market-telegram.service "
        "coin-group-event-telegram.service"
        not in service
    )


def test_private_input_timers_schedule_after_oneshot_inactivity() -> None:
    expected = {
        "coin-group-event-telegram.timer": (
            "OnUnitInactiveSec=15s",
            "Unit=coin-group-event-telegram.service",
        ),
        "trading-bot-private-gold-collector.timer": (
            "OnUnitInactiveSec=30s",
            "Unit=trading-bot-private-gold-collector.service",
        ),
    }
    for filename, required in expected.items():
        timer = (SYSTEMD_ROOT / filename).read_text(encoding="utf-8")
        assert "OnBootSec=20s" in timer
        assert "OnUnitActiveSec=" not in timer
        assert "OnCalendar=" not in timer
        assert "RandomizedDelaySec=0" in timer
        assert required[0] in timer
        assert required[1] in timer


def test_private_collectors_are_filesystem_write_scoped() -> None:
    for filename in (
        "coin-group-event-telegram.service.template",
        "trading-bot-private-gold-collector.service.template",
    ):
        service = (SYSTEMD_ROOT / filename).read_text(encoding="utf-8")
        assert "ProtectSystem=strict" in service
        assert "ReadWritePaths=" in service


def test_input_timer_installer_is_explicit_idempotent_and_restart_safe() -> None:
    installer = (
        REPOSITORY_ROOT / "scripts/install_coin_intelligence_input_timers.sh"
    ).read_text(encoding="utf-8")
    assert "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM" in installer
    assert "install-coin-intelligence-input-timers" in installer
    assert "systemd-analyze verify" in installer
    assert "capture_prior_units_and_timer_state" in installer
    assert "restore_prior_units_and_state" in installer
    assert "transaction_exit_handler" in installer
    assert "trap 'exit 130' INT" in installer
    assert "trap 'exit 143' TERM" in installer
    assert 'SYSTEMD_DIR="/etc/systemd/system"' in installer
    assert 'BACKUP_ROOT="/var/backups/trading-bot/systemd"' in installer
    assert "PRODUCTION_OPERATION_LOCK_PATH" in installer
    assert "PRODUCTION_SOURCE_LOCK_PATH" in installer
    assert "COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE" in installer
    assert "COIN_INTELLIGENCE_INPUT_TIMERS_REPAIR_CONFIRM" in installer
    assert "repair-production-coin-input-timers" in installer
    assert "explicitly_activated=true" in installer
    assert "prior_state_preserved=true" in installer
    assert "systemctl restart coin-group-event-telegram.service" not in installer
    assert "systemctl restart trading-bot-private-gold-collector.service" not in installer
    assert installer.count("systemd-analyze verify") >= 2


def test_input_timer_installer_rejects_force_active_without_inherited_release_lock() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = _sandbox_input_installer(root)
        result = subprocess.run(
            [str(installer)],
            env={
                **os.environ,
                "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": (
                    "install-coin-intelligence-input-timers"
                ),
                "COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert (
            "input_timer_force_active_requires_release_lock_or_repair_confirmation"
            in result.stderr
        )
        assert not (root / "operation-locks").exists()


def test_input_timer_bounded_repair_uses_self_locks_backup_and_timer_only_activation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = _sandbox_input_installer(root)
        fake_bin = root / "bin"
        systemd_dir = root / "systemd"
        market_root = root / "production-market-runtime"
        estimator_root = root / "production-estimator-runtime"
        for path in (
            fake_bin,
            systemd_dir,
            market_root / "market",
            market_root / "staging",
            market_root / "session",
            market_root / "python-packages",
            estimator_root / "conversation",
        ):
            path.mkdir(parents=True, exist_ok=True)
        _write_secure_collector_inputs(market_root)
        command_log = root / "commands.log"
        state = root / "state"
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
  start|restart) touch "$FAKE_STATE/active.$unit" ;;
  stop) rm -f "$FAKE_STATE/active.$unit" ;;
  show)
    [[ "$1" == "--property=Result" ]] && printf 'success\n' || printf '0\n'
    ;;
  daemon-reload) exit 0 ;;
  *) exit 2 ;;
esac
""",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)
        analyze = fake_bin / "systemd-analyze"
        analyze.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        analyze.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_SYSTEMD_LOG": str(command_log),
            "FAKE_STATE": str(state),
            "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": "install-coin-intelligence-input-timers",
            "COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE": "1",
            "COIN_INTELLIGENCE_INPUT_TIMERS_REPAIR_CONFIRM": "repair-production-coin-input-timers",
            "PROJECT_DIR": str(REPOSITORY_ROOT),
            "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market_root),
            "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator_root),
            "COIN_INTELLIGENCE_SYSTEMD_DIR": str(systemd_dir),
            "COIN_INTELLIGENCE_SYSTEMD_BACKUP_ROOT": str(root / "backups"),
            **_collector_binding_environment(market_root, "-1000000000000"),
        }

        result = subprocess.run(
            [str(installer)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr or result.stdout
        assert "authority=bounded_repair" in result.stdout
        commands = command_log.read_text(encoding="utf-8")
        assert "restart coin-group-event-telegram.timer" in commands
        assert "restart trading-bot-private-gold-collector.timer" in commands
        assert "restart coin-group-event-telegram.service" not in commands
        assert "restart trading-bot-private-gold-collector.service" not in commands
        assert (root / "secure" / ".production-runtime-source.lock").is_file()
        assert not (root / "operation-locks" / "production-release.lock").exists()
        backups = list((root / "backups").glob("coin-input-units.*"))
        assert len(backups) == 1
        assert all((path.stat().st_mode & 0o777) == 0o600 for path in backups[0].iterdir())


def test_input_timer_units_pass_systemd_verify_after_safe_render() -> None:
    replacements = {
        "@CODE_ROOT@": "/opt/trading-bot/current",
        "@MARKET_ENV@": "/opt/trading-bot/runtime/market.env",
        "@GROUP_EVENT_CHANNEL_ID@": "-1000000000000",
        "@ESTIMATOR_RUNTIME_ROOT@": "/opt/trading-bot/runtime/estimator",
        "@PYTHON_PACKAGES@": "/opt/trading-bot/runtime/python-packages",
        "@MARKET_RUNTIME_ROOT@": "/opt/trading-bot/runtime/market",
        "@RUNTIME_ROOT@": "/opt/trading-bot/runtime/market",
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        units = {
            "coin-group-event-telegram.service": "coin-group-event-telegram.service.template",
            "coin-group-event-telegram.timer": "coin-group-event-telegram.timer",
            "trading-bot-private-gold-collector.service": "trading-bot-private-gold-collector.service.template",
            "trading-bot-private-gold-collector.timer": "trading-bot-private-gold-collector.timer",
        }
        rendered_paths = []
        for destination, source in units.items():
            content = (SYSTEMD_ROOT / source).read_text(encoding="utf-8")
            for placeholder, value in replacements.items():
                content = content.replace(placeholder, value)
            assert "@" not in content
            path = root / destination
            path.write_text(content, encoding="utf-8")
            rendered_paths.append(str(path))
        result = subprocess.run(
            ["systemd-analyze", "verify", *rendered_paths],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0, result.stderr or result.stdout


def test_input_timer_installer_is_repeatable_without_restarting_collectors() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = _sandbox_input_installer(root)
        fake_bin = root / "bin"
        systemd_dir = root / "systemd"
        market_root = root / "production-market-runtime"
        estimator_root = root / "production-estimator-runtime"
        for path in (
            fake_bin,
            systemd_dir,
            market_root / "market",
            market_root / "staging",
            market_root / "session",
            market_root / "python-packages",
            estimator_root / "conversation",
        ):
            path.mkdir(parents=True, exist_ok=True)
        _write_secure_collector_inputs(market_root)
        command_log = root / "commands.log"
        fake = "#!/usr/bin/env bash\nprintf '%s %s\\n' \"$(basename \"$0\")\" \"$*\" >>\"$FAKE_SYSTEMD_LOG\"\nexit 0\n"
        for command in ("systemctl", "systemd-analyze"):
            path = fake_bin / command
            path.write_text(fake, encoding="utf-8")
            path.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_SYSTEMD_LOG": str(command_log),
            "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": "install-coin-intelligence-input-timers",
            "PROJECT_DIR": str(REPOSITORY_ROOT),
            "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market_root),
            "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator_root),
            **_collector_binding_environment(market_root, "-1000000000000"),
            "COIN_INTELLIGENCE_SYSTEMD_DIR": str(systemd_dir),
            "COIN_INTELLIGENCE_SYSTEMD_BACKUP_ROOT": str(root / "backups"),
        }
        first = subprocess.run(
            [str(installer)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert first.returncode == 0, first.stderr or first.stdout
        first_units = {
            path.name: path.read_bytes()
            for path in systemd_dir.iterdir()
            if path.is_file()
        }
        second = subprocess.run(
            [str(installer)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert second.returncode == 0, second.stderr or second.stdout
        second_units = {
            path.name: path.read_bytes()
            for path in systemd_dir.iterdir()
            if path.is_file()
        }
        assert first_units == second_units
        commands = command_log.read_text(encoding="utf-8")
        assert commands.count("systemctl restart coin-group-event-telegram.timer") == 2
        assert commands.count("systemctl restart trading-bot-private-gold-collector.timer") == 2
        assert "systemctl restart coin-group-event-telegram.service" not in commands
        assert "systemctl restart trading-bot-private-gold-collector.service" not in commands


def test_input_timer_check_only_requires_exact_installed_units_active_timers_and_successful_runs() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = _sandbox_input_installer(root)
        fake_bin = root / "bin"
        systemd_dir = root / "systemd"
        market_root = root / "production-market-runtime"
        estimator_root = root / "production-estimator-runtime"
        for path in (
            fake_bin,
            systemd_dir,
            market_root / "market",
            market_root / "staging",
            market_root / "session",
            market_root / "python-packages",
            estimator_root / "conversation",
        ):
            path.mkdir(parents=True, exist_ok=True)
        _write_secure_collector_inputs(market_root)
        command_log = root / "commands.log"
        systemctl = fake_bin / "systemctl"
        systemctl.write_text(
            """#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_SYSTEMD_LOG"
case "$1" in
  show)
    [[ "$2" == "--property=Result" ]] && printf 'success\n' || printf '0\n'
    ;;
  *) exit 0 ;;
esac
""",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)
        analyze = fake_bin / "systemd-analyze"
        analyze.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        analyze.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_SYSTEMD_LOG": str(command_log),
            "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": "install-coin-intelligence-input-timers",
            "PROJECT_DIR": str(REPOSITORY_ROOT),
            "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market_root),
            "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator_root),
            **_collector_binding_environment(market_root, "-1000000000000"),
        }
        installed = subprocess.run(
            [str(installer)], env=environment, capture_output=True, text=True, check=False
        )
        assert installed.returncode == 0, installed.stderr or installed.stdout
        before = {path.name: path.read_bytes() for path in systemd_dir.iterdir()}
        command_log.write_text("", encoding="utf-8")
        checked = subprocess.run(
            [str(installer)],
            env={**environment, "COIN_INTELLIGENCE_INPUT_TIMERS_CHECK_ONLY": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert checked.returncode == 0, checked.stderr or checked.stdout
        assert "coin_intelligence_input_timers=ready" in checked.stdout
        assert before == {path.name: path.read_bytes() for path in systemd_dir.iterdir()}
        assert "restart" not in command_log.read_text(encoding="utf-8")

        (systemd_dir / "coin-group-event-telegram.timer").write_text(
            "tampered\n", encoding="utf-8"
        )
        blocked = subprocess.run(
            [str(installer)],
            env={**environment, "COIN_INTELLIGENCE_INPUT_TIMERS_CHECK_ONLY": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert blocked.returncode != 0
        assert "installed_input_unit_contract_invalid" in blocked.stderr


def test_input_timer_installer_rejects_insecure_credentials_scope_and_binding() -> None:
    for case in ("env_mode", "env_symlink", "session_hardlink", "channel", "staging_root"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer = _sandbox_input_installer(root)
            fake_bin = root / "bin"
            systemd_dir = root / "systemd"
            market_name = "staging-market-runtime" if case == "staging_root" else "production-market-runtime"
            market_root = root / market_name
            estimator_root = root / "production-estimator-runtime"
            for path in (
                fake_bin,
                systemd_dir,
                market_root / "market",
                market_root / "staging",
                market_root / "session",
                market_root / "python-packages",
                estimator_root / "conversation",
            ):
                path.mkdir(parents=True, exist_ok=True)
            _write_secure_collector_inputs(market_root)
            for name in ("systemctl", "systemd-analyze"):
                executable = fake_bin / name
                executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": "install-coin-intelligence-input-timers",
                "PROJECT_DIR": str(REPOSITORY_ROOT),
                "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market_root),
                "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator_root),
                "COIN_INTELLIGENCE_SYSTEMD_DIR": str(systemd_dir),
                "COIN_INTELLIGENCE_SYSTEMD_BACKUP_ROOT": str(root / "backups"),
                **_collector_binding_environment(market_root, "-1000000000000"),
            }
            if case == "env_mode":
                (market_root / "public-market-telegram.env").chmod(0o644)
            elif case == "env_symlink":
                env_file = market_root / "public-market-telegram.env"
                target = market_root / "credential-target.env"
                env_file.replace(target)
                env_file.symlink_to(target)
            elif case == "session_hardlink":
                os.link(
                    market_root / "session" / "telegram-reader.session",
                    market_root / "session" / "telegram-reader-copy.session",
                )
            elif case == "channel":
                environment["COIN_INTELLIGENCE_EXPECTED_GROUP_EVENT_CHANNEL_ID"] = "-1009999999999"

            result = subprocess.run(
                [str(installer)],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            assert result.returncode != 0, case
            assert not any(systemd_dir.iterdir()), case
            assert "12345" not in result.stdout + result.stderr


def test_input_timer_installer_rolls_back_units_and_timer_state_on_post_install_verify_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installer = _sandbox_input_installer(root)
        fake_bin = root / "bin"
        state = root / "state"
        systemd_dir = root / "systemd"
        market_root = root / "production-market-runtime"
        estimator_root = root / "production-estimator-runtime"
        for path in (
            fake_bin,
            state,
            systemd_dir,
            market_root / "market",
            market_root / "staging",
            market_root / "session",
            market_root / "python-packages",
            estimator_root / "conversation",
        ):
            path.mkdir(parents=True, exist_ok=True)
        _write_secure_collector_inputs(market_root)
        units = (
            "coin-group-event-telegram.service",
            "coin-group-event-telegram.timer",
            "trading-bot-private-gold-collector.service",
            "trading-bot-private-gold-collector.timer",
        )
        original = {}
        for unit in units:
            payload = f"old-{unit}\n".encode()
            (systemd_dir / unit).write_bytes(payload)
            original[unit] = payload
        group_timer = "coin-group-event-telegram.timer"
        (state / f"enabled.{group_timer}").touch()
        (state / f"active.{group_timer}").touch()
        systemctl = fake_bin / "systemctl"
        systemctl.write_text(
            """#!/usr/bin/env bash
set -u
command="$1"; shift
[[ "${1:-}" == "--quiet" ]] && shift
unit="${1:-}"
case "$command" in
  is-enabled) [[ -f "$FAKE_STATE/enabled.$unit" ]] ;;
  is-active) [[ -f "$FAKE_STATE/active.$unit" ]] && exit 0 || exit 3 ;;
  enable) touch "$FAKE_STATE/enabled.$unit" ;;
  disable) rm -f "$FAKE_STATE/enabled.$unit" ;;
  start|restart) touch "$FAKE_STATE/active.$unit" ;;
  stop) rm -f "$FAKE_STATE/active.$unit" ;;
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
count=0
[[ -f "$FAKE_ANALYZE_COUNT" ]] && count="$(cat "$FAKE_ANALYZE_COUNT")"
count=$((count + 1))
printf '%s' "$count" >"$FAKE_ANALYZE_COUNT"
[[ "$count" != "${FAKE_ANALYZE_FAIL_AT:-0}" ]]
""",
            encoding="utf-8",
        )
        analyze.chmod(0o755)
        environment = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_STATE": str(state),
            "FAKE_ANALYZE_COUNT": str(root / "analyze-count"),
            "FAKE_ANALYZE_FAIL_AT": "2",
            "COIN_INTELLIGENCE_INPUT_TIMERS_CONFIRM": "install-coin-intelligence-input-timers",
            "PROJECT_DIR": str(REPOSITORY_ROOT),
            "COIN_INTELLIGENCE_MARKET_RUNTIME_ROOT": str(market_root),
            "COIN_INTELLIGENCE_ESTIMATOR_RUNTIME_ROOT": str(estimator_root),
            **_collector_binding_environment(market_root, "-1009876543210"),
            "COIN_INTELLIGENCE_SYSTEMD_DIR": str(systemd_dir),
            "COIN_INTELLIGENCE_SYSTEMD_BACKUP_ROOT": str(root / "backups"),
        }
        result = subprocess.run(
            [str(installer)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "rolled_back" in result.stderr
        assert "-1009876543210" not in result.stdout + result.stderr
        assert {unit: (systemd_dir / unit).read_bytes() for unit in units} == original
        assert (state / f"enabled.{group_timer}").is_file()
        assert (state / f"active.{group_timer}").is_file()
        private_timer = "trading-bot-private-gold-collector.timer"
        assert not (state / f"enabled.{private_timer}").exists()
        assert not (state / f"active.{private_timer}").exists()
        backups = list((root / "backups").glob("coin-input-units.*"))
        assert len(backups) == 1
        assert all((backup.stat().st_mode & 0o777) == 0o600 for backup in backups[0].iterdir())
