from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "preflight_webapp_ir_dark_snapshot_standby",
    ROOT / "scripts/preflight_webapp_ir_dark_snapshot_standby.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SELECTED = "trading_bot_wa_ir_snapshot_db_snapshot_20260801"


class PreflightWebappIrDarkSnapshotStandbyTests(unittest.TestCase):
    def make_env(self) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "standby.env"
        path.write_text(f"WA_IR_SNAPSHOT_DB_CONTAINER={SELECTED}\n", encoding="utf-8")
        path.chmod(0o600)
        self.addCleanup(directory.cleanup)
        return directory

    def responses(self, *, names: str = SELECTED, state: str = '{"Status":"running","Health":{"Status":"healthy"}}', network: str = "none", ports: str = "{}", promotion_active: str = "inactive", promotion_enabled: str = "masked", timer_active: str = "active", timer_enabled: str = "enabled"):
        expected = {
            MODULE._DOCKER_PS: names + "\n",
            (*MODULE._DOCKER_INSPECT_STATE, SELECTED): state + "\n",
            (*MODULE._DOCKER_INSPECT_NETWORK, SELECTED): network + "\n",
            (*MODULE._DOCKER_INSPECT_PORTS, SELECTED): ports + "\n",
            (*MODULE._SYSTEMCTL_ACTIVE, MODULE._PROMOTION_UNIT): promotion_active + "\n",
            (*MODULE._SYSTEMCTL_ENABLED, MODULE._PROMOTION_UNIT): promotion_enabled + "\n",
            (*MODULE._SYSTEMCTL_ACTIVE, MODULE._REFRESH_TIMER_UNIT): timer_active + "\n",
            (*MODULE._SYSTEMCTL_ENABLED, MODULE._REFRESH_TIMER_UNIT): timer_enabled + "\n",
        }

        def run(command):
            output = expected[tuple(command)]
            code = 0 if command[0] == "/usr/bin/docker" or output.strip() in {"active", "enabled"} else 3
            return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

        return run

    def collect(self, directory: tempfile.TemporaryDirectory[str], **responses: object):
        with mock.patch.object(MODULE.os, "geteuid", return_value=0), mock.patch.object(
            MODULE, "run_command", side_effect=self.responses(**responses)
        ):
            return MODULE.collect(
                Path(directory.name) / "standby.env",
                now=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )

    def test_collects_exact_dark_snapshot_posture_from_fixed_commands(self) -> None:
        directory = self.make_env()
        observation = self.collect(directory)
        result = MODULE.verify_webapp_ir_dark_snapshot_preflight(
            observation, now=observation.observed_at
        )
        self.assertEqual({"snapshot_db"}, set(observation.services))
        self.assertEqual("none", observation.network_mode)
        self.assertEqual((), observation.published_ports)
        self.assertFalse(result.writer)
        self.assertFalse(result.promotion)
        self.assertFalse(result.execution)

    def test_collector_uses_only_the_fixed_observation_commands(self) -> None:
        directory = self.make_env()
        runner = self.responses()
        with mock.patch.object(MODULE.os, "geteuid", return_value=0), mock.patch.object(
            MODULE, "run_command", side_effect=runner
        ) as run:
            MODULE.collect(Path(directory.name) / "standby.env", now=datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(
            [
                mock.call(MODULE._DOCKER_PS),
                mock.call((*MODULE._DOCKER_INSPECT_STATE, SELECTED)),
                mock.call((*MODULE._DOCKER_INSPECT_NETWORK, SELECTED)),
                mock.call((*MODULE._DOCKER_INSPECT_PORTS, SELECTED)),
                mock.call((*MODULE._SYSTEMCTL_ACTIVE, MODULE._PROMOTION_UNIT)),
                mock.call((*MODULE._SYSTEMCTL_ENABLED, MODULE._PROMOTION_UNIT)),
                mock.call((*MODULE._SYSTEMCTL_ACTIVE, MODULE._REFRESH_TIMER_UNIT)),
                mock.call((*MODULE._SYSTEMCTL_ENABLED, MODULE._REFRESH_TIMER_UNIT)),
            ],
            run.call_args_list,
        )

    def test_any_extra_container_is_rejected_before_inspection(self) -> None:
        directory = self.make_env()
        with self.assertRaisesRegex(MODULE.DarkSnapshotHostPreflightError, "snapshot_db only"):
            self.collect(directory, names=SELECTED + "\nunrelated_container")

    def test_missing_or_stopped_selected_container_is_rejected(self) -> None:
        directory = self.make_env()
        with self.assertRaisesRegex(MODULE.DarkSnapshotHostPreflightError, "snapshot_db only"):
            self.collect(directory, names="")

    def test_rejects_ports_network_state_or_systemd_drift(self) -> None:
        directory = self.make_env()
        invalid = (
            {"ports": '{"5432/tcp":[{"HostPort":"5432"}]}'},
            {"network": "bridge"},
            {"state": '{"Status":"exited","Health":{"Status":"healthy"}}'},
            {"promotion_active": "active"},
            {"promotion_enabled": "disabled"},
            {"timer_active": "inactive"},
            {"timer_enabled": "disabled"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                observation = self.collect(directory, **changes)
                with self.assertRaises(MODULE.WebappIrDarkSnapshotPreflightError):
                    MODULE.verify_webapp_ir_dark_snapshot_preflight(
                        observation, now=observation.observed_at
                    )

    def test_non_root_and_unsafe_env_fail_closed(self) -> None:
        directory = self.make_env()
        path = Path(directory.name) / "standby.env"
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(MODULE.DarkSnapshotHostPreflightError, "run as root"):
                MODULE.collect(path)
        path.chmod(0o644)
        with mock.patch.object(MODULE.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(MODULE.DarkSnapshotHostPreflightError, "root-only"):
                MODULE.collect(path)

    def test_cli_has_only_required_arguments_and_emits_non_authorizing_json(self) -> None:
        parser = MODULE.build_parser()
        self.assertEqual({"standby_env", "json"}, {action.dest for action in parser._actions if action.dest != "help"})
        directory = self.make_env()
        with mock.patch.object(MODULE, "collect") as collect:
            collect.return_value = MODULE.WebappIrDarkSnapshotPreflightObservation(
                services={"snapshot_db": {"state": "running", "health": "healthy"}},
                network_mode="none",
                published_ports=(),
                promotion_state="inactive",
                promotion_unit_state="masked",
                refresh_timer_enabled=True,
                refresh_timer_state="active",
                observed_at=datetime.now(timezone.utc),
            )
            output = MODULE.execute(parser.parse_args(["--standby-env", str(Path(directory.name) / "standby.env"), "--json"]))
        self.assertEqual("verified-non-authorizing", output["status"])
        self.assertEqual(False, output["writer_authorized"])
        self.assertEqual(False, output["promotion_authorized"])
        self.assertEqual(False, output["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
