import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.run_guarded_scratch_alembic import (
    ScratchMigrationSafetyError,
    _validate_command,
    validate_checkout,
    validate_scratch_database_urls,
)


class GuardedScratchAlembicTests(unittest.TestCase):
    def test_requires_explicit_scratch_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "mode"):
                validate_scratch_database_urls(
                    sync_database_url="postgresql://user:pass@db/stage1_migration_test",
                    database_url="postgresql+asyncpg://user:pass@db/stage1_migration_test",
                )

    def test_accepts_driver_difference_for_same_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/stage2_registration_test",
                database_url="postgresql+asyncpg://user:pass@db/stage2_registration_test",
            )
        self.assertEqual(target.database_name, "stage2_registration_test")

    def test_accepts_stage3_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/stage3_registration_test",
                database_url="postgresql+asyncpg://user:pass@db/stage3_registration_test",
            )
        self.assertEqual(target.database_name, "stage3_registration_test")

    def test_rejects_runtime_name_and_url_mismatch(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "runtime"):
                validate_scratch_database_urls(
                    sync_database_url="postgresql://user:pass@db/trading_bot_db",
                    database_url="postgresql://user:pass@db/trading_bot_db",
                )
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "same target"):
                validate_scratch_database_urls(
                    sync_database_url="postgresql://user:pass@db/stage1_migration_one",
                    database_url="postgresql://user:pass@db/stage1_migration_two",
                )

    def test_checkout_and_command_are_bounded(self):
        repo_root = Path(__file__).resolve().parents[1]
        validate_checkout(expected_checkout=str(repo_root), repo_root=repo_root)
        with self.assertRaisesRegex(ScratchMigrationSafetyError, "checkout"):
            validate_checkout(expected_checkout="/tmp/not-this-repo", repo_root=repo_root)
        _validate_command(["upgrade", "head"])
        with self.assertRaisesRegex(ScratchMigrationSafetyError, "only guarded"):
            _validate_command(["revision", "--autogenerate"])


if __name__ == "__main__":
    unittest.main()
