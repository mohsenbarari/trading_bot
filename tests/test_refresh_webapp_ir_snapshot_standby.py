from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


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


class RefreshWebappIrSnapshotStandbyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
