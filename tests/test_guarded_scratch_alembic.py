import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.engine import make_url

from scripts import run_guarded_scratch_alembic as guarded
from scripts.run_guarded_scratch_alembic import (
    ScratchMigrationTarget,
    ScratchMigrationSafetyError,
    _validate_command,
    validate_alembic_source,
    validate_checkout,
    validate_scratch_database_urls,
    verify_connected_database,
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

    def test_accepts_stage4_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/stage4_registration_test",
                database_url="postgresql+asyncpg://user:pass@db/stage4_registration_test",
            )
        self.assertEqual(target.database_name, "stage4_registration_test")

    def test_accepts_market_stage7_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/market_stage7_quota_test",
                database_url="postgresql+asyncpg://user:pass@db/market_stage7_quota_test",
            )
        self.assertEqual(target.database_name, "market_stage7_quota_test")

    def test_accepts_market_stage9_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/market_stage9_repair_test",
                database_url="postgresql+asyncpg://user:pass@db/market_stage9_repair_test",
            )
        self.assertEqual(target.database_name, "market_stage9_repair_test")

    def test_accepts_market_stage10_scratch_target(self):
        with patch.dict(os.environ, {"TRADING_BOT_MIGRATION_MODE": "scratch"}, clear=True):
            target = validate_scratch_database_urls(
                sync_database_url="postgresql+psycopg2://user:pass@db/market_stage10_cancel_all_test",
                database_url="postgresql+asyncpg://user:pass@db/market_stage10_cancel_all_test",
            )
        self.assertEqual(target.database_name, "market_stage10_cancel_all_test")

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

    def test_urls_require_both_values_parse_cleanly_and_match_allowed_name(self):
        environment = {"TRADING_BOT_MIGRATION_MODE": "scratch"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "both database URLs"):
                validate_scratch_database_urls(sync_database_url="", database_url="postgresql://db/x")
            with patch.object(guarded, "make_url", side_effect=ValueError("bad")):
                with self.assertRaisesRegex(ScratchMigrationSafetyError, "parsing failed"):
                    validate_scratch_database_urls(sync_database_url="bad", database_url="bad")
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "allowed scratch"):
                validate_scratch_database_urls(
                    sync_database_url="postgresql://user:pass@db/not_stage9",
                    database_url="postgresql+asyncpg://user:pass@db/not_stage9",
                )

    def test_checkout_requires_expected_path_and_alembic_sources(self):
        repo_root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ScratchMigrationSafetyError, "expected checkout is required"):
            validate_checkout(expected_checkout="", repo_root=repo_root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "missing Alembic sources"):
                validate_checkout(expected_checkout=str(root), repo_root=root)

    def test_alembic_source_requires_one_expected_head(self):
        script = MagicMock()
        with patch.object(guarded.ScriptDirectory, "from_config", return_value=script):
            script.get_heads.return_value = ["one", "two"]
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "exactly one head"):
                validate_alembic_source()
            script.get_heads.return_value = ["head-a"]
            with patch.dict(
                os.environ,
                {"TRADING_BOT_EXPECTED_ALEMBIC_HEAD": "head-b"},
                clear=True,
            ):
                with self.assertRaisesRegex(ScratchMigrationSafetyError, "does not match"):
                    validate_alembic_source()
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(validate_alembic_source(), "head-a")

    def test_connected_database_must_match_and_engine_is_disposed(self):
        target = ScratchMigrationTarget(
            database_name="stage2_registration_test",
            sync_url=make_url(
                "postgresql+psycopg2://user:pass@db/stage2_registration_test"
            ),
        )
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.scalar_one.return_value = "stage2_registration_test"
        with patch.object(guarded, "create_engine", return_value=engine):
            verify_connected_database(target)
        engine.dispose.assert_called_once()

        engine.reset_mock()
        connection.execute.return_value.scalar_one.return_value = "other_database"
        with patch.object(guarded, "create_engine", return_value=engine):
            with self.assertRaisesRegex(ScratchMigrationSafetyError, "does not match"):
                verify_connected_database(target)
        engine.dispose.assert_called_once()

    def test_command_validation_rejects_missing_revision(self):
        for command in ([], ["upgrade"], ["downgrade", "base", "extra"]):
            with self.subTest(command=command), self.assertRaises(ScratchMigrationSafetyError):
                _validate_command(command)
        _validate_command(["current"])
        _validate_command(["history"])

    def test_main_stops_on_safety_or_unexpected_preflight_and_returns_alembic_status(self):
        with patch.object(
            guarded,
            "_validate_command",
            side_effect=ScratchMigrationSafetyError("unsafe"),
        ), patch.object(guarded.subprocess, "run") as run:
            self.assertEqual(guarded.main(["upgrade", "head"]), 2)
            run.assert_not_called()

        with patch.object(guarded, "_validate_command", side_effect=RuntimeError("boom")), patch.object(
            guarded.subprocess, "run"
        ) as run:
            self.assertEqual(guarded.main(["upgrade", "head"]), 2)
            run.assert_not_called()

        target = ScratchMigrationTarget(
            database_name="stage2_registration_test",
            sync_url=make_url(
                "postgresql+psycopg2://user:pass@db/stage2_registration_test"
            ),
        )
        with patch.object(guarded, "_validate_command"), patch.object(
            guarded, "validate_scratch_database_urls", return_value=target
        ), patch.object(guarded, "validate_checkout"), patch.object(
            guarded, "validate_alembic_source", return_value="head-a"
        ), patch.object(guarded, "verify_connected_database"), patch.object(
            guarded.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=7),
        ) as run:
            self.assertEqual(guarded.main(["downgrade", "base"]), 7)
        self.assertIn("alembic", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
