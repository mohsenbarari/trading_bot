"""Static adversarial tests for the global V2 Witness-consumption registry.

No test here opens a database.  The PostgreSQL scratch integration suite owns
execution of these DDL triggers; these checks make the cross-generation
invariant and migration ordering reviewable without touching an application
or production DSN.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    REPO_ROOT
    / "models"
    / "physical_wal_v2_witness_roundtrip_attestation_consumption.py"
)
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "0v2consreg01_add_v2_witness_attestation_consumption_registry.py"
)


class PhysicalWalV2WitnessRoundtripAttestationConsumptionRegistrySchemaTests(
    unittest.TestCase
):
    def _migration_module(self):
        spec = importlib.util.spec_from_file_location(
            "v2wsrc_registry_migration",
            MIGRATION_PATH,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_model_has_one_global_primary_key_and_immutable_source_shape(self) -> None:
        table = PhysicalWalV2WitnessRoundtripAttestationConsumption.__table__
        self.assertEqual(
            "physical_wal_v2_witness_roundtrip_attestation_consumptions",
            table.name,
        )
        self.assertEqual(
            (
                "attestation_sha256",
                "source_generation",
                "source_commit_id",
                "consumed_at",
            ),
            tuple(table.c.keys()),
        )
        self.assertEqual(
            ("attestation_sha256",),
            tuple(column.name for column in table.primary_key.columns),
        )
        checks = {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if getattr(constraint, "sqltext", None) is not None and constraint.name
        }
        self.assertEqual(
            {
                "ck_v2wsrc_registry_attestation",
                "ck_v2wsrc_registry_source",
                "ck_v2wsrc_registry_consumed_at",
            },
            set(checks),
        )
        self.assertIn(
            PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
            checks["ck_v2wsrc_registry_source"],
        )
        self.assertIn(
            PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
            checks["ck_v2wsrc_registry_source"],
        )
        self.assertIn("v2-witness-strict-writer-g2", checks["ck_v2wsrc_registry_source"])
        self.assertNotIn("id", table.c)
        self.assertNotIn("updated_at", table.c)

        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.assertIn("PRIMARY KEY (attestation_sha256)", ddl)
        self.assertIn("source_generation = 'strict_writer_gen1'", ddl)
        self.assertIn("source_generation = 'strict_writer_gen2'", ddl)

    def test_migration_locks_checks_overlap_backfills_and_claims_both_generations(self) -> None:
        module = self._migration_module()
        self.assertEqual("0v2consreg01", module.revision)
        self.assertEqual("0v2strictbind01", module.down_revision)

        events: list[tuple[str, str]] = []
        with (
            patch.object(
                module.op,
                "execute",
                side_effect=lambda sql: events.append(("execute", str(sql))),
            ),
            patch.object(
                module.op,
                "create_table",
                side_effect=lambda name, *_args: events.append(("create_table", name)),
            ),
        ):
            module.upgrade()

        rendered = "\n".join(value for kind, value in events if kind == "execute")
        self.assertEqual(
            [
                (
                    "create_table",
                    "physical_wal_v2_witness_roundtrip_attestation_consumptions",
                )
            ],
            [event for event in events if event[0] == "create_table"],
        )
        self.assertIn("LOCK TABLE physical_wal_v2_witness_roundtrip_strict_writer_commits", rendered)
        self.assertIn("physical_wal_v2_witness_roundtrip_strict_writer_bound_commits", rendered)
        self.assertIn("IN SHARE ROW EXCLUSIVE MODE", rendered)
        self.assertIn("INNER JOIN", rendered)
        self.assertIn("USING (attestation_sha256)", rendered)
        self.assertIn("Gen1/Gen2 attestation overlap exists", rendered)
        self.assertLess(
            rendered.index("LOCK TABLE"),
            rendered.index("Gen1/Gen2 attestation overlap exists"),
        )
        self.assertLess(
            rendered.index("Gen1/Gen2 attestation overlap exists"),
            rendered.index("INSERT INTO physical_wal_v2_witness_roundtrip_attestation_consumptions"),
        )
        # Two migration backfills plus the shared source-trigger claim body.
        self.assertEqual(3, rendered.count("INSERT INTO physical_wal_v2_witness_roundtrip_attestation_consumptions"))
        self.assertIn("'strict_writer_gen1'", rendered)
        self.assertIn("'strict_writer_gen2'", rendered)
        self.assertNotIn("ON CONFLICT", rendered)
        self.assertIn("CREATE FUNCTION trading_bot_v2wsrc_claim_global_attestation", rendered)
        self.assertIn("TG_TABLE_NAME", rendered)
        self.assertIn("NEW.attestation_sha256", rendered)
        self.assertIn("NEW.commit_id", rendered)
        self.assertIn("Do not catch unique_violation", rendered)

        # The stale Gen1 write path is not merely checked by an adapter: its
        # own table now claims the same registry before insert, just like Gen2.
        self.assertIn(
            "CREATE TRIGGER trg_v2wsrc_claim_global_attestation\n"
            "        BEFORE INSERT ON physical_wal_v2_witness_roundtrip_strict_writer_commits",
            rendered,
        )
        self.assertIn(
            "CREATE TRIGGER trg_v2wsrcb_claim_global_attestation\n"
            "        BEFORE INSERT ON physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
            rendered,
        )
        self.assertEqual(4, rendered.count("CREATE TRIGGER"))
        self.assertIn("trg_v2wsrc_registry_append_only_row", rendered)
        self.assertIn("trg_v2wsrc_registry_append_only_truncate", rendered)

    def test_downgrade_refuses_registry_or_either_source_generation(self) -> None:
        module = self._migration_module()
        commands: list[str] = []
        with patch.object(module.op, "execute", side_effect=commands.append), patch.object(
            module.op,
            "drop_table",
            side_effect=lambda name: commands.append("DROP_TABLE " + name),
        ):
            module.downgrade()
        rendered = "\n".join(commands)
        guard = (
            "refusing destructive V2 global attestation registry downgrade: durable registry or source rows exist"
        )
        self.assertIn(guard, rendered)
        self.assertIn(
            "EXISTS (SELECT 1 FROM physical_wal_v2_witness_roundtrip_attestation_consumptions)",
            rendered,
        )
        self.assertIn(
            "EXISTS (SELECT 1 FROM physical_wal_v2_witness_roundtrip_strict_writer_commits)",
            rendered,
        )
        self.assertIn(
            "EXISTS (SELECT 1 FROM physical_wal_v2_witness_roundtrip_strict_writer_bound_commits)",
            rendered,
        )
        self.assertLess(rendered.index(guard), rendered.index("DROP TRIGGER"))
        self.assertIn("DROP_TABLE physical_wal_v2_witness_roundtrip_attestation_consumptions", rendered)

    def test_model_and_migration_remain_schema_only(self) -> None:
        model_source = MODEL_PATH.read_text(encoding="utf-8")
        migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
        for source in (model_source, migration_source):
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
            self.assertNotIn("async def", source)
        self.assertNotIn("core", model_source)
        self.assertNotIn("commit_physical_wal_v2", migration_source)


if __name__ == "__main__":
    unittest.main()
