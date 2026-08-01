"""Static contract tests for the V1 PostgreSQL writer-admission schema.

These deliberately do not run Alembic against a database.  The foundation is
schema-only; a later adapter owns transaction execution and live rollout.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import importlib.util
import unittest
from pathlib import Path
from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)
from core import physical_operational_failover_v1_writer_admission_postgres_contract as contract


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "0writeradm01_add_operational_writer_admission_schema.py"
)


class OperationalWriterAdmissionSchemaTests(unittest.TestCase):
    binding = {
        "cluster_id": "cluster-prod-001",
        "local_site": "webapp_ir",
        "release_sha": "a" * 40,
        "generation_id": "generation-0001",
    }
    control = {
        "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
        "control_role_label": "local-admission-controller",
        "control_policy_sha256": "b" * 64,
    }
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    def test_models_expose_exact_head_and_append_only_commit_projection(self) -> None:
        head_columns = set(OperationalWriterAdmissionHead.__table__.columns.keys())
        commit_columns = set(OperationalWriterAdmissionCommit.__table__.columns.keys())

        self.assertTrue(
            {
                "id",
                "cluster_id",
                "local_site",
                "release_sha",
                "generation_id",
                "revision",
                "prior_revision",
                "highest_writer_epoch",
                "writer_epoch",
                "writer_lease_id",
                "fence_generation",
                "evidence_id",
                "revalidation_id",
                "term_expires_at",
                "state_sha256",
                "receipt_sha256",
                "current_commit_id",
                "current_commit_sha256",
                "control_boundary",
                "control_role_label",
                "control_policy_sha256",
                "committed_at",
            }.issubset(head_columns)
        )
        self.assertTrue(
            {
                "id",
                "head_id",
                "transition_kind",
                "prior_revision",
                "next_revision",
                "prior_fence_generation",
                "next_fence_generation",
                "previous_commit_sha256",
                "prior_state_sha256",
                "state_sha256",
                "receipt_sha256",
                "commit_sha256",
                "evidence_id",
                "revalidation_id",
                "term_expires_at",
                "operation_kind",
                "admitted_at",
                "control_boundary",
                "control_role_label",
                "control_policy_sha256",
                "committed_at",
            }.issubset(commit_columns)
        )

        head_constraints = {
            constraint.name
            for constraint in OperationalWriterAdmissionHead.__table__.constraints
            if constraint.name
        }
        commit_constraints = {
            constraint.name
            for constraint in OperationalWriterAdmissionCommit.__table__.constraints
            if constraint.name
        }
        self.assertTrue(
            {
                "ux_owa_heads_binding",
                "ck_owa_heads_revision_chain",
                "ck_owa_heads_control_metadata",
                "fk_owa_heads_current_commit",
            }.issubset(head_constraints)
        )
        self.assertTrue(
            {
                "ux_owa_commits_head_revision",
                "ux_owa_commits_receipt_sha256",
                "ux_owa_commits_commit_sha256",
                "ck_owa_commits_writer_operation",
                "fk_owa_commits_head_binding",
            }.issubset(commit_constraints)
        )

    def test_postgresql_model_ddl_carries_binding_replay_and_policy_guards(self) -> None:
        head_ddl = str(
            CreateTable(OperationalWriterAdmissionHead.__table__).compile(
                dialect=postgresql.dialect()
            )
        )
        commit_ddl = str(
            CreateTable(OperationalWriterAdmissionCommit.__table__).compile(
                dialect=postgresql.dialect()
            )
        )
        self.assertIn("UNIQUE (cluster_id, local_site, release_sha, generation_id)", head_ddl)
        self.assertIn("prior_revision = revision - 1", head_ddl)
        self.assertIn(OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA, head_ddl)
        self.assertIn("UNIQUE (head_id, next_revision)", commit_ddl)
        self.assertIn("UNIQUE (receipt_sha256)", commit_ddl)
        self.assertIn("UNIQUE (commit_sha256)", commit_ddl)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", commit_ddl)
        self.assertIn("operation_kind IN ('transaction_commit', 'external_effect')", commit_ddl)
        self.assertIn("prior_state_sha256 ~ '^(0{64}|[0-9a-f]{64})$'", commit_ddl)
        self.assertNotIn("?NULL", commit_ddl)

    def test_migration_is_single_head_schema_only_and_chain_hardened(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("owa_schema_migration", MIGRATION_PATH)
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.revision, "0writeradm01")
        self.assertEqual(module.down_revision, "0promauthop01")

        self.assertIn("operational_writer_admission_heads", source)
        self.assertIn("operational_writer_admission_commits", source)
        self.assertIn("fk_owa_heads_current_commit", source)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", source)
        self.assertIn("FOR UPDATE", source)
        self.assertIn("trg_owa_commits_append_only_row", source)
        self.assertIn("trg_owa_commits_append_only_truncate", source)
        self.assertIn("trg_owa_heads_current_commit_consistent", source)
        self.assertIn("current_head.highest_writer_epoch <> 0", source)
        self.assertIn("NEW.highest_writer_epoch < OLD.highest_writer_epoch", source)
        self.assertIn("successor.transition_kind = 'writer_admission'", source)
        self.assertIn("successor.transition_kind = 'witness_revalidation'", source)
        self.assertIn("refusing destructive operational writer-admission downgrade", source)

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
                "boto3",
                "botocore",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        self.assertNotIn("physical_wal_v2", source)
        self.assertNotIn("physical_full_matrix_v4", source)
        self.assertNotIn("boto3", source)
        self.assertNotIn("requests", source)

    def test_control_role_is_declared_metadata_not_host_identity(self) -> None:
        source = (REPO_ROOT / "models" / "operational_writer_admission.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("policy metadata", source)
        self.assertIn("PostgreSQL role is *not* evidence", source)
        self.assertNotIn("os.geteuid", source)
        self.assertNotIn("promotion_authorized=True", source)
        self.assertNotIn("writer_authorized=True", source)

    def test_canonical_digest_helpers_bind_full_state_receipt_and_commit_chain(self) -> None:
        state = {
            "revision": 1,
            "highest_writer_epoch": 7,
            "active_term": {
                "holder_site": "webapp_ir",
                "writer_epoch": 7,
                "writer_lease_id": "writer-lease-73",
                "evidence_id": "witness-evidence-0007",
                "revalidation_id": "revalidation-id-0007",
                "issued_at": self.now,
                "expires_at": self.now + timedelta(seconds=90),
            },
            "revalidated_runtime_instance_id": "runtime-instance-0007",
            "clock_floor": self.now,
            "fence_generation": 3,
            "fenced": False,
            "fence_reason": None,
            "requires_fresh_witness_revalidation": False,
        }
        state_digest = contract.operational_writer_admission_postgres_state_sha256_v1(
            binding=self.binding,
            state=state,
        )
        self.assertRegex(state_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            state_digest,
            contract.operational_writer_admission_postgres_state_sha256_v1(
                binding=dict(self.binding), state=dict(state)
            ),
        )
        receipt_digest = contract.operational_writer_admission_postgres_receipt_sha256_v1(
            binding=self.binding,
            transition_kind="writer_admission",
            prior_revision=0,
            prior_fence_generation=3,
            prior_state_sha256="c" * 64,
            previous_commit_sha256="d" * 64,
            next_state_sha256=state_digest,
            next_fence_generation=3,
            operation={
                "operation_kind": "transaction_commit",
                "opened_state_revision": 0,
                "fence_generation": 3,
                "evidence_id": "witness-evidence-0007",
                "writer_epoch": 7,
                "writer_lease_id": "writer-lease-73",
                "opened_at": self.now,
                "admitted_at": self.now,
            },
            control=self.control,
            committed_at=self.now,
        )
        self.assertRegex(receipt_digest, r"^[0-9a-f]{64}$")
        invalid_state = dict(
            state,
            active_term=dict(
                state["active_term"],
                writer_lease_id="writer:lease-000073",
            ),
        )
        with self.assertRaisesRegex(
            contract.OperationalWriterAdmissionPostgresContractError,
            "STATE_INVALID",
        ):
            contract.operational_writer_admission_postgres_state_sha256_v1(
                binding=self.binding,
                state=invalid_state,
            )
        with self.assertRaisesRegex(
            contract.OperationalWriterAdmissionPostgresContractError,
            "RECEIPT_INVALID",
        ):
            contract.operational_writer_admission_postgres_receipt_sha256_v1(
                binding=self.binding,
                transition_kind="writer_admission",
                prior_revision=0,
                prior_fence_generation=3,
                prior_state_sha256="c" * 64,
                previous_commit_sha256="d" * 64,
                next_state_sha256=state_digest,
                next_fence_generation=3,
                operation={
                    "operation_kind": "transaction_commit",
                    "opened_state_revision": 0,
                    "fence_generation": 3,
                    "evidence_id": "witness-evidence-0007",
                    "writer_epoch": 7,
                    "writer_lease_id": "writer:lease-000073",
                    "opened_at": self.now,
                    "admitted_at": self.now,
                },
                control=self.control,
                committed_at=self.now,
            )
        commit_digest = contract.operational_writer_admission_postgres_commit_sha256_v1(
            commit_id=UUID("11111111-1111-1111-1111-111111111111"),
            head_id=UUID("22222222-2222-2222-2222-222222222222"),
            receipt_sha256=receipt_digest,
            previous_commit_sha256="d" * 64,
            state_sha256=state_digest,
            committed_at=self.now,
        )
        self.assertRegex(commit_digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            commit_digest,
            contract.operational_writer_admission_postgres_commit_sha256_v1(
                commit_id=UUID("33333333-3333-3333-3333-333333333333"),
                head_id=UUID("22222222-2222-2222-2222-222222222222"),
                receipt_sha256=receipt_digest,
                previous_commit_sha256="d" * 64,
                state_sha256=state_digest,
                committed_at=self.now,
            ),
        )

    def test_digest_helpers_reject_unbound_metadata_and_bad_bootstrap_chain(self) -> None:
        startup = {
            "revision": 0,
            "highest_writer_epoch": 0,
            "active_term": None,
            "revalidated_runtime_instance_id": None,
            "clock_floor": None,
            "fence_generation": 0,
            "fenced": True,
            "fence_reason": "startup-requires-fresh-witness",
            "requires_fresh_witness_revalidation": True,
        }
        state_digest = contract.operational_writer_admission_postgres_state_sha256_v1(
            binding=self.binding,
            state=startup,
        )
        with self.assertRaisesRegex(
            contract.OperationalWriterAdmissionPostgresContractError,
            "RECEIPT_INVALID",
        ):
            contract.operational_writer_admission_postgres_receipt_sha256_v1(
                binding=self.binding,
                transition_kind="bootstrap",
                prior_revision=-1,
                prior_fence_generation=0,
                prior_state_sha256="a" * 64,
                previous_commit_sha256="0" * 64,
                next_state_sha256=state_digest,
                next_fence_generation=0,
                operation=None,
                control=self.control,
                committed_at=self.now,
            )
        malformed_control = dict(self.control, control_role_label="UnixRoot")
        with self.assertRaisesRegex(
            contract.OperationalWriterAdmissionPostgresContractError,
            "RECEIPT_INVALID",
        ):
            contract.operational_writer_admission_postgres_receipt_sha256_v1(
                binding=self.binding,
                transition_kind="bootstrap",
                prior_revision=-1,
                prior_fence_generation=0,
                prior_state_sha256="0" * 64,
                previous_commit_sha256="0" * 64,
                next_state_sha256=state_digest,
                next_fence_generation=0,
                operation=None,
                control=malformed_control,
                committed_at=self.now,
            )


if __name__ == "__main__":
    unittest.main()
