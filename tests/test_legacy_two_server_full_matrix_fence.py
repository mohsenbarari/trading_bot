import io
import json
import unittest
from unittest.mock import patch

from core.legacy_two_server_full_matrix_fence import (
    LegacyTwoServerFullMatrixRetiredError,
    assert_legacy_two_server_full_matrix_retired,
    blocked_legacy_two_server_full_matrix_payload,
)
from scripts import run_staging_two_server_full_matrix as staging_runner


class LegacyTwoServerFullMatrixFenceTests(unittest.TestCase):
    def test_assertion_is_not_configurable(self):
        with self.assertRaises(LegacyTwoServerFullMatrixRetiredError):
            assert_legacy_two_server_full_matrix_retired(
                component="test", operation="external action"
            )

    def test_payload_is_stable_and_non_secret(self):
        payload = blocked_legacy_two_server_full_matrix_payload(component="test")

        self.assertEqual(payload["status"], "blocked_legacy_two_server_full_matrix_retired")
        self.assertEqual(payload["component"], "test")
        self.assertNotIn("token", json.dumps(payload).lower())

    def test_staging_preflight_and_execute_are_blocked_before_argument_parsing(self):
        for argv in (
            ["--mode", "preflight", "--iran-ssh-host", "not-a-host"],
            ["--mode=execute", "--iran-base-url", "not-a-url"],
        ):
            with self.subTest(argv=argv), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = staging_runner.main(argv)

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["status"], "blocked_legacy_two_server_full_matrix_retired")
            self.assertEqual(payload["component"], "staging-two-server-full-matrix-runner")

    def test_staging_plan_cli_is_also_retired_before_command_plan_emission(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = staging_runner.main(["--mode", "plan", "--run-id", "FMX_HISTORICAL_PLAN"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "blocked_legacy_two_server_full_matrix_retired")
        self.assertEqual(payload["component"], "staging-two-server-full-matrix-runner")


if __name__ == "__main__":
    unittest.main()
