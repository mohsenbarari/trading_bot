"""Static contract tests for the quarantined V4 Phase-1 checkpoint relation."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import Column, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint import (
    PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA,
    PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS,
    PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    REPO_ROOT
    / "models"
    / "physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py"
)
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "experimental"
    / "0v4p1ack01_add_v4_phase1_post_effect_strict_ack_checkpoint.py"
)


class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointSchemaTests(
    unittest.TestCase
):
    def test_experimental_migration_is_not_in_the_standard_alembic_chain(self) -> None:
        """An incomplete causal fence must not become the default upgrade head."""

        self.assertEqual("experimental", MIGRATION_PATH.parent.name)
        self.assertFalse(
            (
                REPO_ROOT
                / "migrations"
                / "versions"
                / "0v4p1ack01_add_v4_phase1_post_effect_strict_ack_checkpoint.py"
            ).exists()
        )

    def test_quarantined_p1_artifacts_are_excluded_from_generic_docker_context(self) -> None:
        """Broad Docker COPY instructions must not bypass candidate quarantine."""

        ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            {
                "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
                "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_admission.py",
                "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope.py",
                "core/physical_full_matrix_v4_phase1_same_root_coordinator_contract.py",
                "models/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
                "migrations/experimental/",
            }.issubset(set(ignored))
        )

    def test_separate_control_plane_model_retains_full_v4_anchor_and_exact_gen2_receipt(self) -> None:
        table = PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint.__table__
        self.assertEqual(
            "physical_full_matrix_v4_p1_post_effect_strict_ack_checkpoints",
            table.name,
        )
        self.assertNotEqual(
            PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__tablename__,
            table.name,
        )
        self.assertTrue(
            {
                "id",
                "schema",
                "status",
                "checkpoint_id",
                "checkpoint_sha256",
                "canonical_checkpoint",
                "captured_at",
                "signer_site",
                "signer_key_id",
                "run_id",
                "plan_sha256",
                "phase_name",
                "phase_sequence",
                "phase_oracle",
                "transport_profile",
                "effect_key",
                "phase_request_sha256",
                "readiness_binding_sha256",
                "route_commitment_sha256",
                "four_role_binding_sha256",
                "writer_holder_site",
                "writer_epoch",
                "writer_lease_id",
                "witnessed_term_proof_sha256",
                "source_site",
                "destination_site",
                "roundtrip_attestation_sha256",
                "roundtrip_configuration_sha256",
                "witness_transition_id",
                "witness_sequence",
                "claim_id",
                "journaled_effect_start_identity_sha256",
                "journal_binding_sha256",
                "baseline_plan_binding_sha256",
                "anchor_genesis_sequence",
                "anchor_genesis_head_sha256",
                "anchor_previous_sequence",
                "anchor_previous_head_sha256",
                "anchor_sequence",
                "anchor_head_sha256",
                "anchor_commitment_sha256",
                "anchor_attestation_sha256",
                "anchor_local_previous_record_sha256",
                "anchor_local_event_sha256",
                "anchor_occurred_at",
                "capture_id",
                "capture_handoff_sha256",
                "capture_started_at",
                "strict_observation_schema",
                "strict_observation_sha256",
                "strict_runtime_commit_receipt_sha256",
                "strict_runtime_commit_pins_sha256",
                "strict_instruction_schema",
                "strict_configuration_sha256",
                "strict_v2_base_configuration_sha256",
                "strict_atomic_commit_boundary",
                "strict_gen2_commit_id",
                "strict_v2_base_commit_id",
                "strict_attestation_sha256",
                "strict_local_commit_record_id",
                "strict_local_response_id",
                "strict_attestation_consumption_id",
                "strict_committed_at",
                "strict_canonical_runtime_commit_receipt",
                "strict_ack_post_effect_bound",
                "capture_handoff_verified",
                "checkpoint_durable",
                "phase_completion_evidenced",
                "writer_authorized",
                "promotion_authorized",
                "execution_authorized",
                "full_matrix_authorized",
                "full_matrix_executed",
                "direct_fi_to_ir_control",
                "direct_ir_to_fi_control",
            }.issubset(table.c.keys())
        )
        self.assertNotIn("updated_at", table.c)
        self.assertNotIn("completed_at", table.c)
        self.assertNotIn("phase_receipt", table.c)

    def test_model_requires_one_parent_one_capture_and_fixed_non_authorizing_flags(self) -> None:
        table = PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint.__table__
        unique_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            {
                "ux_v4p1peack_checkpoint_id",
                "ux_v4p1peack_checkpoint_sha",
                "ux_v4p1peack_effect_start",
                "ux_v4p1peack_capture_id",
                "ux_v4p1peack_gen2_commit",
                "ux_v4p1peack_runtime_receipt",
            },
            unique_names,
        )
        foreign_keys = {
            constraint.name: constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        self.assertEqual({"fk_v4p1peack_strict_gen2_commit"}, set(foreign_keys))
        parent_fk = foreign_keys["fk_v4p1peack_strict_gen2_commit"]
        self.assertEqual(
            ("strict_gen2_commit_id",),
            tuple(element.parent.name for element in parent_fk.elements),
        )
        self.assertEqual(
            (
                "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits.commit_id",
            ),
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
                "ck_v4p1peack_control_flags",
                "ck_v4p1peack_v4_request",
                "ck_v4p1peack_hashes",
                "ck_v4p1peack_anchor",
                "ck_v4p1peack_identity",
                "ck_v4p1peack_strict_gen2",
                "ck_v4p1peack_bounded_bytes",
            },
            set(checks),
        )
        self.assertIn(
            PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA,
            checks["ck_v4p1peack_control_flags"],
        )
        self.assertIn(
            PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS,
            checks["ck_v4p1peack_control_flags"],
        )
        self.assertIn("checkpoint_durable IS FALSE", checks["ck_v4p1peack_control_flags"])
        self.assertIn(
            "phase_completion_evidenced IS FALSE",
            checks["ck_v4p1peack_control_flags"],
        )
        self.assertIn("execution_authorized IS FALSE", checks["ck_v4p1peack_control_flags"])
        self.assertIn("full_matrix_executed IS FALSE", checks["ck_v4p1peack_control_flags"])
        self.assertIn("anchor_sequence = anchor_previous_sequence + 1", checks["ck_v4p1peack_anchor"])
        self.assertIn("strict_attestation_consumption_id", checks["ck_v4p1peack_identity"])
        self.assertIn("strict_canonical_runtime_commit_receipt", checks["ck_v4p1peack_bounded_bytes"])

    def test_postgresql_ddl_has_bounded_signed_bytes_and_restrict_gen2_parent(self) -> None:
        ddl = str(
            CreateTable(
                PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint.__table__
            ).compile(dialect=postgresql.dialect())
        )
        self.assertIn("canonical_checkpoint BYTEA NOT NULL", ddl)
        self.assertIn("strict_canonical_runtime_commit_receipt BYTEA NOT NULL", ddl)
        self.assertIn("UNIQUE (strict_gen2_commit_id)", ddl)
        self.assertIn("UNIQUE (journaled_effect_start_identity_sha256)", ddl)
        self.assertIn("UNIQUE (capture_id)", ddl)
        self.assertIn("FOREIGN KEY(strict_gen2_commit_id)", ddl)
        self.assertIn("ON DELETE RESTRICT", ddl)
        self.assertIn(
            "octet_length(canonical_checkpoint) BETWEEN 1 AND 524288",
            ddl,
        )
        self.assertIn("checkpoint_durable IS FALSE", ddl)
        self.assertIn("direct_fi_to_ir_control = 'forbidden'", ddl)

    def test_append_only_child_migration_never_alters_or_backfills_gen2(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "v4p1peack_schema_migration",
            MIGRATION_PATH,
        )
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("0v4p1ack01", module.revision)
        self.assertEqual("0v2basepin01", module.down_revision)
        self.assertEqual(1, source.count("op.create_table("))
        self.assertIn("separate immutable control-plane relation", source)
        self.assertIn("no V4 post-effect identity", source)
        self.assertNotIn(
            "ALTER TABLE physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
            source,
        )
        self.assertNotIn(
            "INSERT INTO physical_wal_v2_witness_roundtrip_strict_writer_bound_commits",
            source,
        )
        self.assertIn("FOR KEY SHARE", source)
        self.assertIn("canonical_runtime_receipt IS DISTINCT FROM", source)
        self.assertIn("trg_v4p1peack_validate_insert", source)
        self.assertIn("trg_v4p1peack_append_only_row", source)
        self.assertIn("trg_v4p1peack_append_only_truncate", source)
        guard = (
            "refusing destructive V4 Phase-1 post-effect Strict-ACK checkpoint downgrade: durable rows exist"
        )
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index("DROP TRIGGER"))

        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
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

        created_tables: list[str] = []
        created_arguments: list[tuple[object, ...]] = []
        upgrade_sql: list[str] = []
        with (
            patch.object(
                module.op,
                "create_table",
                side_effect=lambda name, *args: (
                    created_tables.append(name),
                    created_arguments.append(args),
                ),
            ),
            patch.object(module.op, "execute", side_effect=upgrade_sql.append),
        ):
            module.upgrade()
        self.assertEqual(
            ["physical_full_matrix_v4_p1_post_effect_strict_ack_checkpoints"],
            created_tables,
        )
        self.assertEqual(1, len(created_arguments))
        self.assertEqual(
            list(PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint.__table__.c.keys()),
            [item.name for item in created_arguments[0] if isinstance(item, Column)],
        )
        rendered_upgrade = "\n".join(upgrade_sql)
        self.assertEqual(3, rendered_upgrade.count("CREATE TRIGGER"))
        self.assertIn("trading_bot_v4p1peack_validate_insert", rendered_upgrade)
        self.assertIn("trading_bot_v4p1peack_reject_mutation", rendered_upgrade)
        self.assertIn("strict_canonical_runtime_commit_receipt", rendered_upgrade)

    def test_model_is_control_plane_only_and_has_no_runtime_dependency(self) -> None:
        source = MODEL_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(any(name == "core" or name.startswith("core.") for name in imports))
        self.assertIn("control-plane", source)
        self.assertNotIn("async def", source)
        self.assertNotIn("execute_phase", source)
