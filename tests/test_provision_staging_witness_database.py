import unittest
from pathlib import Path

from scripts.provision_staging_witness_database import (
    StagingWitnessProvisionError,
    _validate_bootstrap_identity,
)


class StagingWitnessBootstrapIdentityTests(unittest.TestCase):
    def test_relay_ledger_grant_supports_select_for_update(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "provision_staging_witness_database.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '("SELECT, INSERT, UPDATE", "human_approval_relay_receipts")',
            source,
        )

    def test_initial_database_owner_is_allowed(self):
        _validate_bootstrap_identity(
            current_user="postgres_initializer",
            database_owner="postgres_initializer",
            current_user_is_superuser=False,
            migrator="writer_witness_migrator",
            runtime="writer_witness_runtime",
        )

    def test_superuser_can_safely_repeat_bootstrap_after_owner_transfer(self):
        _validate_bootstrap_identity(
            current_user="postgres_initializer",
            database_owner="writer_witness_migrator",
            current_user_is_superuser=True,
            migrator="writer_witness_migrator",
            runtime="writer_witness_runtime",
        )

    def test_unrelated_database_owner_is_rejected(self):
        with self.assertRaises(StagingWitnessProvisionError):
            _validate_bootstrap_identity(
                current_user="postgres_initializer",
                database_owner="unrelated_owner",
                current_user_is_superuser=True,
                migrator="writer_witness_migrator",
                runtime="writer_witness_runtime",
            )

    def test_runtime_or_migrator_cannot_be_reused_as_bootstrap(self):
        for role in ("writer_witness_migrator", "writer_witness_runtime"):
            with self.subTest(role=role), self.assertRaises(
                StagingWitnessProvisionError
            ):
                _validate_bootstrap_identity(
                    current_user=role,
                    database_owner=role,
                    current_user_is_superuser=True,
                    migrator="writer_witness_migrator",
                    runtime="writer_witness_runtime",
                )


if __name__ == "__main__":
    unittest.main()
