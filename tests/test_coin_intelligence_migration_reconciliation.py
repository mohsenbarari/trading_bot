from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from alembic.config import Config
from alembic.script import ScriptDirectory

from migrations.versions import fb1c2d3e4f5a_reconcile_coin_schema_history as migration


REPO_ROOT = Path(__file__).resolve().parents[1]


class _TableInspector:
    def __init__(self, tables: set[str]) -> None:
        self._tables = tables

    def get_table_names(self) -> list[str]:
        return sorted(self._tables)


class CoinIntelligenceMigrationGraphTests(unittest.TestCase):
    def test_graph_has_one_reconciled_head(self) -> None:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)

        self.assertEqual(script.get_heads(), ["fc2d3e4f5a6b"])
        revisions = {
            item.revision: item
            for item in script.walk_revisions(base="base", head="fc2d3e4f5a6b")
        }
        self.assertEqual(revisions["fc2d3e4f5a6b"].down_revision, "fb1c2d3e4f5a")
        self.assertEqual(revisions["fb1c2d3e4f5a"].down_revision, "fa0b1c2d3e4f")
        self.assertEqual(
            revisions["f9b0c1d2e3a4"].down_revision,
            ("e8a4b5c6d7e9", "e5a1c4d7b2f9"),
        )
        self.assertEqual(revisions["f9c8d7e6a5b4"].down_revision, "f9b0c1d2e3a4")

    def test_complete_schema_is_validated_without_recreation(self) -> None:
        bind = Mock()
        with (
            patch.object(migration.op, "get_bind", return_value=bind),
            patch.object(
                migration.sa,
                "inspect",
                return_value=_TableInspector(set(migration._TABLES)),
            ),
            patch.object(migration, "_create_complete_coin_schema") as create,
            patch.object(migration, "_validate_schema") as validate,
        ):
            migration.upgrade()

        create.assert_not_called()
        validate.assert_called_once_with(bind)

    def test_all_absent_schema_is_recreated_then_validated(self) -> None:
        bind = Mock()
        with (
            patch.object(migration.op, "get_bind", return_value=bind),
            patch.object(migration.sa, "inspect", return_value=_TableInspector(set())),
            patch.object(migration, "_create_complete_coin_schema") as create,
            patch.object(migration, "_validate_schema") as validate,
        ):
            migration.upgrade()

        create.assert_called_once_with()
        validate.assert_called_once_with(bind)

    def test_partial_schema_fails_before_writing(self) -> None:
        bind = Mock()
        partial = {migration._TABLES[0]}
        with (
            patch.object(migration.op, "get_bind", return_value=bind),
            patch.object(migration.sa, "inspect", return_value=_TableInspector(partial)),
            patch.object(migration, "_create_complete_coin_schema") as create,
            patch.object(migration, "_validate_schema") as validate,
        ):
            with self.assertRaisesRegex(RuntimeError, "partial schema"):
                migration.upgrade()

        create.assert_not_called()
        validate.assert_not_called()

    def test_downgrade_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "intentionally blocked"):
            migration.downgrade()


if __name__ == "__main__":
    unittest.main()
