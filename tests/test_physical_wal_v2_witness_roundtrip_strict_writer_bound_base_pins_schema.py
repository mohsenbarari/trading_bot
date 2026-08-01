"""Static adversarial checks for immutable Gen2 opaque-base persistence pins."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "migrations" / "versions" / "0v2basepin01_add_v2_gen2_base_pin_columns.py"
)


class PhysicalWalV2WitnessRoundtripStrictWriterBoundBasePinsSchemaTests(
    unittest.TestCase
):
    def _migration(self):
        spec = importlib.util.spec_from_file_location(
            "v2wsrcb_base_pins_migration",
            MIGRATION_PATH,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_latest_model_requires_durable_base_configuration_and_commit_identity(self) -> None:
        table = PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__table__
        self.assertIn("v2_base_configuration_sha256", table.c)
        self.assertIn("v2_base_commit_id", table.c)
        self.assertFalse(table.c.v2_base_configuration_sha256.nullable)
        self.assertFalse(table.c.v2_base_commit_id.nullable)
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.assertIn("v2_base_configuration_sha256 VARCHAR(64) NOT NULL", ddl)
        self.assertIn("v2_base_commit_id VARCHAR(128) NOT NULL", ddl)
        self.assertIn("UNIQUE (v2_base_commit_id)", ddl)
        self.assertIn(
            "v2_base_configuration_sha256 ~ '^[0-9a-f]{64}$'",
            ddl,
        )
        self.assertIn(
            "v2_base_commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$'",
            ddl,
        )

    def test_child_migration_fails_closed_instead_of_guessing_opaque_rows(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        module = self._migration()
        self.assertEqual("0v2basepin01", module.revision)
        self.assertEqual("0v2consreg01", module.down_revision)
        self.assertIn("v2_base_configuration_sha256", source)
        self.assertIn("v2_base_commit_id", source)
        self.assertIn("ACCESS EXCLUSIVE", source)
        guard = "refusing Gen2 V2 base-pin migration: durable bound rows exist"
        self.assertIn(guard, source)
        self.assertIn(
            "refusing destructive Gen2 V2 base-pin downgrade: durable bound rows exist",
            source,
        )
        self.assertIn("existing append-only row/truncate", source)
        self.assertNotIn("ALTER TABLE physical_wal_v2_witness_roundtrip_strict_writer_commits", source)
        self.assertNotIn("CREATE FUNCTION", source)
        self.assertNotIn("CREATE TRIGGER", source)

        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            imports
            & {
                "asyncio",
                "boto3",
                "botocore",
                "httpx",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )

    def test_upgrade_adds_two_required_pins_and_preserves_existing_guards(self) -> None:
        module = self._migration()
        executed: list[str] = []
        columns: list[tuple[str, str, bool]] = []
        checks: list[tuple[str, str, str]] = []
        uniques: list[tuple[str, str, tuple[str, ...]]] = []
        with (
            patch.object(module.op, "execute", side_effect=executed.append),
            patch.object(
                module.op,
                "add_column",
                side_effect=lambda table, column: columns.append(
                    (table, column.name, column.nullable)
                ),
            ),
            patch.object(
                module.op,
                "create_check_constraint",
                side_effect=lambda name, table, sql: checks.append((name, table, sql)),
            ),
            patch.object(
                module.op,
                "create_unique_constraint",
                side_effect=lambda name, table, columns: uniques.append(
                    (name, table, tuple(columns))
                ),
            ),
        ):
            module.upgrade()
        self.assertEqual(
            [
                (
                    "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
                    "v2_base_configuration_sha256",
                    False,
                ),
                (
                    "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
                    "v2_base_commit_id",
                    False,
                ),
            ],
            columns,
        )
        self.assertEqual(2, len(checks))
        self.assertIn("v2_base_configuration_sha256", checks[0][2])
        self.assertIn("v2_base_commit_id", checks[1][2])
        self.assertEqual(
            [
                (
                    "ux_v2wsrcb_base_commit_id",
                    "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
                    ("v2_base_commit_id",),
                )
            ],
            uniques,
        )
        rendered = "\n".join(executed)
        self.assertIn("LOCK TABLE", rendered)
        self.assertIn("durable bound rows exist", rendered)


if __name__ == "__main__":
    unittest.main()
