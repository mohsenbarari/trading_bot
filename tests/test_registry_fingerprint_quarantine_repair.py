import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from scripts.repair_registry_fingerprint_quarantine import inspect_or_repair, validate_runtime_compatibility


class RegistryFingerprintQuarantineRepairTests(unittest.TestCase):
    def test_script_entrypoint_can_import_project_modules(self):
        result = subprocess.run(
            [sys.executable, "scripts/repair_registry_fingerprint_quarantine.py", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--expected-release-sha", result.stdout)

    def test_runtime_compatibility_requires_exact_release_and_registry(self):
        validate_runtime_compatibility(
            expected_release_sha="release-a",
            expected_registry_fingerprint="registry-a",
            actual_release_sha="release-a",
            actual_registry_fingerprint="registry-a",
        )

        with self.assertRaises(RuntimeError):
            validate_runtime_compatibility(
                expected_release_sha="release-a",
                expected_registry_fingerprint="registry-a",
                actual_release_sha="release-b",
                actual_registry_fingerprint="registry-a",
            )
        with self.assertRaises(RuntimeError):
            validate_runtime_compatibility(
                expected_release_sha="release-a",
                expected_registry_fingerprint="registry-a",
                actual_release_sha="release-a",
                actual_registry_fingerprint="registry-b",
            )

    def test_repair_only_releases_exact_registry_mismatch_quarantine(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (3,)
        cursor.rowcount = 3
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch(
            "scripts.repair_registry_fingerprint_quarantine.psycopg2.connect",
            return_value=connection,
        ):
            result = inspect_or_repair("postgresql://db/example", repair=True)

        self.assertEqual(result, {"status": "repaired", "matched": 3, "released": 3})
        self.assertEqual(cursor.execute.call_count, 2)
        update_sql, update_params = cursor.execute.call_args_list[1].args
        self.assertIn("quarantined_at IS NOT NULL", update_sql)
        self.assertIn("last_delivery_error = %s", update_sql)
        self.assertNotIn("synced = true", update_sql.lower())
        self.assertEqual(update_params, ("registry_fingerprint_mismatch",))


if __name__ == "__main__":
    unittest.main()
