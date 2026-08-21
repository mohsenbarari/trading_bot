from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import capture_production_baseline as baseline


class ProductionBaselineCaptureTests(unittest.TestCase):
    def test_redact_mapping_keeps_safe_values_and_redacts_sensitive_keys(self) -> None:
        values = {
            "IRAN_HOST": "65.109.220.59",
            "IRAN_SSH_PRIVATE_KEY_PATH": "/root/.ssh/id_prod",
            "OBSERVABILITY_API_KEY": "secret",
        }
        redacted = baseline.redact_mapping(
            values,
            allow_keys=("IRAN_HOST", "IRAN_SSH_PRIVATE_KEY_PATH", "OBSERVABILITY_API_KEY"),
        )

        self.assertEqual(redacted["IRAN_HOST"], "65.109.220.59")
        self.assertEqual(redacted["IRAN_SSH_PRIVATE_KEY_PATH"], "[REDACTED]")
        self.assertEqual(redacted["OBSERVABILITY_API_KEY"], "[REDACTED]")

    def test_extract_unsynced_values_from_nested_health_payload(self) -> None:
        payload = {
            "local": {"unsynced_count": 0},
            "tables": [{"name": "users", "unsynced_rows": 0}, {"name": "messages", "unsynced_rows": 2}],
        }

        self.assertEqual(baseline.extract_unsynced_values(payload), [0, 0, 2])

    def test_parse_sync_health_marks_clean_when_all_unsynced_values_are_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "health.json"
            path.write_text(json.dumps({"unsynced_count": 0, "peer": {"unsynced_rows": 0}}), encoding="utf-8")

            parsed = baseline.parse_sync_health(path)

        self.assertTrue(parsed["parsed"])
        self.assertTrue(parsed["clean"])
        self.assertEqual(parsed["unsynced_values"], [0, 0])

    def test_parse_sync_health_marks_dirty_when_any_unsynced_value_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "health.json"
            path.write_text(json.dumps({"unsynced_count": 1}), encoding="utf-8")

            parsed = baseline.parse_sync_health(path)

        self.assertTrue(parsed["parsed"])
        self.assertFalse(parsed["clean"])
        self.assertEqual(parsed["unsynced_values"], [1])

    def test_remote_args_use_accept_new_host_key_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            identity = Path(temporary) / "production-key"
            identity.write_text("test-key-material\n", encoding="utf-8")
            identity.chmod(0o600)
            args = baseline.remote_args(
                {
                    "IRAN_HOST": "65.109.220.59",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_SSH_USER": "root",
                    "IRAN_SSH_AUTH_METHOD": "key",
                    "IRAN_SSH_PRIVATE_KEY_PATH": str(identity),
                },
                "true",
            )

        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertNotIn("StrictHostKeyChecking=no", args)
        self.assertIn("BatchMode=yes", args)
        self.assertIn("PasswordAuthentication=no", args)
        self.assertIn("IdentitiesOnly=yes", args)
        self.assertIn(str(identity), args)

    def test_remote_args_reject_password_auth_for_unattended_recoverability(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires key authentication"):
            baseline.remote_args(
                {
                    "IRAN_HOST": "65.109.220.59",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_SSH_USER": "root",
                    "IRAN_SSH_AUTH_METHOD": "password",
                },
                "true",
            )

    def test_remote_args_require_explicit_identity_for_unattended_recoverability(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit Iran SSH identity"):
            baseline.validate_production_iran_transport(
                {
                    "IRAN_HOST": "65.109.220.59",
                    "IRAN_SSH_PORT": "37067",
                    "IRAN_SSH_USER": "root",
                    "IRAN_SSH_AUTH_METHOD": "key",
                    "IRAN_SSH_PRIVATE_KEY_PATH": "",
                }
            )

    def test_timeout_terminates_term_resistant_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "descendant-finished"
            leader_code = (
                "import signal, subprocess, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "child = \"import pathlib, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('descendant-started', flush=True); "
                "time.sleep(1.0); pathlib.Path(sys.argv[1]).write_text('survived')\"; "
                "subprocess.Popen([sys.executable, '-c', child, sys.argv[1]]); "
                "print('leader-started', flush=True); time.sleep(30)"
            )

            with patch.object(baseline, "PROCESS_TERMINATION_GRACE_SECONDS", 0.1), patch.object(
                baseline, "PROCESS_KILL_TIMEOUT_SECONDS", 0.75
            ):
                result = baseline.run_command(
                    name="process_group_timeout_probe",
                    args=[sys.executable, "-c", leader_code, str(marker)],
                    logs_dir=root,
                    timeout=0.15,
                )

            time.sleep(1.1)
            self.assertEqual(result["exit_code"], 124)
            self.assertTrue(result["timed_out"])
            self.assertFalse(marker.exists())
            self.assertIn(
                "leader-started",
                (root / "process_group_timeout_probe.stdout.log").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertIn(
                "TIMEOUT after 0.15s",
                (root / "process_group_timeout_probe.stderr.log").read_text(
                    encoding="utf-8"
                ),
            )

    def test_successful_leader_with_detached_output_child_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "normal-return-descendant-finished"
            leader_code = (
                "import subprocess, sys; "
                "child = \"import pathlib, signal, sys, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(1.0); pathlib.Path(sys.argv[1]).write_text('survived')\"; "
                "subprocess.Popen([sys.executable, '-c', child, sys.argv[1]], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                "print('leader-complete', flush=True)"
            )

            with patch.object(baseline, "PROCESS_TERMINATION_GRACE_SECONDS", 0.1), patch.object(
                baseline, "PROCESS_KILL_TIMEOUT_SECONDS", 0.75
            ):
                result = baseline.run_command(
                    name="normal_return_process_group_probe",
                    args=[sys.executable, "-c", leader_code, str(marker)],
                    logs_dir=root,
                    timeout=5,
                )

            time.sleep(1.1)
            self.assertEqual(result["exit_code"], 125)
            self.assertFalse(result["timed_out"])
            self.assertFalse(marker.exists())
            self.assertIn(
                "CONTAINMENT ERROR",
                (root / "normal_return_process_group_probe.stderr.log").read_text(
                    encoding="utf-8"
                ),
            )


if __name__ == "__main__":
    unittest.main()
