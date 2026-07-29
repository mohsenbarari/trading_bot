from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.production_writer_lease import LEASE_SCHEMA


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SPEC = importlib.util.spec_from_file_location(
    "restore_webapp_ir_snapshot", ROOT / "scripts/restore_webapp_ir_snapshot.py"
)
assert RESTORE_SPEC and RESTORE_SPEC.loader
RESTORE = importlib.util.module_from_spec(RESTORE_SPEC)
sys.modules[RESTORE_SPEC.name] = RESTORE
RESTORE_SPEC.loader.exec_module(RESTORE)
SPEC = importlib.util.spec_from_file_location(
    "refresh_webapp_ir_snapshot_standby", ROOT / "scripts/refresh_webapp_ir_snapshot_standby.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def secure_write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)


def write_writer_lease(
    path: Path,
    *,
    holder_site: str = "webapp_ir",
    expires_at: datetime | None = None,
) -> None:
    current = datetime.now(timezone.utc)
    secure_write(
        path,
        json.dumps(
            {
                "schema": LEASE_SCHEMA,
                "holder_site": holder_site,
                "writer_epoch": 3,
                "lease_id": "lease-3",
                "issued_at": (current - timedelta(seconds=30)).isoformat(),
                "expires_at": (expires_at or current + timedelta(minutes=5)).isoformat(),
                "witness_transition_id": "transition-3",
                "proof_sha256": "a" * 64,
            }
        ),
    )


class RefreshWebappIrSnapshotStandbyTests(unittest.TestCase):
    def _apply_arguments(self, root: Path, *, writer_lease_file: Path):
        data = root / "data"
        work = data / "work"
        state = data / "state"
        work.mkdir(parents=True)
        state.mkdir()
        for directory in (data, work, state):
            os.chmod(directory, 0o700)
        config = root / "transport.json"
        secure_write(config, '{"maximum_snapshot_age_seconds": 30}\n')
        env = root / "standby.env"
        secure_write(
            env,
            "\n".join(
                (
                    f"WA_IR_SNAPSHOT_WORK_ROOT={work}",
                    f"WA_IR_SNAPSHOT_STATE_ROOT={state}",
                    f"WA_IR_SNAPSHOT_TRANSPORT_CONFIG={config}",
                    f"WA_IR_SNAPSHOT_WRITER_LEASE_FILE={writer_lease_file}",
                    "WA_IR_SNAPSHOT_MAX_AGE_SECONDS=30",
                )
            )
            + "\n",
        )
        transport = root / "transport.py"
        restore = root / "restore.py"
        for tool in (transport, restore):
            tool.write_text("# fixture\n", encoding="utf-8")
            os.chmod(tool, 0o700)
        arguments = MODULE.build_parser().parse_args(
            [
                "--standby-env",
                str(env),
                "--transport-script",
                str(transport),
                "--restore-script",
                str(restore),
                "--transport-python",
                sys.executable,
                "--restore-python",
                sys.executable,
                "--apply",
            ]
        )
        return arguments, work

    def test_plan_is_timer_bounded_and_starts_no_application_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            work = data / "work"
            state = data / "state"
            work.mkdir(parents=True)
            state.mkdir()
            for directory in (data, work, state):
                os.chmod(directory, 0o700)
            config = root / "transport.json"
            secure_write(config, '{"maximum_snapshot_age_seconds": 30}\n')
            env = root / "standby.env"
            secure_write(
                env,
                "\n".join(
                    (
                        f"WA_IR_SNAPSHOT_WORK_ROOT={work}",
                        f"WA_IR_SNAPSHOT_STATE_ROOT={state}",
                        f"WA_IR_SNAPSHOT_TRANSPORT_CONFIG={config}",
                        "WA_IR_SNAPSHOT_MAX_AGE_SECONDS=30",
                    )
                )
                + "\n",
            )
            # The default production scripts do not exist in this isolated
            # temporary package, so use root-owned read-only fixture files.
            transport = root / "transport.py"
            restore = root / "restore.py"
            for tool in (transport, restore):
                tool.write_text("# fixture\n", encoding="utf-8")
                os.chmod(tool, 0o700)
            payload = MODULE.execute(
                MODULE.build_parser().parse_args(
                    [
                        "--standby-env",
                        str(env),
                        "--transport-script",
                        str(transport),
                        "--restore-script",
                        str(restore),
                        "--transport-python",
                        sys.executable,
                        "--restore-python",
                        sys.executable,
                    ]
                )
            )
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["timer_interval_seconds"], 15)
        self.assertFalse(payload["app_started"])
        self.assertFalse(payload["direct_sync_started"])
        self.assertFalse(payload["migration_started"])
        self.assertFalse(payload["public_routing_changed"])

    def test_interval_outside_freshness_window_is_rejected(self) -> None:
        with self.assertRaisesRegex(RESTORE.RestoreError, "between 15 and 30"):
            MODULE.execute(MODULE.build_parser().parse_args(["--standby-env", "/missing", "--timer-interval-seconds", "31"]))

    def test_live_local_webapp_ir_lease_self_fences_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease_file = root / "writer-lease.json"
            write_writer_lease(lease_file)
            arguments, _ = self._apply_arguments(root, writer_lease_file=lease_file)
            with mock.patch.object(MODULE, "run_json_command") as run:
                with self.assertRaisesRegex(RESTORE.RestoreError, "live local webapp_ir Writer lease"):
                    MODULE.execute(arguments)
        run.assert_not_called()

    def test_unsafe_local_lease_file_fails_closed_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease_file = root / "writer-lease.json"
            write_writer_lease(lease_file)
            os.chmod(lease_file, 0o644)
            arguments, _ = self._apply_arguments(root, writer_lease_file=lease_file)
            with mock.patch.object(MODULE, "run_json_command") as run:
                with self.assertRaisesRegex(RESTORE.RestoreError, "lease is unsafe"):
                    MODULE.execute(arguments)
        run.assert_not_called()

    def test_live_lease_appearing_after_transport_self_fences_before_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lease_file = root / "writer-lease.json"
            write_writer_lease(
                lease_file,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            arguments, work = self._apply_arguments(root, writer_lease_file=lease_file)
            candidate = work / "candidate"
            candidate.mkdir()
            (candidate / "snapshot-ready.json").write_text("{}\n", encoding="utf-8")
            calls: list[str] = []

            def run(arguments, *, label):  # noqa: ANN001
                calls.append(label)
                if label == "snapshot transport consume":
                    write_writer_lease(lease_file)
                    return {"status": "ready", "candidate_directory": str(candidate)}
                self.fail(f"unexpected command: {label}")

            with mock.patch.object(MODULE, "run_json_command", side_effect=run):
                with self.assertRaisesRegex(RESTORE.RestoreError, "live local webapp_ir Writer lease"):
                    MODULE.execute(arguments)
        self.assertEqual(calls, ["snapshot transport consume"])


if __name__ == "__main__":
    unittest.main()
