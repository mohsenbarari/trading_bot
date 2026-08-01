"""Static tests for the V2 Witness-roundtrip strict-writer DB foundation.

These tests do not open a database or execute an Alembic revision.  They make
the one-row local-response/attestation-consumption contract reviewable before
a future async transaction runtime is introduced.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.physical_wal_v2_witness_roundtrip_strict_writer import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterCommit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "0v2strictdb01_add_v2_witness_strict_writer_schema.py"
)
MODEL_PATH = REPO_ROOT / "models" / "physical_wal_v2_witness_roundtrip_strict_writer.py"


class PhysicalWalV2WitnessRoundtripStrictWriterSchemaTests(unittest.TestCase):
    def test_single_model_row_contains_response_consumption_and_full_v2_pins(self) -> None:
        table = PhysicalWalV2WitnessRoundtripStrictWriterCommit.__table__
        self.assertEqual(
            "physical_wal_v2_witness_roundtrip_strict_writer_commits",
            table.name,
        )
        self.assertTrue(
            {
                "id",
                "instruction_schema",
                "configuration_sha256",
                "atomic_commit_boundary",
                "commit_id",
                "attestation_sha256",
                "attestation_consumption_id",
                "ir_durable_assertion_sha256",
                "context_certificate_sha256",
                "context_sha256",
                "source_envelope_sha256",
                "source_request_sha256",
                "destination_receipt_sha256",
                "durable_ledger_entry_sha256",
                "target_recovery_evidence_sha256",
                "readback_attestation_sha256",
                "stage_receipt_sha256",
                "witness_sequence",
                "witness_ledger_entry_sha256",
                "witness_ledger_previous_head_sha256",
                "witness_ledger_binding_sha256",
                "writer_holder_site",
                "writer_epoch",
                "writer_lease_id",
                "witnessed_term_proof_sha256",
                "witness_transition_id",
                "activation_mode",
                "activation_stream_generation_id",
                "activation_route_artifact_sha256",
                "activation_source_cutover_attestation_sha256",
                "activation_receiver_permit_sha256",
                "writer_admission_commit_id",
                "writer_admission_commit_sha256",
                "local_commit_record_id",
                "local_response_id",
                "canonical_runtime_receipt",
                "runtime_commit_receipt_sha256",
                "committed_at",
            }.issubset(table.c.keys())
        )
        self.assertNotIn("updated_at", table.c)
        self.assertNotIn("status", table.c)

    def test_model_has_one_time_idempotency_replay_and_v1_admission_guards(self) -> None:
        table = PhysicalWalV2WitnessRoundtripStrictWriterCommit.__table__
        unique_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            {
                "ux_v2wsrc_commit_id",
                "ux_v2wsrc_attestation",
                "ux_v2wsrc_consumption",
                "ux_v2wsrc_local_commit",
                "ux_v2wsrc_local_response",
                "ux_v2wsrc_runtime_receipt",
                "ux_v2wsrc_owa_commit",
            },
            unique_names,
        )
        foreign_keys = {
            constraint.name: constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        self.assertEqual({"fk_v2wsrc_owa_commit"}, set(foreign_keys))
        admission_fk = foreign_keys["fk_v2wsrc_owa_commit"]
        self.assertEqual(
            ("writer_admission_commit_id",),
            tuple(element.parent.name for element in admission_fk.elements),
        )
        self.assertEqual(
            ("operational_writer_admission_commits.id",),
            tuple(element.target_fullname for element in admission_fk.elements),
        )
        self.assertEqual("RESTRICT", admission_fk.ondelete)

        checks = {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if getattr(constraint, "sqltext", None) is not None and constraint.name
        }
        self.assertEqual(
            {
                "ck_v2wsrc_instruction",
                "ck_v2wsrc_identity",
                "ck_v2wsrc_hashes",
                "ck_v2wsrc_term",
                "ck_v2wsrc_activation",
                "ck_v2wsrc_witness_sequence",
                "ck_v2wsrc_local_response",
            },
            set(checks),
        )
        self.assertIn(PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA, checks["ck_v2wsrc_instruction"])
        self.assertIn(PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY, checks["ck_v2wsrc_instruction"])
        self.assertIn("v2-witness-strict-writer", checks["ck_v2wsrc_identity"])
        self.assertIn("v2-witness-consume-", checks["ck_v2wsrc_identity"])
        self.assertIn("attestation_consumption_id", checks["ck_v2wsrc_identity"])
        self.assertIn("witnessed_term_proof_sha256", checks["ck_v2wsrc_hashes"])
        self.assertIn("normal_fi_writer", checks["ck_v2wsrc_activation"])
        self.assertIn("promoted_ir_writer", checks["ck_v2wsrc_activation"])
        self.assertIn("octet_length(canonical_runtime_receipt)", checks["ck_v2wsrc_local_response"])

    def test_postgresql_ddl_binds_one_row_identity_and_allows_only_genesis_zero_head(self) -> None:
        ddl = str(
            CreateTable(PhysicalWalV2WitnessRoundtripStrictWriterCommit.__table__).compile(
                dialect=postgresql.dialect()
            )
        )
        self.assertIn("BYTEA", ddl)
        self.assertIn("UNIQUE (commit_id)", ddl)
        self.assertIn("UNIQUE (attestation_sha256)", ddl)
        self.assertIn("UNIQUE (attestation_consumption_id)", ddl)
        self.assertIn("UNIQUE (writer_admission_commit_id)", ddl)
        self.assertIn(
            "attestation_consumption_id = ('v2-witness-consume-' || attestation_sha256)",
            ddl,
        )
        self.assertIn("witness_ledger_previous_head_sha256 ~ '^[0-9a-f]{64}$'", ddl)
        self.assertIn(
            "witness_ledger_entry_sha256 ~ '^[0-9a-f]{64}$' AND witness_ledger_entry_sha256 <> '0",
            ddl,
        )
        self.assertIn("FOREIGN KEY(writer_admission_commit_id)", ddl)
        self.assertIn("ON DELETE RESTRICT", ddl)

    def test_child_migration_is_db_only_append_only_and_v1_consistent(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("v2wsrc_schema_migration", MIGRATION_PATH)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("0v2strictdb01", module.revision)
        self.assertEqual("0writeradm01", module.down_revision)
        self.assertEqual(1, source.count("op.create_table("))
        self.assertIn("physical_wal_v2_witness_roundtrip_strict_writer_commits", source)
        self.assertIn("fk_v2wsrc_owa_commit", source)
        self.assertIn("FOR KEY SHARE", source)
        self.assertIn("admission_row.transition_kind IS DISTINCT FROM 'writer_admission'", source)
        self.assertIn("admission_row.operation_kind IS DISTINCT FROM 'transaction_commit'", source)
        self.assertIn("admission_row.writer_epoch IS DISTINCT FROM NEW.writer_epoch", source)
        self.assertIn("admission_row.writer_lease_id IS DISTINCT FROM NEW.writer_lease_id", source)
        self.assertIn("admission_row.commit_sha256 IS DISTINCT FROM NEW.writer_admission_commit_sha256", source)
        self.assertIn("trg_v2wsrc_append_only_row", source)
        self.assertIn("trg_v2wsrc_append_only_truncate", source)
        guard = "refusing destructive V2 Witness strict writer downgrade: durable rows exist"
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index("DROP TRIGGER"))

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
        self.assertNotIn("commit_physical_wal_v2", source)
        self.assertNotIn("async def", source)

        created_tables: list[str] = []
        upgrade_sql: list[str] = []
        with (
            patch.object(
                module.op,
                "create_table",
                side_effect=lambda name, *args: created_tables.append(name),
            ),
            patch.object(module.op, "execute", side_effect=upgrade_sql.append),
        ):
            module.upgrade()
        self.assertEqual(
            ["physical_wal_v2_witness_roundtrip_strict_writer_commits"],
            created_tables,
        )
        rendered_upgrade = "\n".join(upgrade_sql)
        self.assertEqual(3, rendered_upgrade.count("CREATE TRIGGER"))
        self.assertIn("trading_bot_v2wsrc_validate_insert", rendered_upgrade)
        self.assertIn("trading_bot_v2wsrc_reject_mutation", rendered_upgrade)

    def test_model_is_schema_only_and_explicitly_does_not_wire_v1_strict_runtime(self) -> None:
        source = MODEL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("core", imports)
        self.assertNotIn("asyncio", source)
        self.assertNotIn("commit_after_verified_witness_roundtrip_attestation", source)
        self.assertNotIn("async def", source)
        self.assertIn("intentionally not imported or used", source)
        self.assertIn("future explicit bridge", source)


if __name__ == "__main__":
    unittest.main()
