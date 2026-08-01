"""Static contract tests for the isolated Gen2 V2 strict-writer schema.

These tests neither open a database nor execute an Alembic revision.  They
review the immutable Gen2 table separately from the retained Gen1 table so a
future adapter cannot silently select an optional/unbound receipt shape.
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

from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "0v2strictbind01_add_v2_witness_bound_writer_schema.py"
)
MODEL_PATH = (
    REPO_ROOT / "models" / "physical_wal_v2_witness_roundtrip_strict_writer_bound.py"
)


class PhysicalWalV2WitnessRoundtripStrictWriterBoundSchemaTests(unittest.TestCase):
    def test_gen2_model_keeps_full_v2_pins_and_bound_parent_projection(self) -> None:
        table = PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__table__
        self.assertEqual(
            "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
            table.name,
        )
        self.assertTrue(
            {
                "id",
                "instruction_schema",
                "configuration_sha256",
                "v2_base_configuration_sha256",
                "atomic_commit_boundary",
                "commit_id",
                "v2_base_commit_id",
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
                "v1_parent_cluster_id",
                "v1_parent_local_site",
                "v1_parent_release_sha",
                "v1_parent_generation_id",
                "v1_writer_admission_commit_id",
                "v1_writer_admission_commit_sha256",
                "v1_writer_admission_receipt_sha256",
                "v1_parent_prior_revision",
                "v1_parent_next_revision",
                "v1_parent_fence_generation",
                "v1_parent_holder_site",
                "v1_parent_evidence_id",
                "v1_parent_revalidation_id",
                "v1_parent_writer_epoch",
                "v1_parent_writer_lease_id",
                "v1_parent_term_issued_at",
                "v1_parent_term_expires_at",
                "v1_parent_admitted_at",
                "v1_v2_writer_term_bridge_certificate_id",
                "v1_v2_writer_term_bridge_intent_sha256",
                "v1_v2_writer_term_bridge_certificate_sha256",
                "v1_v2_writer_term_bridge_parent_binding_sha256",
                "canonical_v1_v2_writer_term_bridge_certificate",
                "local_commit_record_id",
                "local_response_id",
                "canonical_runtime_receipt",
                "runtime_commit_receipt_sha256",
                "committed_at",
            }.issubset(table.c.keys())
        )
        self.assertNotIn("updated_at", table.c)
        self.assertNotIn("status", table.c)
        self.assertNotIn("writer_admission_commit_id", table.c)

    def test_model_requires_single_use_v1_parent_and_preissued_certificate(self) -> None:
        table = PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__table__
        unique_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            {
                "ux_v2wsrcb_commit_id",
                "ux_v2wsrcb_base_commit_id",
                "ux_v2wsrcb_attestation",
                "ux_v2wsrcb_consumption",
                "ux_v2wsrcb_local_commit",
                "ux_v2wsrcb_local_response",
                "ux_v2wsrcb_runtime_receipt",
                "ux_v2wsrcb_owa_commit",
                "ux_v2wsrcb_bridge_certificate_id",
                "ux_v2wsrcb_bridge_certificate_sha256",
                "ux_v2wsrcb_bridge_parent_binding",
            },
            unique_names,
        )
        foreign_keys = {
            constraint.name: constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        self.assertEqual({"fk_v2wsrcb_owa_commit"}, set(foreign_keys))
        parent_fk = foreign_keys["fk_v2wsrcb_owa_commit"]
        self.assertEqual(
            ("v1_writer_admission_commit_id",),
            tuple(element.parent.name for element in parent_fk.elements),
        )
        self.assertEqual(
            ("operational_writer_admission_commits.id",),
            tuple(element.target_fullname for element in parent_fk.elements),
        )
        self.assertEqual("RESTRICT", parent_fk.ondelete)

        checks = {
            constraint.name: str(constraint.sqltext)
            for constraint in table.constraints
            if getattr(constraint, "sqltext", None) is not None and constraint.name
        }
        self.assertEqual(
            {
                "ck_v2wsrcb_instruction",
                "ck_v2wsrcb_v1_parent_binding",
                "ck_v2wsrcb_identity",
                "ck_v2wsrcb_hashes",
                "ck_v2wsrcb_term",
                "ck_v2wsrcb_activation",
                "ck_v2wsrcb_witness_sequence",
                "ck_v2wsrcb_v1_parent_projection",
                "ck_v2wsrcb_local_response_bridge",
            },
            set(checks),
        )
        self.assertIn(
            PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
            checks["ck_v2wsrcb_instruction"],
        )
        self.assertIn(
            PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
            checks["ck_v2wsrcb_instruction"],
        )
        self.assertIn("v2-witness-strict-writer-g2", checks["ck_v2wsrcb_identity"])
        self.assertIn("v2-witness-consume-g2-", checks["ck_v2wsrcb_identity"])
        self.assertIn(
            "v1_v2_writer_term_bridge_parent_binding_sha256",
            checks["ck_v2wsrcb_hashes"],
        )
        self.assertIn("v1_parent_next_revision", checks["ck_v2wsrcb_v1_parent_projection"])
        self.assertIn("v1_parent_term_expires_at", checks["ck_v2wsrcb_v1_parent_projection"])
        self.assertIn(
            "canonical_v1_v2_writer_term_bridge_certificate",
            checks["ck_v2wsrcb_local_response_bridge"],
        )

    def test_postgresql_ddl_has_gen2_identity_bounded_bytes_and_restrict_parent(self) -> None:
        ddl = str(
            CreateTable(
                PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__table__
            ).compile(dialect=postgresql.dialect())
        )
        self.assertIn("BYTEA", ddl)
        self.assertIn("UNIQUE (commit_id)", ddl)
        self.assertIn("UNIQUE (v2_base_commit_id)", ddl)
        self.assertIn("UNIQUE (attestation_sha256)", ddl)
        self.assertIn("UNIQUE (v1_writer_admission_commit_id)", ddl)
        self.assertIn("UNIQUE (v1_v2_writer_term_bridge_certificate_id)", ddl)
        self.assertIn(
            "UNIQUE (v1_v2_writer_term_bridge_certificate_sha256)",
            ddl,
        )
        self.assertIn(
            "UNIQUE (v1_v2_writer_term_bridge_parent_binding_sha256)",
            ddl,
        )
        self.assertIn(
            "attestation_consumption_id = ('v2-witness-consume-g2-' || attestation_sha256)",
            ddl,
        )
        self.assertIn(
            "v2_base_commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$'",
            ddl,
        )
        self.assertIn(
            "octet_length(canonical_v1_v2_writer_term_bridge_certificate) BETWEEN 1 AND 262144",
            ddl,
        )
        self.assertIn(
            "v1_v2_writer_term_bridge_parent_binding_sha256 ~ '^[0-9a-f]{64}$'",
            ddl,
        )
        self.assertIn("FOREIGN KEY(v1_writer_admission_commit_id)", ddl)
        self.assertIn("ON DELETE RESTRICT", ddl)

    def test_child_migration_preserves_gen1_and_guards_bound_rows(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "v2wsrcb_schema_migration",
            MIGRATION_PATH,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("0v2strictbind01", module.revision)
        self.assertEqual("0v2strictdb01", module.down_revision)
        self.assertEqual(1, source.count("op.create_table("))
        self.assertIn("physical_wal_v2_witness_roundtrip_strict_writer_bound_commits", source)
        self.assertIn("preissued", source)
        self.assertIn("intent-only", source)
        self.assertIn("sealed V1 writer-admission projection alone does not", source)
        self.assertIn("FOR KEY SHARE", source)
        self.assertIn(
            "FROM operational_writer_admission_heads\n            WHERE id = admission_row.head_id\n"
            "            -- The parent receipt itself is immutable",
            source,
        )
        self.assertIn("FOR UPDATE;", source)
        self.assertIn("admission_row.operation_kind IS DISTINCT FROM 'transaction_commit'", source)
        self.assertIn(
            "admission_row.receipt_sha256 IS DISTINCT FROM NEW.v1_writer_admission_receipt_sha256",
            source,
        )
        self.assertIn(
            "admission_row.commit_sha256 IS DISTINCT FROM NEW.v1_writer_admission_commit_sha256",
            source,
        )
        self.assertIn(
            "v1_v2_writer_term_bridge_parent_binding_sha256",
            source,
        )
        self.assertIn("admission_head.current_commit_id IS DISTINCT FROM admission_row.id", source)
        self.assertIn("trg_v2wsrcb_append_only_row", source)
        self.assertIn("trg_v2wsrcb_append_only_truncate", source)
        guard = (
            "refusing destructive Gen2 V2 Witness strict writer bound downgrade: durable rows exist"
        )
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index("DROP TRIGGER"))
        self.assertNotIn("ALTER TABLE physical_wal_v2_witness_roundtrip_strict_writer_commits", source)

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
            ["physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"],
            created_tables,
        )
        rendered_upgrade = "\n".join(upgrade_sql)
        self.assertEqual(3, rendered_upgrade.count("CREATE TRIGGER"))
        self.assertIn("trading_bot_v2wsrcb_validate_insert", rendered_upgrade)
        self.assertIn("trading_bot_v2wsrcb_reject_mutation", rendered_upgrade)

    def test_model_is_schema_only_and_explicit_about_preissued_intent(self) -> None:
        source = MODEL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("core", imports)
        self.assertNotIn("asyncio", source)
        self.assertNotIn("commit_physical_wal_v2", source)
        self.assertNotIn("async def", source)
        self.assertIn("preissued", source)
        self.assertIn("intent-only", source)
        self.assertIn("does not contain that", source)
        self.assertIn("V1 projection alone does not", source)
