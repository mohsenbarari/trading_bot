"""Fail-closed contracts for the production coin-inference Snapshot relay."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from scripts.relay_production_coin_inference_snapshot import (
    PRODUCTION_CONFIRMATION,
    ProductionSnapshotRelayError,
    _file_inside,
    _process_group_exists,
    _process_group_has_live_members,
    _run_bounded,
    _relay_remote,
    _root,
    _single_writer_lock,
    _validate_remote_identity_file,
    main,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sandbox_relay_installer(
    root: Path, *, systemd_dir: Path | None = None, backup_root: Path | None = None
) -> Path:
    source = REPO_ROOT / "scripts/install_production_coin_inference_snapshot_relay.sh"
    systemd_dir = systemd_dir or root / "systemd"
    backup_root = backup_root or root / "backups"
    source_lock = root / "secure" / ".production-runtime-source.lock"
    source_lock.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    rendered = source.read_text(encoding="utf-8")
    replacements = {
        'SYSTEMD_DIR="/etc/systemd/system"': f'SYSTEMD_DIR="{systemd_dir}"',
        'BACKUP_ROOT="/var/backups/trading-bot/systemd"': f'BACKUP_ROOT="{backup_root}"',
        'PRODUCTION_OPERATION_LOCK_DIR="/root/secure-envs/trading-bot/queue-cutover-artifacts"': f'PRODUCTION_OPERATION_LOCK_DIR="{root / "operation-locks"}"',
        'PRODUCTION_SOURCE_LOCK_PATH="/root/secure-envs/trading-bot/.production-runtime-source.lock"': f'PRODUCTION_SOURCE_LOCK_PATH="{source_lock}"',
    }
    for before, after in replacements.items():
        assert before in rendered
        rendered = rendered.replace(before, after)
    installer = root / "install-relay.test.sh"
    installer.write_text(rendered, encoding="utf-8")
    installer.chmod(0o755)
    return installer


def _fake_systemd_environment(root: Path, *, analyze_fail_at: int = 0) -> dict[str, str]:
    fake_bin = root / "bin"
    state = root / "systemd-state"
    fake_bin.mkdir(parents=True)
    state.mkdir(parents=True)
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        """#!/usr/bin/env bash
set -u
command="$1"; shift
[[ "${1:-}" == "--quiet" ]] && shift
unit="${1:-}"
printf 'systemctl %s %s\n' "$command" "$unit" >>"$FAKE_SYSTEMD_LOG"
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
printf 'systemd-analyze %s\n' "$count" >>"$FAKE_SYSTEMD_LOG"
[[ "$count" != "${FAKE_ANALYZE_FAIL_AT:-0}" ]]
""",
        encoding="utf-8",
    )
    analyze.chmod(0o755)
    for name in ("ssh", "scp"):
        command = fake_bin / name
        command.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        command.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_STATE": str(state),
        "FAKE_ANALYZE_COUNT": str(root / "analyze-count"),
        "FAKE_ANALYZE_FAIL_AT": str(analyze_fail_at),
        "FAKE_SYSTEMD_LOG": str(root / "systemd.log"),
    }


class ProductionSnapshotRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.source_root = base / "production-source"
        self.runtime_root = base / "production-runtime"
        self.source_root.mkdir()
        self.runtime_root.mkdir()
        self.store_path = self.source_root / "market.sqlite3"
        self.snapshot_path = self.runtime_root / "coin-rates.json"
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.connection = connect_market_store(self.store_path)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def _seed_store(self) -> None:
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=derive_event_key("production-relay-test", "physical-gold"),
                source_code="PRIVATE_GOLD_CHANNEL",
                source_family="TELEGRAM_PRIVATE",
                event_time_utc=self.now - timedelta(seconds=5),
                available_at_utc=self.now - timedelta(seconds=5),
                instrument="MELTED_GOLD_PRIVATE",
                market_label="PRIVATE_GOLD_PHYSICAL",
                settlement_term="TODAY",
                trade_form="PHYSICAL",
                event_type="QUOTE",
                side="MID",
                price=80_300_000,
                price_unit="TOMAN_PER_MESGHAL_750",
                currency="TOMAN",
                parse_confidence=1.0,
                parser_version="production-relay-test-v1",
                quality_state="ELIGIBLE",
                quality_policy_version="production-relay-test-v1",
            ),
        )
        self.connection.commit()

    def _invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(arguments)
        return result, json.loads(stream.getvalue())

    def _publish_arguments(self) -> list[str]:
        return [
            "publish-relay",
            "--environment",
            "production",
            "--production-confirmation",
            PRODUCTION_CONFIRMATION,
            "--source-root",
            str(self.source_root),
            "--market-store",
            str(self.store_path),
            "--runtime-root",
            str(self.runtime_root),
            "--snapshot",
            str(self.snapshot_path),
        ]

    def test_confirmation_is_required_before_any_write(self) -> None:
        self._seed_store()
        arguments = self._publish_arguments()
        arguments[arguments.index(PRODUCTION_CONFIRMATION)] = "not-approved"
        result, payload = self._invoke(*arguments)
        self.assertEqual((result, payload), (2, {"reason": "production_confirmation_required", "status": "FAILED"}))
        self.assertFalse(self.snapshot_path.exists())

    def test_paths_are_production_scoped_and_never_staging(self) -> None:
        root = _root(str(self.runtime_root), field="runtime")
        self.assertEqual(_file_inside(root, str(self.snapshot_path), field="snapshot"), self.snapshot_path)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "root_contains_staging"):
            _root(str(self.runtime_root / "staging"), field="runtime", must_exist=False)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "outside_root"):
            _file_inside(root, str(self.source_root / "other.json"), field="snapshot")

    def test_publish_reads_store_without_mutating_it_and_is_atomic(self) -> None:
        self._seed_store()
        before = self.store_path.read_bytes()
        result, payload = self._invoke(*self._publish_arguments())
        self.assertEqual((result, payload["status"], payload["remote_relayed"]), (0, "PUBLISHED", False))
        self.assertEqual(self.store_path.read_bytes(), before)
        self.assertEqual(len(str(payload["snapshot_sha256"])), 64)
        self.assertEqual(self.snapshot_path.stat().st_mode & 0o777, 0o644)
        self.assertFalse(list(self.runtime_root.glob("*.tmp")))

    def test_remote_failure_preserves_prior_local_final_snapshot(self) -> None:
        self._seed_store()
        result, _payload = self._invoke(*self._publish_arguments())
        self.assertEqual(result, 0)
        original = self.snapshot_path.read_bytes()
        identity = Path(self.temporary.name) / "production-relay-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o600)
        remote_arguments = self._publish_arguments() + [
            "--remote-host",
            "root@production-host.example",
            "--remote-port",
            "2200",
            "--remote-runtime-root",
            "/srv/trading-bot/production-runtime",
            "--remote-snapshot",
            "/srv/trading-bot/production-runtime/coin-rates.json",
            "--remote-project-dir",
            "/srv/trading-bot/production-current",
            "--remote-identity-file",
            str(identity),
        ]
        with (
            patch(
                "scripts.relay_production_coin_inference_snapshot._remote_snapshot_digest",
                return_value=sha256(original).hexdigest(),
            ),
            patch(
                "scripts.relay_production_coin_inference_snapshot._relay_remote",
                side_effect=ProductionSnapshotRelayError("synthetic_remote_failure"),
            ),
        ):
            result, payload = self._invoke(*remote_arguments)
        self.assertEqual((result, payload["reason"]), (2, "synthetic_remote_failure"))
        self.assertEqual(self.snapshot_path.read_bytes(), original)
        self.assertFalse(list(self.runtime_root.glob("*.tmp")))

    def test_local_promotion_failure_rolls_back_and_never_contacts_remote(self) -> None:
        self._seed_store()
        result, _payload = self._invoke(*self._publish_arguments())
        self.assertEqual(result, 0)
        original = self.snapshot_path.read_bytes()
        identity = Path(self.temporary.name) / "production-relay-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o600)
        remote_arguments = self._publish_arguments() + [
            "--remote-host", "root@production-host.example",
            "--remote-port", "2200",
            "--remote-runtime-root", "/srv/trading-bot/production-runtime",
            "--remote-snapshot", "/srv/trading-bot/production-runtime/coin-rates.json",
            "--remote-project-dir", "/srv/trading-bot/production-current",
            "--remote-identity-file", str(identity),
        ]
        with (
            patch(
                "scripts.relay_production_coin_inference_snapshot._remote_snapshot_digest",
                return_value=sha256(original).hexdigest(),
            ),
            patch(
                "scripts.relay_production_coin_inference_snapshot._atomic_promote",
                side_effect=OSError("synthetic local promotion failure"),
            ),
            patch("scripts.relay_production_coin_inference_snapshot._relay_remote") as remote,
        ):
            result, payload = self._invoke(*remote_arguments)
        self.assertEqual((result, payload["reason"]), (2, "OSError"))
        remote.assert_not_called()
        self.assertEqual(self.snapshot_path.read_bytes(), original)
        self.assertFalse((self.runtime_root / ".production-snapshot-relay-transaction.json").exists())
        self.assertFalse(list(self.runtime_root.glob("*.rollback-*.bak")))

    def test_digest_mismatch_and_staleness_preserve_existing_snapshot(self) -> None:
        self._seed_store()
        result, published = self._invoke(*self._publish_arguments())
        self.assertEqual(result, 0)
        original = self.snapshot_path.read_bytes()
        candidate = self.runtime_root / ".coin-rates.json.relay-test.tmp"
        candidate.write_bytes(original)
        result, payload = self._invoke(
            "install-relayed",
            "--environment",
            "production",
            "--production-confirmation",
            PRODUCTION_CONFIRMATION,
            "--runtime-root",
            str(self.runtime_root),
            "--candidate",
            str(candidate),
            "--snapshot",
            str(self.snapshot_path),
            "--expected-sha256",
            "0" * 64,
        )
        self.assertEqual((result, payload["reason"]), (2, "snapshot_digest_mismatch"))
        self.assertEqual(self.snapshot_path.read_bytes(), original)
        with patch(
            "scripts.relay_production_coin_inference_snapshot._utc_now",
            return_value=self.now + timedelta(seconds=121),
        ):
            result, payload = self._invoke(
                "check",
                "--environment",
                "production",
                "--production-confirmation",
                PRODUCTION_CONFIRMATION,
                "--runtime-root",
                str(self.runtime_root),
                "--snapshot",
                str(self.snapshot_path),
                "--maximum-age-seconds",
                "120",
            )
        self.assertEqual((result, payload["reason"]), (2, "snapshot_stale_or_future"))

    def test_valid_remote_candidate_is_promoted_with_identical_digest(self) -> None:
        self._seed_store()
        result, published = self._invoke(*self._publish_arguments())
        self.assertEqual(result, 0)
        original = self.snapshot_path.read_bytes()
        digest = str(published["snapshot_sha256"])
        candidate = self.runtime_root / ".coin-rates.json.relay-valid.tmp"
        candidate.write_bytes(original)
        self.snapshot_path.write_text("old", encoding="utf-8")
        result, payload = self._invoke(
            "install-relayed",
            "--environment",
            "production",
            "--production-confirmation",
            PRODUCTION_CONFIRMATION,
            "--runtime-root",
            str(self.runtime_root),
            "--candidate",
            str(candidate),
            "--snapshot",
            str(self.snapshot_path),
            "--expected-sha256",
            digest,
        )
        self.assertEqual((result, payload["status"], payload["snapshot_sha256"]), (0, "INSTALLED", digest))
        self.assertEqual(self.snapshot_path.read_bytes(), original)
        self.assertFalse(candidate.exists())

    def test_remote_transport_carries_digest_into_guarded_atomic_installer(self) -> None:
        self._seed_store()
        result, published = self._invoke(*self._publish_arguments())
        self.assertEqual(result, 0)
        digest = str(published["snapshot_sha256"])
        identity = Path(self.temporary.name) / "production-relay-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o600)
        with patch(
            "scripts.relay_production_coin_inference_snapshot._run_bounded"
        ) as run:
            _relay_remote(
                self.snapshot_path,
                remote_host="root@192.0.2.10",
                remote_port=2200,
                remote_runtime_root="/srv/trading-bot/production-data/coin-intelligence/production-runtime",
                remote_snapshot="/srv/trading-bot/production-data/coin-intelligence/production-runtime/coin-rates.json",
                remote_project_dir="/srv/trading-bot/current",
                maximum_age_seconds=120,
                digest=digest,
                remote_identity_file=str(identity),
            )
        self.assertEqual(run.call_count, 3)
        rendered = "\n".join(str(call.args[0]) for call in run.call_args_list)
        self.assertIn("install-relayed", rendered)
        self.assertIn("--expected-sha256", rendered)
        self.assertIn(digest, rendered)
        self.assertIn(PRODUCTION_CONFIRMATION, rendered)
        self.assertIn("PasswordAuthentication=no", rendered)
        self.assertIn("KbdInteractiveAuthentication=no", rendered)
        self.assertIn("IdentitiesOnly=yes", rendered)
        self.assertIn(str(identity), rendered)

    def test_bounded_transport_kills_process_group_before_timeout_returns(self) -> None:
        process = MagicMock()
        process.pid = 43210
        process.poll.side_effect = [0]
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["ssh"], 1),
            subprocess.TimeoutExpired(["ssh"], 2),
            ("", ""),
        ]
        with patch(
            "scripts.relay_production_coin_inference_snapshot.subprocess.Popen",
            return_value=process,
        ) as popen, patch(
            "scripts.relay_production_coin_inference_snapshot.os.killpg"
        ) as killpg, patch(
            "scripts.relay_production_coin_inference_snapshot._process_group_has_live_members",
            return_value=True,
        ), patch(
            "scripts.relay_production_coin_inference_snapshot._wait_for_process_group_exit",
            return_value=True,
        ):
            with self.assertRaisesRegex(
                ProductionSnapshotRelayError,
                "remote_transport_timeout",
            ):
                _run_bounded(["ssh", "host", "true"], timeout=1, check=True)

        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list if call.args[1] != 0],
            [signal.SIGTERM, signal.SIGKILL],
        )
        self.assertEqual(process.communicate.call_count, 3)

    def test_bounded_transport_fails_closed_if_group_cannot_be_reaped(self) -> None:
        process = MagicMock()
        process.pid = 43211
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired(["ssh"], 1)
        process.stdout = MagicMock()
        process.stderr = MagicMock()
        with patch(
            "scripts.relay_production_coin_inference_snapshot.subprocess.Popen",
            return_value=process,
        ), patch(
            "scripts.relay_production_coin_inference_snapshot.os.killpg"
        ), patch(
            "scripts.relay_production_coin_inference_snapshot._wait_for_process_group_exit",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                ProductionSnapshotRelayError,
                "remote_process_group_not_stopped",
            ):
                _run_bounded(["ssh", "host", "true"], timeout=1, check=True)

        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_bounded_transport_kills_term_resistant_descendant_after_leader_exits(self) -> None:
        child_pid_path = Path(self.temporary.name) / "transport-child.pid"
        program = (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
        )
        child_pid: int | None = None
        try:
            with self.assertRaisesRegex(
                ProductionSnapshotRelayError,
                "remote_transport_timeout",
            ):
                _run_bounded(
                    [sys.executable, "-c", program, str(child_pid_path)],
                    timeout=1,
                    check=True,
                )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())
        finally:
            if child_pid is not None and Path(f"/proc/{child_pid}").exists():
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_bounded_transport_normal_return_fails_closed_on_detached_descendant(self) -> None:
        child_identity = Path(self.temporary.name) / "normal-return-child.pid"
        escaped_marker = Path(self.temporary.name) / "normal-return-child.escaped"
        child_code = "\n".join(
            (
                "import os, pathlib, signal, sys, time",
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                "time.sleep(0.75)",
                "pathlib.Path(sys.argv[2]).write_text('escaped')",
            )
        )
        leader_code = "\n".join(
            (
                "import pathlib, subprocess, sys, time",
                "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                "ready = pathlib.Path(sys.argv[2])",
                "deadline = time.monotonic() + 5",
                "while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)",
                "raise SystemExit(0 if ready.exists() else 91)",
            )
        )
        child_pid = None
        try:
            with self.assertRaisesRegex(
                ProductionSnapshotRelayError,
                "remote_process_group_survived_normal_exit",
            ):
                _run_bounded(
                    [
                        sys.executable,
                        "-c",
                        leader_code,
                        child_code,
                        str(child_identity),
                        str(escaped_marker),
                    ],
                    timeout=3,
                    check=True,
                )
            child_pid, child_group = map(
                int, child_identity.read_text(encoding="utf-8").split(":")
            )
            self.assertFalse(_process_group_has_live_members(child_group))
            time.sleep(0.85)
            self.assertFalse(escaped_marker.exists())
        finally:
            if child_pid is None and child_identity.exists():
                child_pid = int(
                    child_identity.read_text(encoding="utf-8").split(":", 1)[0]
                )
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_relay_lock_rejects_symlink_insecure_mode_and_hardlink(self) -> None:
        lock = self.runtime_root / ".production-snapshot-relay.lock"
        target = self.runtime_root / "operator-owned-lock"
        target.write_text("lock\n", encoding="utf-8")
        target.chmod(0o600)
        lock.symlink_to(target)
        with self.assertRaises(ProductionSnapshotRelayError):
            with _single_writer_lock(self.snapshot_path):
                pass
        lock.unlink()

        lock.write_text("lock\n", encoding="utf-8")
        lock.chmod(0o666)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "snapshot_lock_invalid"):
            with _single_writer_lock(self.snapshot_path):
                pass
        lock.chmod(0o600)
        hardlink = self.runtime_root / "relay-lock-hardlink"
        os.link(lock, hardlink)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "snapshot_lock_invalid"):
            with _single_writer_lock(self.snapshot_path):
                pass

    def test_remote_identity_must_be_canonical_regular_and_private(self) -> None:
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "remote_identity_file_required"):
            _validate_remote_identity_file(None)
        identity = Path(self.temporary.name) / "production-relay-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o644)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "remote_identity_file_invalid"):
            _validate_remote_identity_file(str(identity))
        identity.chmod(0o600)
        self.assertEqual(_validate_remote_identity_file(str(identity)), identity)
        alias = identity.with_name("production-relay-identity-link")
        alias.symlink_to(identity)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "remote_identity_file_invalid"):
            _validate_remote_identity_file(str(alias))
        hardlink = identity.with_name("production-relay-identity-hardlink")
        os.link(identity, hardlink)
        with self.assertRaisesRegex(ProductionSnapshotRelayError, "remote_identity_file_invalid"):
            _validate_remote_identity_file(str(identity))

    def test_direct_cli_rejects_noncanonical_remote_contract_before_local_publish(self) -> None:
        self._seed_store()
        cases = (
            ("--remote-project-dir", "/srv/trading-bot/production/../current"),
            ("--remote-runtime-root", "/srv/trading-bot//production-runtime"),
            ("--remote-snapshot", "/srv/trading-bot/production-runtime/%n.json"),
            ("--remote-host", f"root@{'a' * 64}.example"),
        )
        for option, invalid in cases:
            arguments = self._publish_arguments() + [
                "--remote-host",
                "root@production-host.example",
                "--remote-port",
                "2200",
                "--remote-runtime-root",
                "/srv/trading-bot/production-runtime",
                "--remote-snapshot",
                "/srv/trading-bot/production-runtime/coin-rates.json",
                "--remote-project-dir",
                "/srv/trading-bot/production-current",
            ]
            arguments[arguments.index(option) + 1] = invalid
            result, payload = self._invoke(*arguments)
            self.assertEqual(result, 2)
            self.assertEqual(payload["status"], "FAILED")
            self.assertFalse(self.snapshot_path.exists())

    def test_direct_remote_relay_requires_explicit_identity_before_local_publish(self) -> None:
        self._seed_store()
        arguments = self._publish_arguments() + [
            "--remote-host",
            "root@production-host.example",
            "--remote-port",
            "2200",
            "--remote-runtime-root",
            "/srv/trading-bot/production-runtime",
            "--remote-snapshot",
            "/srv/trading-bot/production-runtime/coin-rates.json",
            "--remote-project-dir",
            "/srv/trading-bot/production-current",
        ]
        result, payload = self._invoke(*arguments)
        self.assertEqual((result, payload["reason"]), (2, "remote_identity_file_required"))
        self.assertFalse(self.snapshot_path.exists())

    def test_production_maximum_age_is_fixed_at_120_seconds(self) -> None:
        self._seed_store()
        result, payload = self._invoke(*self._publish_arguments(), "--maximum-age-seconds", "121")
        self.assertEqual((result, payload["reason"]), (2, "maximum_age_seconds_invalid"))
        self.assertFalse(self.snapshot_path.exists())

    def test_compose_mounts_only_three_consumers_read_only_with_flags_off(self) -> None:
        foreign = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        iran = (REPO_ROOT / "docker-compose.iran.yml").read_text(encoding="utf-8")
        for variable in (
            "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED:-false",
            "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED:-false",
            "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED:-false",
            "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED:-false",
        ):
            self.assertEqual(foreign.count(variable), 2)
            self.assertEqual(iran.count(variable), 1)
        source = (
            "source: ${PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR:-"
            "/srv/trading-bot/production-data/coin-intelligence/production-runtime}"
        )
        target = "target: /app/runtime/coin-inference"
        container_snapshot = (
            "COIN_INTELLIGENCE_INFERENCE_SNAPSHOT_PATH: "
            "/app/runtime/coin-inference/coin-rates.json"
        )
        self.assertEqual(foreign.count(source), 2)
        self.assertEqual(iran.count(source), 1)
        self.assertEqual(foreign.count(target), 2)
        self.assertEqual(iran.count(target), 1)
        self.assertEqual(foreign.count(container_snapshot), 2)
        self.assertEqual(iran.count(container_snapshot), 1)
        self.assertEqual(foreign.count("read_only: true"), 2)
        self.assertEqual(iran.count("read_only: true"), 1)
        self.assertNotIn("COIN_INTELLIGENCE_INFERENCE_SNAPSHOT", foreign.split("sync_worker:", 1)[1])
        self.assertNotIn("COIN_INTELLIGENCE_INFERENCE_SNAPSHOT", iran.split("sync_worker:", 1)[1])

    def test_installer_requires_explicit_confirmation_and_production_paths(self) -> None:
        installer = (REPO_ROOT / "scripts/install_production_coin_inference_snapshot_relay.sh").read_text()
        self.assertIn('PRODUCTION_COIN_INFERENCE_CONFIRM:-', installer)
        self.assertIn(PRODUCTION_CONFIRMATION, installer)
        self.assertIn("production-runtime", installer)
        self.assertIn("ReadOnlyPaths=$PROJECT_DIR $SOURCE_ROOT", installer)
        self.assertIn("ReadWritePaths=$LOCAL_ROOT", installer)
        self.assertIn("TimeoutStartSec=180", installer)
        self.assertIn("TimeoutStopSec=15", installer)
        self.assertIn("OnCalendar=*-*-* *:*:05,35", installer)
        self.assertNotIn("set -x", installer)
        self.assertIn("production_remote_manifest_required", installer)
        self.assertIn("remote_key_connectivity_failed", installer)
        self.assertIn("PasswordAuthentication=no", installer)
        self.assertIn("KbdInteractiveAuthentication=no", installer)
        self.assertIn("IdentitiesOnly=yes", installer)
        self.assertIn("remote_identity_file_required", installer)
        self.assertIn('[[ "$MAXIMUM_AGE_SECONDS" == "120" ]]', installer)
        self.assertIn("systemd-analyze verify", installer)
        self.assertIn("restore_prior_units_and_state", installer)
        self.assertIn("transaction_exit_handler", installer)
        self.assertIn("prior_state_preserved=true", installer)
        self.assertIn('SYSTEMD_DIR="/etc/systemd/system"', installer)
        self.assertIn('BACKUP_ROOT="/var/backups/trading-bot/systemd"', installer)
        self.assertIn("PRODUCTION_OPERATION_LOCK_PATH", installer)
        self.assertIn("PRODUCTION_SOURCE_LOCK_PATH", installer)
        self.assertIn('PRODUCTION_COIN_INFERENCE_REMOTE_HOST:-}', installer)
        self.assertIn('PRODUCTION_COIN_INFERENCE_REMOTE_PORT:-}', installer)
        self.assertIn('PRODUCTION_COIN_INFERENCE_REMOTE_PROJECT_DIR:-}', installer)
        self.assertNotIn("65.109.220.59", installer)
        self.assertNotIn("37067", installer)

    def test_installer_rolls_back_units_and_timer_state_after_installed_verify_failure(self) -> None:
        base = Path(self.temporary.name)
        systemd_dir = base / "systemd"
        backup_root = base / "backups"
        installer = _sandbox_relay_installer(
            base, systemd_dir=systemd_dir, backup_root=backup_root
        )
        systemd_dir.mkdir()
        service = systemd_dir / "coin-intelligence-production-snapshot-relay.service"
        timer = systemd_dir / "coin-intelligence-production-snapshot-relay.timer"
        service.write_text("old-service\n", encoding="utf-8")
        timer.write_text("old-timer\n", encoding="utf-8")
        identity = base / "production-relay-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o600)
        environment = _fake_systemd_environment(base / "fake", analyze_fail_at=2)
        state = Path(environment["FAKE_STATE"])
        timer_name = timer.name
        (state / f"enabled.{timer_name}").touch()
        (state / f"active.{timer_name}").touch()
        environment.update(
            {
                "PRODUCTION_COIN_INFERENCE_CONFIRM": PRODUCTION_CONFIRMATION,
                "PROJECT_DIR": str(REPO_ROOT),
                "PRODUCTION_COIN_INFERENCE_SOURCE_ROOT": str(self.source_root),
                "PRODUCTION_COIN_INFERENCE_SOURCE_STORE": str(self.store_path),
                "PRODUCTION_COIN_INFERENCE_RUNTIME_ROOT": str(self.runtime_root),
                "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_PATH": str(self.snapshot_path),
                "PRODUCTION_COIN_INFERENCE_REMOTE_HOST": "root@production-host.example",
                "PRODUCTION_COIN_INFERENCE_REMOTE_PORT": "2200",
                "PRODUCTION_COIN_INFERENCE_REMOTE_RUNTIME_ROOT": "/srv/trading-bot/production-runtime",
                "PRODUCTION_COIN_INFERENCE_REMOTE_SNAPSHOT": "/srv/trading-bot/production-runtime/coin-rates.json",
                "PRODUCTION_COIN_INFERENCE_REMOTE_PROJECT_DIR": "/srv/trading-bot/production-current",
                "PRODUCTION_COIN_INFERENCE_REMOTE_IDENTITY_FILE": str(identity),
                "PRODUCTION_COIN_INFERENCE_SYSTEMD_DIR": str(systemd_dir),
                "PRODUCTION_COIN_INFERENCE_SYSTEMD_BACKUP_ROOT": str(backup_root),
            }
        )
        result = subprocess.run(
            [str(installer)],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rolled_back", result.stderr)
        self.assertNotIn("production-host", result.stdout + result.stderr)
        self.assertEqual(service.read_text(encoding="utf-8"), "old-service\n")
        self.assertEqual(timer.read_text(encoding="utf-8"), "old-timer\n")
        self.assertTrue((state / f"enabled.{timer_name}").is_file())
        self.assertTrue((state / f"active.{timer_name}").is_file())
        backups = list(backup_root.glob("coin-snapshot-relay.*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in backups[0].iterdir()))

    def test_installer_success_preserves_existing_disabled_inactive_timer(self) -> None:
        base = Path(self.temporary.name)
        systemd_dir = base / "systemd-success"
        backup_root = base / "backups-success"
        installer = _sandbox_relay_installer(
            base, systemd_dir=systemd_dir, backup_root=backup_root
        )
        systemd_dir.mkdir()
        for unit in (
            "coin-intelligence-production-snapshot-relay.service",
            "coin-intelligence-production-snapshot-relay.timer",
        ):
            (systemd_dir / unit).write_text(f"old-{unit}\n", encoding="utf-8")
        identity = base / "production-relay-success-identity"
        identity.write_text("test-key-material\n", encoding="utf-8")
        identity.chmod(0o600)
        environment = _fake_systemd_environment(base / "fake-success")
        environment.update(
            {
                "PRODUCTION_COIN_INFERENCE_CONFIRM": PRODUCTION_CONFIRMATION,
                "PROJECT_DIR": str(REPO_ROOT),
                "PRODUCTION_COIN_INFERENCE_SOURCE_ROOT": str(self.source_root),
                "PRODUCTION_COIN_INFERENCE_SOURCE_STORE": str(self.store_path),
                "PRODUCTION_COIN_INFERENCE_RUNTIME_ROOT": str(self.runtime_root),
                "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_PATH": str(self.snapshot_path),
                "PRODUCTION_COIN_INFERENCE_REMOTE_HOST": "root@production-host.example",
                "PRODUCTION_COIN_INFERENCE_REMOTE_PORT": "2200",
                "PRODUCTION_COIN_INFERENCE_REMOTE_RUNTIME_ROOT": "/srv/trading-bot/production-runtime",
                "PRODUCTION_COIN_INFERENCE_REMOTE_SNAPSHOT": "/srv/trading-bot/production-runtime/coin-rates.json",
                "PRODUCTION_COIN_INFERENCE_REMOTE_PROJECT_DIR": "/srv/trading-bot/production-current",
                "PRODUCTION_COIN_INFERENCE_REMOTE_IDENTITY_FILE": str(identity),
                "PRODUCTION_COIN_INFERENCE_SYSTEMD_DIR": str(systemd_dir),
                "PRODUCTION_COIN_INFERENCE_SYSTEMD_BACKUP_ROOT": str(backup_root),
            }
        )
        result = subprocess.run(
            [str(installer)], env=environment, capture_output=True, text=True, check=False
        )
        self.assertEqual(
            result.returncode,
            0,
            result.stderr + result.stdout + Path(environment["FAKE_SYSTEMD_LOG"]).read_text(),
        )
        self.assertIn("prior_state_preserved=true", result.stdout)
        state = Path(environment["FAKE_STATE"])
        timer_name = "coin-intelligence-production-snapshot-relay.timer"
        self.assertFalse((state / f"enabled.{timer_name}").exists())
        self.assertFalse((state / f"active.{timer_name}").exists())


if __name__ == "__main__":
    unittest.main()
