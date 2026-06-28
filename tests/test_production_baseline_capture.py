from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import capture_production_baseline as baseline


class ProductionBaselineCaptureTests(unittest.TestCase):
    def test_redact_mapping_keeps_safe_values_and_redacts_sensitive_keys(self) -> None:
        values = {
            "IRAN_HOST": "87.107.3.22",
            "IRAN_SSH_PRIVATE_KEY_PATH": "/root/.ssh/id_prod",
            "OBSERVABILITY_API_KEY": "secret",
        }
        redacted = baseline.redact_mapping(
            values,
            allow_keys=("IRAN_HOST", "IRAN_SSH_PRIVATE_KEY_PATH", "OBSERVABILITY_API_KEY"),
        )

        self.assertEqual(redacted["IRAN_HOST"], "87.107.3.22")
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
        args = baseline.remote_args(
            {
                "IRAN_HOST": "87.107.3.22",
                "IRAN_SSH_PORT": "22",
                "IRAN_SSH_USER": "root",
            },
            "true",
        )

        self.assertIn("StrictHostKeyChecking=accept-new", args)
        self.assertNotIn("StrictHostKeyChecking=no", args)


if __name__ == "__main__":
    unittest.main()
