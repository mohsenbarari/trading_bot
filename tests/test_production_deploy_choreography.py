"""Direct, sandboxed contracts for production deployment choreography."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"


def run_sourced_script(body: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'source "$1"\n{body}', "production-deploy-test", str(RELEASE_SCRIPT), *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TZ": "UTC",
        },
    )


class ProductionDeployChoreographyTests(unittest.TestCase):
    def test_production_compose_identity_ignores_ambient_pollution_and_is_fixed(self) -> None:
        locked = run_sourced_script(
            """
COMPOSE_PROJECT_NAME=attacker_project
FOREIGN_COMPOSE_PROJECT_NAME=trading_bot
lock_production_compose_project_identity
printf '%s|%s\n' "$FOREIGN_COMPOSE_PROJECT_NAME" "$COMPOSE_PROJECT_NAME"
"""
        )
        self.assertEqual(locked.returncode, 0, locked.stderr + locked.stdout)
        self.assertEqual(locked.stdout.strip(), "trading_bot|trading_bot")
        rejected = run_sourced_script(
            """
FOREIGN_COMPOSE_PROJECT_NAME=wrong_project
lock_production_compose_project_identity
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        load_manifest = source.split("load_manifest() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            load_manifest.index("unset COMPOSE_PROJECT_NAME FOREIGN_COMPOSE_PROJECT_NAME"),
            load_manifest.index('source "$MANIFEST_PATH"'),
        )

    def test_relay_state_path_ignores_ambient_override_and_rejects_runtime_alias(self) -> None:
        environment = {
            "PATH": os.environ["PATH"],
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TZ": "UTC",
            "PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE": "/tmp/production-operator-owned.json",
        }
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; printf "%s\\n" "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE"',
                "production-deploy-test",
                str(RELEASE_SCRIPT),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            result.stdout.strip(),
            "/var/lib/trading-bot/production-release/coin-snapshot-relay-state.json",
        )
        rejected = run_sourced_script(
            """
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE=/tmp/production-operator-owned.json
validate_production_coin_relay_state_file
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("canonical production path", rejected.stderr)

    def test_runtime_render_outputs_cannot_alias_live_env_destinations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-env-alias-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = root / "immutable-source.env"
            source.write_text("SECRET=source-value\n", encoding="utf-8")
            live = project / ".env"
            original = b"SECRET=live-value\n"
            live.write_bytes(original)
            iran_render = root / "iran-render.env"
            result = run_sourced_script(
                """
LOCAL_PROJECT_DIR="$2"
IRAN_PROJECT_DIR=/srv/trading-bot/production-current
RUNTIME_ENV_SOURCE_PATH="$3"
FOREIGN_RUNTIME_ENV_PATH="$4"
IRAN_RUNTIME_ENV_PATH="$5"
ALLOW_PROJECT_ENV_SOURCE=0
validate_runtime_env_source_policy
""",
                str(project),
                str(source),
                str(live),
                str(iran_render),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("staging-only", result.stderr)
            self.assertEqual(live.read_bytes(), original)

            direct_install = run_sourced_script(
                """
ENV_BACKUP_DIR="$3"
RELEASE_ARTIFACT_DIR="$4"
atomic_install_local_runtime_env "$2" "$2" foreign
""",
                str(live),
                str(root / "backups"),
                str(root / "artifacts"),
            )
            self.assertNotEqual(direct_install.returncode, 0)
            self.assertIn("separate from its live destination", direct_install.stderr)
            self.assertEqual(live.read_bytes(), original)

    def test_immutable_runtime_source_rejects_world_readable_and_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-secure-source-") as temporary:
            root = Path(temporary)
            source = root / "source.env"
            source.write_text("PRIVATE_VALUE=not-printed\n", encoding="utf-8")
            source.chmod(0o644)
            insecure = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
validate_secure_runtime_env_source_file
""",
                str(source),
            )
            self.assertNotEqual(insecure.returncode, 0)
            self.assertNotIn("not-printed", insecure.stdout + insecure.stderr)
            source.chmod(0o600)
            secure = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
validate_secure_runtime_env_source_file
""",
                str(source),
            )
            self.assertEqual(secure.returncode, 0, secure.stderr + secure.stdout)
            alias = root / "source-link.env"
            alias.symlink_to(source)
            symlink = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
validate_secure_runtime_env_source_file
""",
                str(alias),
            )
            self.assertNotEqual(symlink.returncode, 0)
            pending = root / ".production-runtime-source.pending.json"
            pending.write_text('{"status":"PREPARED"}\n', encoding="utf-8")
            pending.chmod(0o600)
            pending_result = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
validate_secure_runtime_env_source_file
""",
                str(source),
            )
            self.assertNotEqual(pending_result.returncode, 0)

    def test_release_locked_runtime_pair_rejects_source_drift_between_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-env-drift-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = root / "source.env"
            foreign = root / "foreign.env"
            iran = root / "iran.env"
            source.write_text("PRIVATE_VALUE=first\n", encoding="utf-8")
            foreign.write_text("ROLE=foreign\n", encoding="utf-8")
            iran.write_text("ROLE=iran\n", encoding="utf-8")
            result = run_sourced_script(
                """
LOCAL_PROJECT_DIR="$2"
RUNTIME_ENV_SOURCE_PATH="$3"
FOREIGN_RUNTIME_ENV_PATH="$4"
IRAN_RUNTIME_ENV_PATH="$5"
PRODUCTION_RUNTIME_ENV_PAIR_LOCKED=1
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$(sha256sum "$3" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(sha256sum "$4" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(sha256sum "$5" | awk '{print $1}')"
printf 'PRIVATE_VALUE=second\n' >"$3"
verify_runtime_env_pair_lock
""",
                str(project),
                str(source),
                str(foreign),
                str(iran),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source drifted", result.stderr)
            self.assertNotIn("PRIVATE_VALUE", result.stdout + result.stderr)

    def test_regular_release_source_lock_blocks_the_shared_inference_updater(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-shared-source-lock-") as temporary:
            root = Path(temporary)
            source = root / "immutable.env"
            source.write_text("PRIVATE_VALUE=stable\n", encoding="utf-8")
            source.chmod(0o600)
            manifest = root / "online.env"
            manifest.write_text(
                f"RUNTIME_ENV_SOURCE_PATH={source}\n"
                "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1\n"
                "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot\n",
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            digest = sha256(source.read_bytes()).hexdigest()
            result = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
acquire_production_source_lock
set +e
PYTHONPATH="$3" python3 - "$4" "$5" "$6" "$7" <<'PY'
import sys
from hashlib import sha256
from pathlib import Path
from scripts import update_production_coin_inference_source as updater
updater.APPROVED_MANIFEST_PATH = Path(sys.argv[1])
updater.APPROVED_MANIFEST_ROOTS = (Path(sys.argv[1]).parent,)
raise SystemExit(updater.main([
    "apply", "--manifest", sys.argv[1],
    "--expected-source-sha256", sys.argv[2],
    "--expected-manifest-sha256", sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
    "--confirm", updater.APPLY_CONFIRMATION,
    "--backup-dir", sys.argv[3],
    "--receipt", sys.argv[4],
]))
PY
status=$?
set -e
release_production_source_lock
exit "$status"
""",
                str(source),
                str(REPO_ROOT),
                str(manifest),
                digest,
                str(root / "backups"),
                str(root / "receipts" / "activation.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("immutable_source_update_locked", result.stdout)
            self.assertEqual(sha256(source.read_bytes()).hexdigest(), digest)

    def test_verified_authority_detects_cutover_held_source_lock_without_deadlock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-inherited-source-lock-") as temporary:
            source = Path(temporary) / "immutable.env"
            source.write_text("PRIVATE_VALUE=stable\n", encoding="utf-8")
            source.chmod(0o600)
            result = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
prepare_production_source_lock
exec {holder_fd}<>"$PRODUCTION_SOURCE_LOCK_PATH"
flock -n "$holder_fd"
verify_inherited_production_source_lock
[[ "$PRODUCTION_SOURCE_LOCK_INHERITED_OBSERVED" == 1 ]]
flock -u "$holder_fd"
exec {holder_fd}>&-
""",
                str(source),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_release_exit_guard_always_releases_both_locks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-exit-guard-") as temporary:
            command_log = Path(temporary) / "cleanup.log"
            result = run_sourced_script(
                """
COMMAND_LOG="$2"
production_release_relay_exit_guard() { return "$1"; }
release_production_source_lock() { printf 'source\n' >>"$COMMAND_LOG"; }
release_production_operation_lock() { printf 'operation\n' >>"$COMMAND_LOG"; }
( trap production_release_exit_guard EXIT; false )
""",
                str(command_log),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(command_log.read_text(encoding="utf-8").splitlines(), ["source", "operation"])

    def test_failure_after_coin_input_install_restores_exact_prior_units_and_timer_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-coin-input-rollback-") as temporary:
            root = Path(temporary)
            systemd_dir = root / "systemd"
            systemd_dir.mkdir()
            recovery_dir = root / "release-state" / "coin-input-timer-recovery"
            command_log = root / "systemctl.log"
            units = (
                "coin-group-event-telegram.service",
                "coin-group-event-telegram.timer",
                "trading-bot-private-gold-collector.service",
            )
            originals = {unit: f"prior:{unit}\n".encode() for unit in units}
            for unit, contents in originals.items():
                (systemd_dir / unit).write_bytes(contents)

            result = run_sourced_script(
                r'''
PRODUCTION_COIN_INPUT_SYSTEMD_DIR="$2"
PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR="$3"
PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR_CANONICAL="$3"
COMMAND_LOG="$4"
FAKE_STATE_DIR="$(dirname "$COMMAND_LOG")/systemctl-state"
mkdir -p "$FAKE_STATE_DIR"
RELEASE_SHA=abababababababababababababababababababab
PRODUCTION_COIN_INFERENCE_REQUESTED=1
printf '1\n' >"$FAKE_STATE_DIR/coin-group-event-telegram.timer.enabled"
printf '1\n' >"$FAKE_STATE_DIR/coin-group-event-telegram.timer.active"
printf '0\n' >"$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.enabled"
printf '0\n' >"$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.active"
systemctl() {
  local command="$1" unit="${@: -1}"
  printf '%s %s\n' "$command" "$unit" >>"$COMMAND_LOG"
  case "$command" in
    is-enabled) [[ "$(cat "$FAKE_STATE_DIR/$unit.enabled")" == 1 ]] ;;
    is-active) [[ "$(cat "$FAKE_STATE_DIR/$unit.active")" == 1 ]] || return 3 ;;
    enable) printf '1\n' >"$FAKE_STATE_DIR/$unit.enabled" ;;
    disable) printf '0\n' >"$FAKE_STATE_DIR/$unit.enabled" ;;
    restart) printf '1\n' >"$FAKE_STATE_DIR/$unit.active" ;;
    stop) printf '0\n' >"$FAKE_STATE_DIR/$unit.active" ;;
    daemon-reload) return 0 ;;
    *) return 2 ;;
  esac
}
capture_production_coin_input_timer_recovery_state
for unit in \
  coin-group-event-telegram.service \
  coin-group-event-telegram.timer \
  trading-bot-private-gold-collector.service \
  trading-bot-private-gold-collector.timer; do
  printf 'new:%s\n' "$unit" >"$PRODUCTION_COIN_INPUT_SYSTEMD_DIR/$unit"
done
printf '1\n' >"$FAKE_STATE_DIR/coin-group-event-telegram.timer.enabled"
printf '1\n' >"$FAKE_STATE_DIR/coin-group-event-telegram.timer.active"
printf '1\n' >"$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.enabled"
printf '1\n' >"$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.active"
two_host_release_exit_guard() { return "$1"; }
production_release_relay_exit_guard() { return "$1"; }
release_production_locks() { printf 'release-locks\n' >>"$COMMAND_LOG"; }
set +e
( trap production_release_exit_guard EXIT; false )
guard_status=$?
set -e
[[ "$guard_status" == 1 ]]
[[ ! -e "$PRODUCTION_COIN_INPUT_TIMER_RECOVERY_DIR" ]]
[[ "$(cat "$FAKE_STATE_DIR/coin-group-event-telegram.timer.enabled")" == 1 ]]
[[ "$(cat "$FAKE_STATE_DIR/coin-group-event-telegram.timer.active")" == 1 ]]
[[ "$(cat "$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.enabled")" == 0 ]]
[[ "$(cat "$FAKE_STATE_DIR/trading-bot-private-gold-collector.timer.active")" == 0 ]]
''',
                str(systemd_dir),
                str(recovery_dir),
                str(command_log),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            for unit, contents in originals.items():
                self.assertEqual((systemd_dir / unit).read_bytes(), contents)
            self.assertFalse(
                (systemd_dir / "trading-bot-private-gold-collector.timer").exists()
            )
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn("enable coin-group-event-telegram.timer", commands)
            self.assertIn("restart coin-group-event-telegram.timer", commands)
            self.assertIn("disable trading-bot-private-gold-collector.timer", commands)
            self.assertIn("stop trading-bot-private-gold-collector.timer", commands)
            self.assertIn("release-locks", commands)
            self.assertIn(
                "production_coin_input_timers=prior_units_and_state_restored",
                result.stderr,
            )

    def test_installed_runtime_pair_must_match_locked_render_digests_on_both_hosts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-env-pair-") as temporary:
            root = Path(temporary)
            project = root / "foreign-project"
            remote_project = root / "iran-production-project"
            project.mkdir()
            remote_project.mkdir()
            source = root / "source.env"
            foreign = root / "foreign.env"
            iran = root / "iran.env"
            source.write_text("SOURCE=stable\n", encoding="utf-8")
            foreign.write_text("ROLE=foreign\n", encoding="utf-8")
            iran.write_text("ROLE=iran\n", encoding="utf-8")
            (project / ".env").write_bytes(foreign.read_bytes())
            (remote_project / ".env").write_bytes(iran.read_bytes())
            result = run_sourced_script(
                """
LOCAL_PROJECT_DIR="$2"
IRAN_PROJECT_DIR="$3"
RUNTIME_ENV_SOURCE_PATH="$4"
FOREIGN_RUNTIME_ENV_PATH="$5"
IRAN_RUNTIME_ENV_PATH="$6"
PRODUCTION_RUNTIME_ENV_PAIR_LOCKED=1
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$(sha256sum "$4" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(sha256sum "$5" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(sha256sum "$6" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_INSTALLED=1
ssh_iran() { bash -c "$1"; }
verify_installed_runtime_env_pair
""",
                str(project),
                str(remote_project),
                str(source),
                str(foreign),
                str(iran),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            (remote_project / ".env").write_text("ROLE=drifted\n", encoding="utf-8")
            drift = run_sourced_script(
                """
LOCAL_PROJECT_DIR="$2"
IRAN_PROJECT_DIR="$3"
RUNTIME_ENV_SOURCE_PATH="$4"
FOREIGN_RUNTIME_ENV_PATH="$5"
IRAN_RUNTIME_ENV_PATH="$6"
PRODUCTION_RUNTIME_ENV_PAIR_LOCKED=1
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$(sha256sum "$4" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(sha256sum "$5" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(sha256sum "$6" | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_INSTALLED=1
ssh_iran() { bash -c "$1"; }
verify_installed_runtime_env_pair
""",
                str(project),
                str(remote_project),
                str(source),
                str(foreign),
                str(iran),
            )
            self.assertNotEqual(drift.returncode, 0)
            self.assertIn("Installed Iran runtime env", drift.stderr)

    def test_iran_key_transport_is_identical_for_ssh_scp_and_rsync(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-transport-") as temporary:
            identity = Path(temporary) / "production-key"
            identity.write_text("test-key-material\n", encoding="utf-8")
            identity.chmod(0o600)
            result = run_sourced_script(
                """
IRAN_HOST=production-host.example
IRAN_SSH_USER=root
IRAN_SSH_PORT=2200
IRAN_SSH_AUTH_METHOD=key
IRAN_SSH_PRIVATE_KEY_PATH="$2"
configure_iran_transport
printf 'ssh=%s\nscp=%s\nrsync=%s\n' \
  "$(render_shell_command "${SSH_IRAN_CMD[@]}")" \
  "$(render_shell_command "${SCP_IRAN_CMD[@]}")" \
  "$RSYNC_SSH"
""",
                str(identity),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            for line in result.stdout.splitlines():
                self.assertIn(str(identity), line)
                self.assertIn("PasswordAuthentication=no", line)
                self.assertIn("KbdInteractiveAuthentication=no", line)
                self.assertIn("IdentitiesOnly=yes", line)
                self.assertIn("ConnectTimeout=10", line)
                self.assertIn("ServerAliveInterval=15", line)
                self.assertIn("ServerAliveCountMax=3", line)
                self.assertIn("ConnectionAttempts=1", line)
            self.assertIn("BatchMode=yes", result.stdout)
            self.assertIn("timeout", result.stdout)
            self.assertIn("900s", result.stdout)
            source = RELEASE_SCRIPT.read_text(encoding="utf-8")
            self.assertIn("RSYNC_SSH=\"$(render_shell_command", source)
            self.assertIn("sshpass -e ssh", source)
            self.assertNotIn("sshpass -p \"$IRAN_SSH_PASSWORD\"", source)

    def test_direct_runtime_mutations_are_refused_outside_full_release(self) -> None:
        for command in (
            "deploy-foreign",
            "deploy-iran",
            "sync-project",
            "ship-images",
            "load-images",
            "seed-shared-data",
        ):
            with self.subTest(command=command):
                rejected = run_sourced_script(
                    f'COMMAND={command!r}\nguard_production_release_command\n'
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("internal to the full two-host release", rejected.stderr)

    def test_queue_profile_allows_only_non_runtime_release_evidence_commands(self) -> None:
        for command in (
            "prepare-release-evidence",
            "verify-release-evidence",
            "prepare-private-primary-control-release",
        ):
            with self.subTest(command=command):
                allowed = run_sourced_script(
                    f"""
COMMAND={command!r}
acquire_production_operation_lock() {{ :; }}
acquire_production_source_lock() {{ :; }}
production_runtime_source_profile() {{ printf 'queue-v1\\n'; }}
guard_production_release_command
printf 'allowed\\n'
"""
                )
                self.assertEqual(
                    allowed.returncode,
                    0,
                    allowed.stderr + allowed.stdout,
                )
                self.assertEqual(allowed.stdout.strip(), "allowed")

        rejected = run_sourced_script(
            """
COMMAND=release
acquire_production_operation_lock() { :; }
acquire_production_source_lock() { :; }
production_runtime_source_profile() { printf 'queue-v1\n'; }
guard_production_release_command
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("guarded cutover authority", rejected.stderr)

    def test_release_rechecks_frozen_source_and_remote_payload_after_quiesce(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertGreaterEqual(release.count("verify_frozen_release_source"), 2)
        self.assertLess(
            release.index("verify_frozen_release_source"),
            release.index("quiesce_two_host_writers_for_migration"),
        )
        sync = source.split("sync_project() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("run_iran_transfer rsync", sync)
        self.assertIn("verify_remote_immutable_runtime_payload", sync)
        iran = source.split("deploy_iran() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            iran.index("verify_remote_immutable_runtime_payload"),
            iran.index("run --rm --no-deps migration"),
        )
        self.assertIn("IRAN_SSH_COMMAND_TIMEOUT_SECONDS", source)
        self.assertIn("IRAN_TRANSFER_TIMEOUT_SECONDS", source)

    def test_committed_iran_payload_omits_remote_preserved_paths_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="iran-source-payload-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "runtime.py").write_text("RUNTIME = True\n", encoding="utf-8")
            (project / ".env.example").write_text("EXAMPLE=true\n", encoding="utf-8")
            (project / "tmp").mkdir()
            (project / "tmp" / "tracked.md").write_text("planning\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project),
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release-test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            release_sha = subprocess.check_output(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            result = run_sourced_script(
                """
verify_frozen_release_source() { :; }
LOCAL_PROJECT_DIR="$2"
RELEASE_SHA="$3"
RELEASE_TMP_DIR="$4"
LOCAL_IRAN_SOURCE_PAYLOAD_DIR="$RELEASE_TMP_DIR/iran-source-payload"
LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST="$RELEASE_TMP_DIR/iran-source-payload.sha256"
prepare_committed_iran_source_payload
test -f "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR/runtime.py"
test ! -e "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR/tmp"
test ! -e "$LOCAL_IRAN_SOURCE_PAYLOAD_DIR/.env.example"
! grep -qE '(^|/)(tmp|\\.env\\.example)(/|$)' "$LOCAL_IRAN_SOURCE_PAYLOAD_MANIFEST"
""",
                str(project),
                release_sha,
                str(root / "release"),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_iran_web_root_grants_only_nginx_traversal_and_keeps_env_private(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        sync = source.split("sync_project() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("setfacl -m u:www-data:--x '$IRAN_PROJECT_DIR'", sync)
        self.assertIn(
            "runuser -u www-data -- test -r '$IRAN_PROJECT_DIR/mini_app_dist/index.html'",
            sync,
        )
        self.assertIn(
            "runuser -u www-data -- test ! -r '$IRAN_PROJECT_DIR/.env'",
            sync,
        )

    def test_authorized_inference_release_explicitly_activates_input_timers(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        installer = source.split("run_production_coin_input_timer_installer() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("COIN_INTELLIGENCE_INPUT_TIMERS_FORCE_ACTIVE=1", installer)

    def test_local_runtime_env_install_is_atomic_backed_up_and_secret_free_in_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-env-local-") as temporary:
            root = Path(temporary)
            project = root / "production-project"
            rendered = root / "rendered.env"
            backup_root = root / "secure-backups"
            artifacts = root / "artifacts"
            project.mkdir()
            old_payload = b"PRIVATE_VALUE=old-sensitive-value\n"
            new_payload = b"PRIVATE_VALUE=new-sensitive-value\n"
            (project / ".env").write_bytes(old_payload)
            rendered.write_bytes(new_payload)

            result = run_sourced_script(
                """
LOCAL_PROJECT_DIR="$2"
FOREIGN_RUNTIME_ENV_PATH="$3"
ENV_BACKUP_DIR="$4"
RELEASE_ARTIFACT_DIR="$5"
atomic_install_local_runtime_env "$FOREIGN_RUNTIME_ENV_PATH" "$LOCAL_PROJECT_DIR/.env" foreign
""",
                str(project),
                str(rendered),
                str(backup_root),
                str(artifacts),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertNotIn("sensitive-value", result.stdout + result.stderr)
            self.assertEqual((project / ".env").read_bytes(), new_payload)
            self.assertEqual(stat.S_IMODE((project / ".env").stat().st_mode), 0o600)

            backups = list((backup_root / "live-runtime-env").glob("foreign-before-install.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), old_payload)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

            receipt_path = artifacts / "runtime-env-install-foreign.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            expected_digest = sha256(new_payload).hexdigest()
            self.assertEqual(receipt["expected_sha256"], expected_digest)
            self.assertEqual(receipt["installed_sha256"], expected_digest)
            self.assertEqual(receipt["previous_backup_sha256"], sha256(old_payload).hexdigest())
            self.assertFalse(receipt["secret_values_retained"])
            self.assertNotIn("sensitive-value", receipt_path.read_text(encoding="utf-8"))

    def test_mocked_iran_runtime_env_install_uses_same_directory_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-env-iran-") as temporary:
            root = Path(temporary)
            project = root / "production-iran-project"
            deploy_base = root / "production-iran-base"
            rendered = root / "rendered-iran.env"
            artifacts = root / "artifacts"
            project.mkdir()
            old_payload = b"PRIVATE_VALUE=old-remote-sensitive-value\n"
            new_payload = b"PRIVATE_VALUE=new-remote-sensitive-value\n"
            (project / ".env").write_bytes(old_payload)
            rendered.write_bytes(new_payload)

            result = run_sourced_script(
                """
IRAN_PROJECT_DIR="$2"
IRAN_DEPLOY_BASE_DIR="$3"
IRAN_RUNTIME_ENV_PATH="$4"
IRAN_SSH_TARGET="mock@production-host"
RELEASE_ARTIFACT_DIR="$5"
ssh_iran() { bash -c "$1"; }
scp_iran() { local source="$1" destination="${2#*:}"; cp -- "$source" "$destination"; }
atomic_install_iran_runtime_env
""",
                str(project),
                str(deploy_base),
                str(rendered),
                str(artifacts),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertNotIn("sensitive-value", result.stdout + result.stderr)
            self.assertEqual((project / ".env").read_bytes(), new_payload)
            self.assertEqual(stat.S_IMODE((project / ".env").stat().st_mode), 0o600)

            backups = list(
                (deploy_base / "secure-env-backups" / "runtime-env").glob("iran-before-install.*")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), old_payload)
            receipt = json.loads(
                (artifacts / "runtime-env-install-iran.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["expected_sha256"], sha256(new_payload).hexdigest())
            self.assertEqual(receipt["installed_sha256"], sha256(new_payload).hexdigest())
            self.assertEqual(receipt["previous_backup_sha256"], sha256(old_payload).hexdigest())

    def test_runtime_bind_directories_are_canonical_production_only_and_precede_compose(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-runtime-contract-") as temporary:
            root = Path(temporary)
            foreign_env = root / "foreign.env"
            iran_env = root / "iran.env"
            foreign_runtime = root / "foreign-production-runtime"
            iran_runtime = root / "iran-production-runtime"
            foreign_env.write_text(
                "\n".join(
                    (
                        f"PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR={foreign_runtime}",
                        "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120",
                        "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR=/app/runtime/coin-inference",
                        "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/coin-rates.json",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            iran_env.write_text(
                "\n".join(
                    (
                        f"PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR={iran_runtime}",
                        "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120",
                        "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR=/app/runtime/coin-inference",
                        "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/coin-rates.json",
                        "",
                    )
                ),
                encoding="utf-8",
            )

            result = run_sourced_script(
                """
FOREIGN_RUNTIME_ENV_PATH="$2"
IRAN_RUNTIME_ENV_PATH="$3"
ssh_iran() { bash -c "$1"; }
ensure_local_production_coin_runtime_dir
ensure_remote_production_coin_runtime_dir
""",
                str(foreign_env),
                str(iran_env),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(foreign_runtime.is_dir())
            self.assertTrue(iran_runtime.is_dir())
            self.assertEqual(stat.S_IMODE(foreign_runtime.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(iran_runtime.stat().st_mode), 0o755)

            for invalid in (
                root / "staging-production-runtime",
                root / "production-runtime" / ".." / "alias",
            ):
                invalid_result = run_sourced_script(
                    'validate_production_coin_runtime_dir "$2" test-role', str(invalid)
                )
                self.assertNotEqual(invalid_result.returncode, 0)

            iran_env.write_text(
                iran_env.read_text(encoding="utf-8").replace(
                    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/coin-rates.json",
                    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/other.json",
                ),
                encoding="utf-8",
            )
            mismatched_container_path = run_sourced_script(
                """
FOREIGN_RUNTIME_ENV_PATH="$2"
IRAN_RUNTIME_ENV_PATH="$3"
resolve_production_coin_runtime_contract
""",
                str(foreign_env),
                str(iran_env),
            )
            self.assertNotEqual(mismatched_container_path.returncode, 0)
            self.assertIn("exact canonical path", mismatched_container_path.stderr)

            iran_env.write_text(
                iran_env.read_text(encoding="utf-8").replace(
                    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120",
                    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=121",
                ).replace(
                    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/other.json",
                    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH=/app/runtime/coin-inference/coin-rates.json",
                ),
                encoding="utf-8",
            )
            invalid_age = run_sourced_script(
                """
FOREIGN_RUNTIME_ENV_PATH="$2"
IRAN_RUNTIME_ENV_PATH="$3"
resolve_production_coin_runtime_contract
""",
                str(foreign_env),
                str(iran_env),
            )
            self.assertNotEqual(invalid_age.returncode, 0)
            self.assertIn("exactly 120 seconds", invalid_age.stderr)

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        foreign_deploy = source.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            foreign_deploy.index("ensure_local_production_coin_runtime_dir"),
            foreign_deploy.index("bash ./deploy.sh foreign"),
        )
        iran_deploy = source.split("deploy_iran() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            iran_deploy.index("ensure_remote_production_coin_runtime_dir"),
            iran_deploy.index("docker-compose.iran.yml up -d"),
        )

    def test_relay_is_opt_in_and_reconciled_only_after_remote_script_sync(self) -> None:
        disabled = run_sourced_script(
            """
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=''
validate_production_coin_relay_manifest
"""
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr + disabled.stdout)
        rejected = run_sourced_script(
            """
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=wrong
validate_production_coin_relay_manifest
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("exact manifest confirmation", rejected.stderr)

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        manifest_validation = source.split(
            "validate_production_coin_relay_manifest() {", 1
        )[1].split("\n}", 1)[0]
        self.assertEqual(
            manifest_validation.count(
                "Production coin Snapshot relay enablement requires the exact manifest confirmation."
            ),
            1,
        )
        check_local = source.split("check_local() {", 1)[1].split("\n}", 1)[0]
        preparation = source.split("prepare_local_release_inputs() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("validate_production_coin_relay_manifest", check_local)
        self.assertLess(
            preparation.index("check_local"),
            preparation.index("ensure_runtime_env_file"),
        )
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(release.index("suspend_production_coin_snapshot_relay"), release.index("deploy_foreign"))
        self.assertLess(release.index("sync_project"), release.index("reconcile_production_coin_snapshot_relay"))
        self.assertLess(release.index("reconcile_production_coin_snapshot_relay"), release.index("deploy_iran"))

        reconcile = source.split("reconcile_production_coin_snapshot_relay() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("verify_production_coin_relay_script_parity", reconcile)
        self.assertIn("PRODUCTION_COIN_INFERENCE_REMOTE_HOST=\"$IRAN_SSH_TARGET\"", reconcile)
        self.assertIn("verify_production_coin_snapshot_relay", reconcile)
        self.assertIn("PRODUCTION_COIN_INFERENCE_RELAY_ENABLED", reconcile)
        verification_failure = reconcile.split(
            "if ! (verify_production_coin_snapshot_relay); then", 1
        )[1]
        self.assertIn('systemctl stop "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER"', verification_failure)
        self.assertIn("the relay was left stopped", verification_failure)

    def test_check_local_is_read_only_and_mutating_preparation_is_lock_guarded(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        check = source.split("check_local() {", 1)[1].split("\n}", 1)[0]
        for mutator in (
            "ensure_local_runtime_packages",
            "ensure_runtime_env_file",
            "render_release_artifacts",
            "apt-get",
        ):
            self.assertNotIn(mutator, check)
        prepare = source.split("prepare_local_release_inputs() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("ensure_local_runtime_packages", prepare)
        self.assertIn("ensure_runtime_env_file", prepare)
        self.assertIn("render_release_artifacts", prepare)
        main = source.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            main.index("guard_production_release_command"),
            main.index("prepare_local_release_inputs"),
        )

    def test_release_git_gate_requires_live_origin_main_before_preparation(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        gate = source.split("ensure_production_release_git_ref() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn('[[ "$upstream" == "origin/main" ]]', gate)
        self.assertIn("refs/remotes/origin/main^{commit}", gate)
        self.assertIn("ls-remote --exit-code origin refs/heads/main", gate)
        self.assertNotIn("skipping upstream equality check", gate)

    def test_offline_full_release_exits_before_mutable_local_preparation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-offline-release-") as temporary:
            root = Path(temporary)
            marker = root / "mutable-preparation-ran"
            guard_marker = root / "release-guard-ran"
            manifest = root / "online.env"
            manifest.write_text("# test manifest\n", encoding="utf-8")
            result = run_sourced_script(
                r'''
MANIFEST_PATH="$2"
PREPARATION_MARKER="$3"
GUARD_MARKER="$4"
load_manifest() { IRAN_CONNECTIVITY_MODE=offline; }
guard_production_release_command() {
  printf 'unexpected\n' > "$GUARD_MARKER"
}
prepare_local_release_inputs() {
  printf 'unexpected\n' > "$PREPARATION_MARKER"
}
main release
''',
                str(manifest),
                str(marker),
                str(guard_marker),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no mutable release preparation", result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(guard_marker.exists())

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            release.index("decide_iran_connectivity"),
            release.index("prepare_local_release_inputs"),
        )
        main = source.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(main.index("load_manifest"), main.index("decide_iran_connectivity"))
        self.assertLess(
            main.index("decide_iran_connectivity"),
            main.index("guard_production_release_command"),
        )

    def test_image_bundle_transport_hashes_content_and_installs_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-image-transport-") as temporary:
            root = Path(temporary)
            local_bundle = root / "local-images.tar"
            remote_bundle = root / "remote-images.tar"
            remote_sha = root / "remote-images.tar.sha256"
            transfer_marker = root / "scp-ran"
            release_state = root / "state"
            local_bundle.write_bytes(b"exact-release-image-bundle")
            remote_bundle.write_bytes(b"different-remote-content")
            remote_sha.write_text(sha256(local_bundle.read_bytes()).hexdigest() + "\n", encoding="utf-8")
            result = run_sourced_script(
                r'''
LOCAL_IMAGE_BUNDLE="$2"
REMOTE_IMAGE_BUNDLE="$3"
REMOTE_IMAGE_BUNDLE_SHA="$4"
REMOTE_RELEASE_STATE_DIR="$5"
IRAN_DEPLOY_BASE_DIR="$6"
IRAN_SSH_TARGET=fake-iran
IRAN_FORCE_RELEASE_REFRESH=0
TRANSFER_MARKER="$7"
ssh_iran() { bash -c "$1"; }
scp_iran() {
  printf 'called\n' > "$TRANSFER_MARKER"
  cp -- "$1" "${2#*:}"
}
ship_images
''',
                str(local_bundle),
                str(remote_bundle),
                str(remote_sha),
                str(release_state),
                str(root),
                str(transfer_marker),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(transfer_marker.exists(), "a matching sidecar must not hide corrupt bundle bytes")
            self.assertEqual(remote_bundle.read_bytes(), local_bundle.read_bytes())
            self.assertEqual(remote_sha.read_text(encoding="utf-8").strip(), sha256(local_bundle.read_bytes()).hexdigest())
            self.assertFalse(Path(f"{remote_bundle}.uploading").exists())

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        ship = source.split("ship_images() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("sha256sum", ship)
        self.assertIn(".uploading", ship)
        self.assertIn("mv -f", ship)
        self.assertNotIn(f"cat '$REMOTE_IMAGE_BUNDLE_SHA'", ship)

    def test_load_images_rejects_remote_bundle_mismatch_before_docker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-image-load-") as temporary:
            root = Path(temporary)
            local_bundle = root / "local-images.tar"
            remote_bundle = root / "remote-images.tar"
            docker_marker = root / "docker-ran"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                f"#!/bin/sh\nprintf called > {docker_marker}\nexit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)
            local_bundle.write_bytes(b"expected-image-bundle")
            remote_bundle.write_bytes(b"tampered-image-bundle")
            result = run_sourced_script(
                r'''
PATH="$2:$PATH"
export PATH
LOCAL_IMAGE_BUNDLE="$3"
LOCAL_IMAGE_SIGNATURE_FILE="$4"
REMOTE_IMAGE_BUNDLE="$5"
REMOTE_IMAGE_LOADED_SIGNATURE="$6"
REMOTE_RELEASE_STATE_DIR="$7"
IRAN_FORCE_RELEASE_REFRESH=1
IRAN_HOST_ARCH=amd64
ssh_iran() { bash -c "$1"; }
verify_iran_image_build_receipt() { :; }
load_images
''',
                str(fake_bin),
                str(local_bundle),
                str(root / "missing.signature"),
                str(remote_bundle),
                str(root / "loaded.signature"),
                str(root / "state"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing docker load", result.stderr)
            self.assertFalse(docker_marker.exists())

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        loader = source.split("load_images() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("sha256sum", loader)
        self.assertLess(loader.index("actual_bundle_sha"), loader.index("docker load -i"))
        self.assertNotIn("REMOTE_IMAGE_BUNDLE_SHA", loader)

    def test_relay_enable_rejects_password_auth(self) -> None:
        rejected = run_sourced_script(
            """
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot
IRAN_SSH_AUTH_METHOD=password
validate_production_coin_relay_manifest
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("non-interactive key authentication", rejected.stderr)

    def test_relay_enable_requires_explicit_private_identity(self) -> None:
        rejected = run_sourced_script(
            """
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot
IRAN_SSH_AUTH_METHOD=key
IRAN_SSH_PRIVATE_KEY_PATH=''
validate_production_coin_relay_manifest
"""
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("explicit identity file", rejected.stderr)

    def test_active_or_enabled_relay_requires_exact_disable_confirmation_and_stays_stopped(self) -> None:
        for active, enabled in ((1, 1), (0, 1)):
            with self.subTest(active=active, enabled=enabled), tempfile.TemporaryDirectory(
                prefix="production-relay-disable-"
            ) as temporary:
                state_file = Path(temporary) / "production-state" / "relay.json"
                result = run_sourced_script(
                    """
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$2"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=''
RELEASE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FAKE_ACTIVE="$3"
FAKE_ENABLED="$4"
systemctl() {
  local command="$1" unit="${3:-${2:-}}"
  case "$command" in
    cat) return 0 ;;
    is-enabled) [[ "$FAKE_ENABLED" == 1 ]] ;;
    is-active) [[ "$FAKE_ACTIVE" == 1 ]] ;;
    stop) FAKE_ACTIVE=0; return 0 ;;
    disable) FAKE_ENABLED=0; return 0 ;;
    *) return 0 ;;
  esac
}
suspend_production_coin_snapshot_relay
""",
                    str(state_file),
                    str(active),
                    str(enabled),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exact disable confirmation", result.stderr)
                self.assertTrue(state_file.is_file())
                self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
                marker = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertTrue(marker["relay_intentionally_stopped"])
                self.assertEqual(marker["previous_timer_enabled"], bool(enabled))
                self.assertEqual(marker["previous_timer_active"], bool(active))
                self.assertNotIn(str(REPO_ROOT), state_file.read_text(encoding="utf-8"))

    def test_exact_disable_confirmation_closes_marker_without_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-relay-disable-ok-") as temporary:
            root = Path(temporary)
            state_file = root / "production-state" / "relay.json"
            command_log = root / "commands.log"
            result = run_sourced_script(
                """
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$2"
COMMAND_LOG="$3"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=disable-production-coin-inference-snapshot
RELEASE_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
FAKE_ACTIVE=1
FAKE_ENABLED=1
systemctl() {
  local command="$1"
  printf '%s %s\n' "$command" "${*:2}" >>"$COMMAND_LOG"
  case "$command" in
    cat) return 0 ;;
    is-enabled) [[ "$FAKE_ENABLED" == 1 ]] ;;
    is-active) [[ "$FAKE_ACTIVE" == 1 ]] ;;
    stop) FAKE_ACTIVE=0; return 0 ;;
    disable) FAKE_ENABLED=0; return 0 ;;
    *) return 0 ;;
  esac
}
suspend_production_coin_snapshot_relay
reconcile_production_coin_snapshot_relay
""",
                str(state_file),
                str(command_log),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertFalse(state_file.exists())
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn("disable coin-intelligence-production-snapshot-relay.timer", commands)
            self.assertNotIn("start coin-intelligence-production-snapshot-relay.timer", commands)
            self.assertNotIn("restart coin-intelligence-production-snapshot-relay.timer", commands)

    def test_interrupted_release_restores_exact_relay_state_and_clears_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-relay-interrupted-") as temporary:
            root = Path(temporary)
            state_file = root / "production-state" / "relay.json"
            command_log = root / "systemctl.log"
            result = run_sourced_script(
                """
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$2"
COMMAND_LOG="$3"
RELEASE_SHA=cccccccccccccccccccccccccccccccccccccccc
PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER_WAS_PRESENT=1
PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_PRESENT=1
PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ENABLED=1
PRODUCTION_COIN_SNAPSHOT_RELAY_WAS_ACTIVE=0
PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE_WAS_ACTIVE=1
PRODUCTION_COIN_SNAPSHOT_RELAY_GUARD_ARMED=1
write_production_coin_relay_recovery_marker
FAKE_TIMER_ENABLED=0
FAKE_TIMER_ACTIVE=0
FAKE_SERVICE_ACTIVE=0
systemctl() {
  local command="$1" unit="${*: -1}"
  printf '%s %s\n' "$command" "$unit" >>"$COMMAND_LOG"
  case "$command" in
    cat) return 0 ;;
    stop)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" ]] && FAKE_TIMER_ACTIVE=0
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" ]] && FAKE_SERVICE_ACTIVE=0
      return 0
      ;;
    enable) FAKE_TIMER_ENABLED=1; return 0 ;;
    disable) FAKE_TIMER_ENABLED=0; return 0 ;;
    start)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" ]] && FAKE_TIMER_ACTIVE=1
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" ]] && FAKE_SERVICE_ACTIVE=1
      return 0
      ;;
    is-enabled) [[ "$FAKE_TIMER_ENABLED" == 1 ]] ;;
    is-active)
      if [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_TIMER" ]]; then
        [[ "$FAKE_TIMER_ACTIVE" == 1 ]] || return 3
      else
        [[ "$FAKE_SERVICE_ACTIVE" == 1 ]] || return 3
      fi
      ;;
    *) return 2 ;;
  esac
}
set +e
false
production_release_relay_exit_guard
""",
                str(state_file),
                str(command_log),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exact_prior_state_restored", result.stderr)
            self.assertFalse(state_file.exists())
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn(
                "enable coin-intelligence-production-snapshot-relay.timer",
                commands,
            )
            self.assertNotIn(
                "start coin-intelligence-production-snapshot-relay.timer",
                commands,
            )
            self.assertIn(
                "start coin-intelligence-production-snapshot-relay.service",
                commands,
            )

    def test_service_only_relay_state_is_captured_and_restored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-relay-service-only-") as temporary:
            root = Path(temporary)
            state_file = root / "production-state" / "relay.json"
            command_log = root / "systemctl.log"
            result = run_sourced_script(
                """
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$2"
COMMAND_LOG="$3"
RELEASE_SHA=dddddddddddddddddddddddddddddddddddddddd
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=disable-production-coin-inference-snapshot
FAKE_SERVICE_ACTIVE=1
systemctl() {
  local command="$1" unit="${*: -1}"
  printf '%s %s\n' "$command" "$unit" >>"$COMMAND_LOG"
  case "$command" in
    cat)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" ]]
      ;;
    is-enabled) return 1 ;;
    is-active)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" \
          && "$FAKE_SERVICE_ACTIVE" == 1 ]] || return 3
      ;;
    stop)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" ]] \
        && FAKE_SERVICE_ACTIVE=0
      return 0
      ;;
    start)
      [[ "$unit" == "$PRODUCTION_COIN_SNAPSHOT_RELAY_SERVICE" ]] \
        && FAKE_SERVICE_ACTIVE=1
      return 0
      ;;
    disable|enable) return 0 ;;
    *) return 2 ;;
  esac
}
suspend_production_coin_snapshot_relay
python3 - "$PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    f"{int(payload['previous_timer_unit_present'])}|"
    f"{int(payload['previous_service_unit_present'])}|"
    f"{int(payload['previous_service_active'])}"
)
PY
restore_production_coin_snapshot_relay_recovery_state
""",
                str(state_file),
                str(command_log),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("0|1|1", result.stdout)
            self.assertFalse(state_file.exists())
            self.assertIn(
                "start coin-intelligence-production-snapshot-relay.service",
                command_log.read_text(encoding="utf-8"),
            )

    def test_private_primary_legacy_input_retirement_requires_exact_systemd_states(self) -> None:
        exact = run_sourced_script(
            """
PRODUCTION_PRIVATE_PRIMARY_PRODUCT_REQUIRED=1
RELEASE_SHA=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
read_production_coin_input_timer_recovery_state() {
  printf '%s\n' \
    "release${TAB}$RELEASE_SHA" \
    "unit${TAB}coin-group-event-telegram.service${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}coin-group-event-telegram.timer${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}trading-bot-private-gold-collector.service${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}trading-bot-private-gold-collector.timer${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')"
}
systemctl() {
  case "$1" in
    stop|disable) return 0 ;;
    is-active) return 3 ;;
    is-enabled) return 1 ;;
    *) return 2 ;;
  esac
}
retire_production_legacy_coin_inputs
""".replace("${TAB}", "\t")
        )
        self.assertEqual(exact.returncode, 0, exact.stderr + exact.stdout)

        unavailable = run_sourced_script(
            """
PRODUCTION_PRIVATE_PRIMARY_PRODUCT_REQUIRED=1
RELEASE_SHA=ffffffffffffffffffffffffffffffffffffffff
read_production_coin_input_timer_recovery_state() {
  printf '%s\n' \
    "release${TAB}$RELEASE_SHA" \
    "unit${TAB}coin-group-event-telegram.service${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}coin-group-event-telegram.timer${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}trading-bot-private-gold-collector.service${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')" \
    "unit${TAB}trading-bot-private-gold-collector.timer${TAB}1${TAB}$(printf x | sha256sum | awk '{print $1}')"
}
systemctl() {
  case "$1" in
    stop|disable) return 0 ;;
    is-active) return 4 ;;
    is-enabled) return 4 ;;
    *) return 2 ;;
  esac
}
retire_production_legacy_coin_inputs
""".replace("${TAB}", "\t")
        )
        self.assertNotEqual(unavailable.returncode, 0)
        self.assertIn("state is unavailable", unavailable.stderr)

    def test_two_host_release_marker_binds_exact_code_and_env_pair_until_health(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-two-host-release-") as temporary:
            root = Path(temporary)
            state_file = root / "production-state" / "two-host.json"
            result = run_sourced_script(
                """
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE="$2"
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE_CANONICAL="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$3"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$3"
RELEASE_SHA=dddddddddddddddddddddddddddddddddddddddd
PRODUCTION_RELEASE_TREE=abababababababababababababababababababab
PRODUCTION_PRE_RELEASE_SHA=9999999999999999999999999999999999999999
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$(printf source | sha256sum | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(printf foreign | sha256sum | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(printf iran | sha256sum | awk '{print $1}')"
PRODUCTION_RELEASE_EVIDENCE_VERIFIED=1
PRODUCTION_BACKUP_RECEIPT_PATH=/tmp/production-backup-receipt.json
PRODUCTION_BACKUP_RECEIPT_SHA256="$(printf backup | sha256sum | awk '{print $1}')"
PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH=/tmp/production-rehearsal-receipt.json
PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256="$(printf rehearsal | sha256sum | awk '{print $1}')"
PRODUCTION_BACKUP_ARTIFACT_SET_SHA256="$(printf artifacts | sha256sum | awk '{print $1}')"
PRODUCTION_RELEASE_SCHEMA_HEAD=fd3e4f5a6b7c
PRODUCTION_FOREIGN_IMAGE_ID="sha256:$(printf foreign-image | sha256sum | awk '{print $1}')"
PRODUCTION_FOREIGN_IMAGE_RECEIPT_SHA256="$(printf foreign-receipt | sha256sum | awk '{print $1}')"
PRODUCTION_IRAN_IMAGE_ID="sha256:$(printf iran-image | sha256sum | awk '{print $1}')"
PRODUCTION_IRAN_REMOTE_IMAGE_ID="sha256:$(printf iran-remote-image | sha256sum | awk '{print $1}')"
PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256="$(printf iran-receipt | sha256sum | awk '{print $1}')"
PRODUCTION_FOREIGN_TARGET_BINDING_SHA256="$(printf foreign-target | sha256sum | awk '{print $1}')"
PRODUCTION_IRAN_TARGET_BINDING_SHA256="$(printf iran-target | sha256sum | awk '{print $1}')"
PRODUCTION_IRAN_SOURCE_PAYLOAD_MANIFEST_SHA256="$(printf iran-source-payload | sha256sum | awk '{print $1}')"
write_two_host_release_state prepared
PRODUCTION_TWO_HOST_RELEASE_GUARD_ARMED=1
write_two_host_release_state foreign_committed
two_host_release_exit_guard 17
""",
                str(state_file),
                str(root / "production-state" / "relay.json"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("two_host_reconcile_required=true", result.stderr)
            marker = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(marker["phase"], "foreign_committed")
            self.assertEqual(marker["status"], "release_incomplete")
            self.assertEqual(marker["pre_release_sha"], "9" * 40)
            self.assertNotEqual(marker["pre_release_sha"], marker["release_sha"])
            self.assertFalse(marker["secrets_disclosed"])
            serialized = state_file.read_text(encoding="utf-8")
            self.assertNotIn(str(REPO_ROOT), serialized)
            self.assertNotIn("password", serialized.lower())

            mismatch = run_sourced_script(
                """
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE="$2"
PRODUCTION_TWO_HOST_RELEASE_STATE_FILE_CANONICAL="$2"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE="$3"
PRODUCTION_COIN_SNAPSHOT_RELAY_STATE_FILE_CANONICAL="$3"
RELEASE_SHA=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
PRODUCTION_RELEASE_TREE=abababababababababababababababababababab
PRODUCTION_RUNTIME_ENV_SOURCE_SHA256="$(printf source | sha256sum | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_FOREIGN_SHA256="$(printf foreign | sha256sum | awk '{print $1}')"
PRODUCTION_RUNTIME_ENV_IRAN_SHA256="$(printf iran | sha256sum | awk '{print $1}')"
PRODUCTION_BACKUP_RECEIPT_PATH=/tmp/production-backup-receipt.json
PRODUCTION_BACKUP_RECEIPT_SHA256="$(printf backup | sha256sum | awk '{print $1}')"
PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH=/tmp/production-rehearsal-receipt.json
PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256="$(printf rehearsal | sha256sum | awk '{print $1}')"
load_two_host_release_state
""",
                str(state_file),
                str(root / "production-state" / "relay.json"),
            )
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("different code or runtime env bytes", mismatch.stderr)

        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(release.index("begin_two_host_release_transaction"), release.index("deploy_foreign"))
        self.assertLess(release.index("write_two_host_release_state foreign_committed"), release.index("sync_project"))
        self.assertLess(release.index("write_two_host_release_state iran_payload_installed"), release.index("deploy_iran"))
        self.assertLess(release.index("write_two_host_release_state iran_committed"), release.index("healthcheck"))
        self.assertLess(release.index("healthcheck"), release.index("clear_two_host_release_state"))

    def test_two_host_writers_are_quiesced_through_both_migrations_and_schema_gate(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        ordered = (
            "quiesce_two_host_writers_for_migration",
            "deploy_foreign 1",
            "deploy_iran 1",
            "verify_two_host_schema_head",
            "start_two_host_writers_after_schema_convergence",
            "repair_registry_fingerprint_rollout_quarantine",
            "healthcheck",
        )
        positions = [release.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))

        quiesce = source.split(
            "quiesce_two_host_writers_for_migration() {", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("disable_and_stop_current_foreign_writers", quiesce)
        self.assertIn("disable_and_stop_current_iran_writers", quiesce)
        self.assertIn("restart-disabled and quiesced", quiesce)
        self.assertNotIn("stop -t 30 db", quiesce)
        self.assertNotIn("stop -t 30 redis", quiesce)

        schema_gate = source.split("verify_two_host_schema_head() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("python -m alembic heads", schema_gate)
        self.assertIn("SELECT version_num FROM alembic_version", schema_gate)
        self.assertIn("PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED=1", schema_gate)

        starter = source.split(
            "start_two_host_writers_after_schema_convergence() {", 1
        )[1].split("\n}", 1)[0]
        self.assertIn('PRODUCTION_TWO_HOST_WRITERS_QUIESCED" == "1', starter)
        self.assertIn('PRODUCTION_TWO_HOST_SCHEMAS_VERIFIED" == "1', starter)
        self.assertIn("both writer planes were returned to restart-disabled stopped state", starter)

        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        foreign = legacy.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            foreign.index("Foreign database migration"),
            foreign.index("PRODUCTION_DEFER_FOREIGN_WRITER_START"),
        )
        self.assertIn("startup is deferred until the official two-host schema gate passes", foreign)

    def test_all_release_artifact_verification_and_iran_image_load_finish_before_writer_quiescence(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        quiesce = release.index("quiesce_two_host_writers_for_migration")
        self.assertLess(release.index("load_two_host_release_state"), release.index("verify_prepared_release_artifacts"))
        for gate in (
            "verify_prepared_release_artifacts",
            "bootstrap_iran",
            "configure_nginx",
            "issue_cert",
            "ship_images",
            "load_images",
            "verify_release_evidence_gate",
        ):
            self.assertLess(release.index(gate), quiesce, gate)
        self.assertLess(quiesce, release.index("deploy_foreign 1"))

        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        foreign = legacy.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            foreign.index("PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE"),
            foreign.index("Foreign stateful dependencies startup"),
        )
        self.assertLess(
            foreign.index("PRODUCTION_PREBUILD_ONLY"),
            foreign.index("Foreign stateful dependencies startup"),
        )
        self.assertIn("no post-quiesce build is allowed", foreign)

        deploy = source.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            'PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE="$defer_writer_start"',
            deploy,
        )

    def test_failed_two_host_migration_keeps_writer_planes_explicitly_stopped(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        guard = source.split("two_host_release_exit_guard() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('PRODUCTION_TWO_HOST_WRITERS_QUIESCED" == "1', guard)
        self.assertIn("foreign_and_iran_intentionally_stopped", guard)
        self.assertIn("old_code_restart=forbidden", guard)

    def test_writer_restart_policies_are_journaled_disabled_and_restored_only_after_schema_gate(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'PRODUCTION_WRITER_QUIESCE_STATE_FILE="/var/lib/trading-bot/production-release/writer-quiesce-state.json"',
            source,
        )
        quiesce = source.split("quiesce_two_host_writers_for_migration() {", 1)[1].split("\n}", 1)[0]
        ordered = (
            "capture_writer_quiesce_state",
            "disable_and_stop_current_foreign_writers",
            "disable_and_stop_current_iran_writers",
            "mark_writer_quiesce_complete",
            "PRODUCTION_TWO_HOST_WRITERS_QUIESCED=1",
        )
        positions = [quiesce.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        local_disable = source.split("disable_and_stop_current_foreign_writers() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(local_disable.index("docker update --restart=no"), local_disable.index("docker stop -t 30"))
        self.assertIn("container_id", source)
        self.assertIn("restart_policy", source)
        self.assertIn('"status": "quiesce_prepared"', source)
        self.assertIn('update_writer_journal_phase writers_quiesced', source)

        start = source.split("start_two_host_writers_after_schema_convergence() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(start.index("prepare_restart_disabled_foreign_writers"), start.index("Starting restart-disabled foreign writers"))
        self.assertLess(start.index("prepare_restart_disabled_iran_writers"), start.index("Starting restart-disabled foreign writers"))
        self.assertIn("start_prepared_iran_writers", start)
        self.assertIn("emergency_disable_all_foreign_writers", start)
        self.assertIn("emergency_disable_all_iran_writers", start)
        self.assertNotIn("restore_current_foreign_writer_policies", start)

        finalizer = source.split("finalize_two_host_writer_restart_policies() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("restore_current_foreign_writer_policies", finalizer)
        self.assertIn("restore_current_iran_writer_policies", finalizer)
        self.assertLess(finalizer.index("restore_current_iran_writer_policies"), finalizer.index("clear_writer_quiesce_state"))
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(release.index("healthcheck"), release.index("finalize_two_host_writer_restart_policies"))
        exit_guard = source.split("two_host_release_exit_guard() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("PRODUCTION_TWO_HOST_WRITER_RESTART_GUARD_ARMED", exit_guard)
        self.assertIn("emergency_disable_all_foreign_writers", exit_guard)

    def test_production_images_are_bound_to_release_tree_signature_and_exact_id(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        for marker in (
            "org.opencontainers.image.revision",
            "io.gold-trade.release.tree",
            "io.gold-trade.release.input-signature",
        ):
            self.assertIn(marker, source)
            self.assertIn(marker, legacy)
        self.assertIn("foreign-image-prebuild-receipt.json", source)
        self.assertIn("PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID", legacy)
        self.assertIn("PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE", legacy)
        foreign = legacy.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn(r'Labels \"', foreign)
        self.assertIn(
            "{{index .Config.Labels \"org.opencontainers.image.revision\"}}",
            foreign,
        )
        self.assertIn("verify_remote_iran_image_identity", source)
        self.assertIn("iran-image-prebuild-receipt.json", source)
        self.assertIn("PRODUCTION_IRAN_IMAGE_RECEIPT_SHA256", source)
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("verify_prepared_release_artifacts", release)
        self.assertLess(release.index("verify_foreign_image_build_receipt"), release.index("quiesce_two_host_writers_for_migration"))
        self.assertLess(release.index("verify_release_evidence_gate"), release.index("quiesce_two_host_writers_for_migration"))
        iran_deploy = source.split("deploy_iran() {", 1)[1].split("\n}", 1)[0]
        remote_migration = iran_deploy.index("run --rm --no-deps migration")
        self.assertLess(iran_deploy.index("verify_iran_image_build_receipt"), remote_migration)
        self.assertLess(iran_deploy.index("verify_remote_iran_image_identity"), remote_migration)

    def test_iran_runtime_image_identity_is_portable_across_docker_stores(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        verifier = source.split("verify_remote_iran_image_identity() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("PRODUCTION_IRAN_IMAGE_ID", verifier)
        self.assertIn("PRODUCTION_IRAN_REMOTE_IMAGE_ID", verifier)
        self.assertIn("{{json .Config}}", verifier)
        self.assertIn("{{json .RootFS}}", verifier)
        self.assertIn('remote_portable_sha\" == \"$local_portable_sha', verifier)

        reconciler = source.split("reconcile_unjournaled_writer_replacements() {", 1)[
            1
        ].split("\n}", 1)[0]
        preparer = source.split("prepare_restart_disabled_iran_writers() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("PRODUCTION_IRAN_REMOTE_IMAGE_ID", reconciler)
        self.assertIn("PRODUCTION_IRAN_REMOTE_IMAGE_ID", preparer)

        loader = source.split("load_images() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("remote_loaded_binding", loader)
        self.assertIn(
            'remote_loaded_binding" == "$RELEASE_SHA|$PRODUCTION_RELEASE_TREE|$image_signature',
            loader,
        )
        self.assertLess(
            loader.index("remote_loaded_binding"),
            loader.index("skipping docker load"),
        )

    def test_release_evidence_is_durable_and_fresh_only_before_initial_quiesce(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        gate = source.split("verify_release_evidence_gate() {", 1)[1].split("\nverify_runtime_env_pair_lock() {", 1)[0]
        marker = source.split("write_two_host_release_state() {", 1)[1].split("\n}", 1)[0]
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        for binding in (
            "backup_receipt_sha256",
            "migration_rehearsal_receipt_sha256",
            "backup_artifact_set_sha256",
            "release_schema_head",
            "foreign_image_id",
            "iran_image_id",
            "foreign_target_binding_sha256",
            "iran_target_binding_sha256",
            "iran_source_payload_manifest_sha256",
        ):
            self.assertIn(binding, marker)
        self.assertIn("verify_backup_receipt", gate)
        self.assertIn("cleanup_status", gate)
        self.assertIn("all_row_counts_preserved", gate)
        self.assertIn("if resume:", gate)
        self.assertIn("backup_now = _parse_utc", gate)
        self.assertIn("if not resume:", gate)
        self.assertIn("migration_contract(backup.pre_migration_head, source_head)", gate)
        self.assertIn('row.get("first_upgrade_noop") is not contract.require_first_upgrade_noop', gate)
        self.assertIn("contract.expected_public_table_delta", gate)
        self.assertIn("contract.expected_added_tables", gate)
        self.assertIn("pre_release_sha", gate)
        self.assertIn("Live foreign/Iran writers", gate)
        self.assertLess(release.index("verify_release_evidence_gate"), release.index("begin_two_host_release_transaction"))
        self.assertLess(release.index("begin_two_host_release_transaction"), release.index("quiesce_two_host_writers_for_migration"))

    def test_release_artifacts_have_a_non_service_preparation_command_and_are_reused(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        prepare = source.split("prepare_release_evidence_artifacts() {", 1)[1].split("\n}", 1)[0]
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        authority = source.split("verify_queue_cutover_deploy_authority() {", 1)[1].split("\n}", 1)[0]
        for token in (
            "build_release",
            "write_iran_image_build_receipt",
            "prebuild_foreign_release_image",
            "verify_prepared_release_artifacts",
        ):
            self.assertIn(token, prepare)
        self.assertNotIn("deploy_foreign 1", prepare)
        self.assertNotIn("deploy_iran 1", prepare)
        self.assertNotIn("quiesce_two_host_writers_for_migration", prepare)
        self.assertIn("verify_prepared_release_artifacts", release)
        self.assertNotIn("build_release", release)
        self.assertNotIn("prebuild_foreign_release_image", release)
        self.assertIn("PRODUCTION_QUEUE_CUTOVER_REBUILD_EVIDENCE=1", authority)
        self.assertIn(
            'if [[ "$PRODUCTION_QUEUE_CUTOVER_REBUILD_EVIDENCE" == "1" ]]',
            release,
        )
        self.assertLess(
            release.index("prepare_release_evidence_artifacts"),
            release.index("prepare_committed_iran_source_payload"),
        )
        help_text = subprocess.run(
            ["bash", str(RELEASE_SCRIPT), "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(help_text.returncode, 0, help_text.stderr + help_text.stdout)
        self.assertIn("prepare-release-evidence", help_text.stdout)
        self.assertIn("verify-release-evidence", help_text.stdout)
        self.assertIn("prepare-private-primary-control-release", help_text.stdout)

    def test_writer_replacements_are_created_restart_disabled_and_ids_are_journaled(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        foreign = source.split("prepare_restart_disabled_foreign_writers() {", 1)[1].split("\n}", 1)[0]
        iran = source.split("prepare_restart_disabled_iran_writers() {", 1)[1].split("\n}", 1)[0]
        override = source.split("write_writer_restart_disabled_override() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('restart: "no"', override)
        for body in (foreign, iran):
            self.assertLess(body.index("write_writer_restart_disabled_override"), body.index("up --no-start --force-recreate --no-deps"))
            self.assertLess(body.index("update_writer_journal_phase"), body.index("up --no-start --force-recreate --no-deps"))
            self.assertIn("record_writer_replacement_inventory", body)
            self.assertNotIn("docker update --restart=no", body)
            self.assertNotIn("create --force-recreate --no-deps", body)
        self.assertIn("foreign_replacement_creating", foreign)
        self.assertIn("foreign_replacement_prepared", foreign)
        self.assertIn("iran_replacement_creating", iran)
        self.assertIn("replacements_prepared", iran)
        self.assertIn("current_container_id", source)
        self.assertIn("reconcile_unjournaled_writer_replacements", source)

        with tempfile.TemporaryDirectory(prefix="writer-journal-v2-") as temporary:
            root = Path(temporary)
            state = root / "state.json"
            inventory = root / "foreign.tsv"
            old = "a" * 64
            new_ids = {"app": "b" * 64, "bot": "c" * 64, "sync_worker": "d" * 64}
            state.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "foreign_replacement_creating",
                        "release_sha": "e" * 40,
                        "source_sha256": "f" * 64,
                        "writers": [
                            {
                                "role": role,
                                "service": service,
                                "initial_container_id": old,
                                "current_container_id": old,
                                "restart_policy": "always",
                            }
                            for role, services in (("foreign", ("app", "bot", "sync_worker")), ("iran", ("app", "sync_worker")))
                            for service in services
                        ],
                        "recovery_action": "rerun_exact_same_release_do_not_restart_old_code",
                        "secrets_disclosed": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state.chmod(0o600)
            inventory.write_text("".join(f"{service}\t{container_id}\n" for service, container_id in new_ids.items()), encoding="utf-8")
            result = run_sourced_script(
                '''
PRODUCTION_WRITER_QUIESCE_STATE_FILE="$2"
record_writer_replacement_inventory foreign foreign_replacement_creating foreign_replacement_prepared "$3"
''',
                str(state),
                str(inventory),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "foreign_replacement_prepared")
            for row in payload["writers"]:
                if row["role"] == "foreign":
                    self.assertEqual(row["current_container_id"], new_ids[row["service"]])
                else:
                    self.assertEqual(row["current_container_id"], old)

    def test_legacy_deploy_blocks_production_without_one_time_official_authority(self) -> None:
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("consume_official_production_deploy_authority", legacy)
        self.assertIn("deploy-sh-authority.json", legacy)
        self.assertIn("Official production deploy requires a clean immutable checkout", legacy)
        self.assertIn("verify_official_source_still_frozen", legacy)
        self.assertIn('rev-parse", "@{u}"', legacy)
        self.assertIn("Foreign image/runtime inputs drifted before Compose startup", legacy)
        self.assertNotIn("StrictHostKeyChecking=no", legacy)
        environment = os.environ.copy()
        environment.update(
            {
                "IRAN_HOST": "65.109.220.59",
                "IRAN_PROJECT_DIR": "/srv/trading-bot/current",
                "IRAN_USER": "root",
                "IRAN_SSH_PORT": "37067",
            }
        )
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "deploy.sh"), "foreign"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Official production deploy authority is missing", result.stderr + result.stdout)

    def test_legacy_deploy_cannot_hide_canonical_production_with_env_pollution(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "COMPOSE_PROJECT_NAME": "harmless_preview",
                "FOREIGN_COMPOSE_PROJECT_NAME": "harmless_preview",
                "IRAN_HOST": "example.invalid",
                "IRAN_PROJECT_DIR": "/srv/example/preview",
                "IRAN_USER": "preview",
                "IRAN_SSH_PORT": "2222",
            }
        )
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "deploy.sh"), "foreign"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Official production deploy authority is missing", result.stderr + result.stdout)
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("PRODUCTION_CANONICAL_CHECKOUT", legacy)
        self.assertIn("container_name:", legacy)

    def test_resource_guard_terminates_the_entire_isolated_process_group(self) -> None:
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn('setsid --wait "$@"', legacy)
        self.assertIn('kill -TERM -- "-$process_group"', legacy)
        self.assertIn('kill -KILL -- "-$process_group"', legacy)
        functions = []
        for name in (
            "resource_guard_enabled",
            "sample_cpu_usage",
            "sample_memory_usage",
            "guarded_process_is_live",
            "guarded_process_group_has_live_members",
            "wait_for_guarded_process_stop",
            "terminate_guarded_process",
            "run_with_local_resource_guard",
        ):
            functions.append(legacy.split(f"{name}() {{", 1)[1].split("\n}", 1)[0])
        script = "print_header() { :; }\n"
        for name, body in zip(
            (
                "resource_guard_enabled",
                "sample_cpu_usage",
                "sample_memory_usage",
                "guarded_process_is_live",
                "guarded_process_group_has_live_members",
                "wait_for_guarded_process_stop",
                "terminate_guarded_process",
                "run_with_local_resource_guard",
            ),
            functions,
            strict=True,
        ):
            script += f"{name}() {{{body}\n}}\n"
        script += r'''
DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS=1
DEPLOY_RESOURCE_GUARD_MAX_MEM_PERCENT=0
DEPLOY_RESOURCE_GUARD_MAX_STREAK=1
DEPLOY_RESOURCE_GUARD_TERMINATION_GRACE_SECONDS=0
run_with_local_resource_guard test bash -c 'trap "" TERM; sleep 30 & child=$!; printf "%s\n" "$child" > "$CHILD_PID_FILE"; wait' || status=$?
test "${status:-0}" -eq 124
child="$(cat "$CHILD_PID_FILE")"
for attempt in 1 2 3 4 5; do
  kill -0 "$child" 2>/dev/null || exit 0
  sleep 0.1
done
exit 9
'''
        with tempfile.TemporaryDirectory(prefix="resource-guard-group-") as temporary:
            child_file = Path(temporary) / "child.pid"
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env={**os.environ, "CHILD_PID_FILE": str(child_file)},
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_resource_guard_wall_deadline_kills_term_resistant_descendant(self) -> None:
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        function_names = (
            "resource_guard_enabled",
            "sample_cpu_usage",
            "sample_memory_usage",
            "guarded_process_is_live",
            "guarded_process_group_has_live_members",
            "wait_for_guarded_process_stop",
            "terminate_guarded_process",
            "run_with_local_resource_guard",
        )
        functions = [
            legacy.split(f"{name}() {{", 1)[1].split("\n}", 1)[0]
            for name in function_names
        ]
        script = "print_header() { :; }\n"
        for name, body in zip(function_names, functions, strict=True):
            script += f"{name}() {{{body}\n}}\n"
        script += r'''
DEPLOY_RESOURCE_GUARD_ENABLED=0
DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS=1
DEPLOY_RESOURCE_GUARD_MAX_SECONDS=1
DEPLOY_RESOURCE_GUARD_TERMINATION_GRACE_SECONDS=0
DEPLOY_RESOURCE_GUARD_KILL_VERIFY_SECONDS=2
run_with_local_resource_guard deadline-probe bash -c '
  trap "" TERM
  (trap "" TERM; sleep 2; printf survived > "$DESCENDANT_MARKER") &
  printf "%s\n" "$!" > "$CHILD_PID_FILE"
  wait
' || status=$?
test "${status:-0}" -eq 124
sleep 1.2
test ! -e "$DESCENDANT_MARKER"
'''
        with tempfile.TemporaryDirectory(prefix="resource-guard-deadline-") as temporary:
            child_file = Path(temporary) / "child.pid"
            marker = Path(temporary) / "descendant-finished"
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "CHILD_PID_FILE": str(child_file),
                    "DESCENDANT_MARKER": str(marker),
                },
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Wall-clock deadline reached", result.stdout)

    def test_resource_guard_rejects_successful_leader_with_live_descendant(self) -> None:
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        function_names = (
            "resource_guard_enabled",
            "sample_cpu_usage",
            "sample_memory_usage",
            "guarded_process_is_live",
            "guarded_process_group_has_live_members",
            "wait_for_guarded_process_stop",
            "terminate_guarded_process",
            "run_with_local_resource_guard",
        )
        functions = [
            legacy.split(f"{name}() {{", 1)[1].split("\n}", 1)[0]
            for name in function_names
        ]
        script = "print_header() { :; }\n"
        for name, body in zip(function_names, functions, strict=True):
            script += f"{name}() {{{body}\n}}\n"
        script += r'''
DEPLOY_RESOURCE_GUARD_ENABLED=0
DEPLOY_RESOURCE_GUARD_SAMPLE_SECONDS=1
DEPLOY_RESOURCE_GUARD_MAX_SECONDS=5
DEPLOY_RESOURCE_GUARD_TERMINATION_GRACE_SECONDS=0
DEPLOY_RESOURCE_GUARD_KILL_VERIFY_SECONDS=2
run_with_local_resource_guard normal-return-probe bash -c '
  (trap "" TERM; exec >/dev/null 2>&1; sleep 2; printf survived > "$DESCENDANT_MARKER") &
  exit 0
' || status=$?
test "${status:-0}" -eq 125
sleep 2.1
test ! -e "$DESCENDANT_MARKER"
'''
        with tempfile.TemporaryDirectory(prefix="resource-guard-normal-return-") as temporary:
            marker = Path(temporary) / "descendant-finished"
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=REPO_ROOT,
                env={**os.environ, "DESCENDANT_MARKER": str(marker)},
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("live process-group members remained", result.stdout)

    def test_foreign_migration_has_an_explicit_conservative_wall_deadline(self) -> None:
        legacy = (REPO_ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn(
            'PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS="${PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS:-1800}"',
            legacy,
        )
        foreign_deploy = legacy.split("deploy_foreign() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            'DEPLOY_RESOURCE_GUARD_MAX_SECONDS="$PRODUCTION_FOREIGN_MIGRATION_TIMEOUT_SECONDS"',
            foreign_deploy,
        )
        self.assertLess(
            foreign_deploy.index("Foreign database migration"),
            foreign_deploy.index("Foreign writer startup is deferred"),
        )

    def test_iran_otp_secret_is_required_only_in_rendered_iran_when_telegram_otp_is_on(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-otp-projection-") as temporary:
            root = Path(temporary)
            foreign = root / "foreign.env"
            iran = root / "iran.env"
            foreign.write_text("OTP_DELIVERY_STATE_SECRET=\n", encoding="utf-8")
            iran.write_text(
                "TELEGRAM_LOGIN_OTP_ENABLED=true\nOTP_DELIVERY_STATE_SECRET=\n",
                encoding="utf-8",
            )
            missing = run_sourced_script(
                """
FOREIGN_RUNTIME_ENV_PATH="$2"
IRAN_RUNTIME_ENV_PATH="$3"
validate_iran_otp_delivery_secret_projection
""",
                str(foreign),
                str(iran),
            )
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("missing or too short", missing.stderr)
            secret = "s" * 40
            iran.write_text(
                f"TELEGRAM_LOGIN_OTP_ENABLED=true\nOTP_DELIVERY_STATE_SECRET={secret}\n",
                encoding="utf-8",
            )
            accepted = run_sourced_script(
                """
FOREIGN_RUNTIME_ENV_PATH="$2"
IRAN_RUNTIME_ENV_PATH="$3"
validate_iran_otp_delivery_secret_projection
""",
                str(foreign),
                str(iran),
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr + accepted.stdout)
            self.assertNotIn(secret, accepted.stderr + accepted.stdout)

    def test_live_inference_flags_require_confirmed_relay_and_exact_collector_bindings(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-inference-contract-") as temporary:
            source = Path(temporary) / "source.env"
            source.write_text(
                "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED=true\n"
                "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED=true\n"
                "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false\n"
                "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED=true\n",
                encoding="utf-8",
            )
            blocked = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=''
COIN_GROUP_EVENT_CHANNEL_ID=-1001111111111
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID=-1002222222222
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID=-1003333333333
COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID=12345
validate_production_coin_inference_activation_contract
""",
                str(source),
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("requires the confirmed production Snapshot relay", blocked.stderr)

            ready = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot
COIN_GROUP_EVENT_CHANNEL_ID=-1001111111111
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID=-1002222222222
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID=-1003333333333
COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID=12345
validate_production_coin_inference_activation_contract
printf '%s\n' "$PRODUCTION_COIN_INFERENCE_REQUESTED"
""",
                str(source),
            )
            self.assertEqual(ready.returncode, 0, ready.stderr + ready.stdout)
            self.assertEqual(ready.stdout.strip(), "1")

    def test_private_primary_inference_requires_exact_product_contract_not_legacy_relay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="production-private-primary-contract-") as temporary:
            source = Path(temporary) / "source.env"
            source.write_text(
                "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED=true\n"
                "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED=true\n"
                "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false\n"
                "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED=true\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=120\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR=/srv/trading-bot/production-data/market-pipeline/snapshots\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR=/srv/trading-bot/production-data/market-pipeline/snapshots\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR=/srv/trading-bot/market-data-production/snapshots\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH=/app/runtime/product-estimator/latest-private-primary.json\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_PRIVATE_PRIMARY_SNAPSHOT_PATH=/app/runtime/product-estimator/latest-private-primary.json\n"
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH=/app/runtime/product-estimator/latest-private-primary.json\n",
                encoding="utf-8",
            )
            ready = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=disable-production-coin-inference-snapshot
COIN_GROUP_EVENT_CHANNEL_ID=''
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_OFFER_CHANNEL_ID=''
COIN_INTELLIGENCE_EXPECTED_PRIVATE_GOLD_TRADE_CHANNEL_ID=''
COIN_INTELLIGENCE_EXPECTED_TELEGRAM_API_ID=''
validate_production_coin_inference_activation_contract
printf '%s %s %s %s\n' "$PRODUCTION_COIN_INFERENCE_REQUESTED" "$PRODUCTION_LEGACY_COIN_PIPELINE_REQUIRED" "$PRODUCTION_PRIVATE_PRIMARY_PRODUCT_REQUIRED" "$PRODUCTION_COIN_INFERENCE_RELAY_ENABLED"
""",
                str(source),
            )
            self.assertEqual(ready.returncode, 0, ready.stderr + ready.stdout)
            self.assertEqual(ready.stdout.strip(), "1 0 1 0")

            relay_enabled = run_sourced_script(
                """
RUNTIME_ENV_SOURCE_PATH="$2"
PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1
PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot
PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=
validate_production_coin_inference_activation_contract
""",
                str(source),
            )
            self.assertNotEqual(relay_enabled.returncode, 0)
            self.assertIn("explicit relay-disabled", relay_enabled.stderr)

            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=120",
                    "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS=121",
                ),
                encoding="utf-8",
            )
            blocked = run_sourced_script(
                'RUNTIME_ENV_SOURCE_PATH="$2"; validate_production_coin_inference_activation_contract',
                str(source),
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("maximum age", blocked.stderr)

    def test_release_gates_inputs_snapshot_and_consumers_before_final_health(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            release.index("begin_two_host_release_transaction"),
            release.index("capture_production_coin_input_timer_recovery_state"),
        )
        self.assertLess(
            release.index("capture_production_coin_input_timer_recovery_state"),
            release.index("install_and_verify_production_coin_inputs"),
        )
        self.assertLess(
            release.index("verify_production_coin_snapshot_relay"),
            release.index("start_two_host_writers_after_schema_convergence"),
        )
        self.assertLess(
            release.index("verify_running_production_coin_consumers"),
            release.index("healthcheck"),
        )
        dispatcher = source.split("verify_running_production_coin_consumers() {", 1)[1].split("\n}", 1)[0]
        legacy = source.split("verify_running_legacy_production_coin_consumers() {", 1)[1].split("\n}", 1)[0]
        private = source.split("verify_running_private_primary_consumers() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("verify_running_private_primary_consumers", dispatcher)
        self.assertIn("verify_running_legacy_production_coin_consumers", dispatcher)
        self.assertIn("trading_bot_app", legacy)
        self.assertIn("trading_bot_bot", legacy)
        self.assertIn("--expect-enabled", legacy)
        self.assertIn("trading_bot_app", private)
        self.assertIn("trading_bot_bot", private)
        self.assertIn("private-primary-consumer", private)
        self.assertIn("--expected-sha256", private)

    def test_private_primary_release_retires_legacy_only_after_verified_consumers(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        release = source.split("run_release() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("retire_production_legacy_coin_inputs", source)
        self.assertIn("retire_production_coin_snapshot_relay", source)
        self.assertLess(
            release.index("begin_two_host_release_transaction"),
            release.index("capture_production_coin_input_timer_recovery_state"),
        )
        self.assertLess(
            release.index("capture_production_coin_input_timer_recovery_state"),
            release.index("verify_running_production_coin_consumers"),
        )
        self.assertLess(
            release.index("verify_running_production_coin_consumers"),
            release.index("retire_production_legacy_coin_inputs"),
        )
        self.assertLess(
            release.index("retire_production_legacy_coin_inputs"),
            release.index("retire_production_coin_snapshot_relay"),
        )
        self.assertLess(
            release.index("retire_production_coin_snapshot_relay"),
            release.index("healthcheck"),
        )
        self.assertLess(
            release.index("healthcheck"),
            release.index("clear_production_coin_input_timer_recovery_state"),
        )
        relay_recovery = source.split(
            "write_production_coin_relay_recovery_marker() {", 1
        )[1].split("\n}", 1)[0]
        self.assertIn('"previous_timer_unit_present"', relay_recovery)
        self.assertIn('"previous_service_unit_present"', relay_recovery)
        self.assertIn(
            '"restore_exact_prior_relay_state_on_release_failure"',
            relay_recovery,
        )
        input_recovery = source.split(
            "capture_production_coin_input_timer_recovery_state() {", 1
        )[1].split("\n}", 1)[0]
        self.assertIn('"services"', input_recovery)
        self.assertIn(
            '"restore_prior_units_and_runtime_state_on_release_failure"',
            input_recovery,
        )
        private_contract = source.split(
            'if [[ "$mode" == "PRIVATE_PRIMARY" ]]; then', 1
        )[1].split("else", 1)[0]
        self.assertNotIn("PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=", private_contract)
        self.assertIn('PRODUCTION_COIN_INFERENCE_RELAY_ENABLED" == "0"', private_contract)

    def test_script_can_be_sourced_without_running_a_release(self) -> None:
        result = run_sourced_script('printf "source-only-ok\\n"')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout, "source-only-ok\n")

    def test_backup_key_is_proved_only_by_authenticated_decrypt(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        backup = source.split("prepare_market_pipeline_archive_backup() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertNotIn("local_key_identity", backup)
        self.assertNotIn("remote_key_identity", backup)
        self.assertNotIn("sha256sum '$PRODUCTION_MARKET_PIPELINE_WEB_BACKUP_KEY_PATH'", backup)
        self.assertIn("authenticated decrypt-stream reconciliation", backup)


if __name__ == "__main__":
    unittest.main()
