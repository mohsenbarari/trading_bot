"""add immutable Gen2 V2 strict-writer bridge-bound commit records

Revision ID: 0v2strictbind01
Revises: 0v2strictdb01

This schema-only child revision preserves the earlier Gen1 V2 strict-writer
table unchanged and creates a distinct Gen2 table.  A Gen2 row is the future
short local transaction's one durable response, exact Witness-attestation
consumption, V1 transaction-commit parent projection, and preissued bridge
certificate / final parent-binding digest.  It does not enable a writer,
verify any signature, contact Witness, call Object Storage, run an external
effect, or start a runtime.

The bridge certificate is intentionally preissued and intent-only: it has no
final V1 parent UUID or parent digest because external/HSM work must finish
before the local transaction.  A future adapter must verify the certificate,
its freshness and V2 intent, plus the deterministic parent-binding digest over
the certificate, V2 commit, and exact parent projection before INSERT.  This
DDL retains those bounded canonical bytes but does not add a pgcrypto
dependency or pretend SQL validates cryptographic material.

The sealed V1 writer-admission projection alone does not expose every
holder/term value retained in the Gen2 row.  The adapter may obtain those
values only from the preissued verified bridge intent, then this migration
cross-checks the complete projection against the locked V1 parent and current
head before it permits INSERT.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0v2strictbind01"
down_revision: Union[str, Sequence[str], None] = "0v2strictdb01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ZERO_SHA256 = "0" * 64
_INSTRUCTION_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2"
)
_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2"
)
_SHA256 = "^[0-9a-f]{64}$"
_V2_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_STREAM_GENERATION_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"

_TABLE = "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"
_MUTATION_FUNCTION = "trading_bot_v2wsrcb_reject_mutation"
_INSERT_FUNCTION = "trading_bot_v2wsrcb_validate_insert"


def _nonzero_sha256_checks(*columns: str) -> str:
    return " AND ".join(
        f"{column} ~ '{_SHA256}' AND {column} <> '{_ZERO_SHA256}'"
        for column in columns
    )


def upgrade() -> None:
    # Do not ALTER Gen1 into a nullable hybrid.  The new table means a future
    # Gen2 reader has no fallback path that can silently accept a receipt
    # without a cryptographically verified bridge-parent binding.
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Per-attempt issued_at is deliberately excluded: V2's signed runtime
        # receipt and exact-input comparison likewise exclude it.
        sa.Column("instruction_schema", sa.String(length=128), nullable=False),
        sa.Column("configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("atomic_commit_boundary", sa.String(length=128), nullable=False),
        sa.Column("commit_id", sa.String(length=128), nullable=False),
        sa.Column("attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("attestation_consumption_id", sa.String(length=128), nullable=False),
        sa.Column("ir_durable_assertion_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_certificate_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_envelope_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_request_sha256", sa.String(length=64), nullable=False),
        sa.Column("destination_receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("durable_ledger_entry_sha256", sa.String(length=64), nullable=False),
        sa.Column("target_recovery_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("readback_attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("stage_receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("witness_sequence", sa.BigInteger(), nullable=False),
        sa.Column("witness_ledger_entry_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "witness_ledger_previous_head_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("witness_ledger_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("writer_holder_site", sa.String(length=16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("witnessed_term_proof_sha256", sa.String(length=64), nullable=False),
        sa.Column("witness_transition_id", sa.String(length=128), nullable=False),
        sa.Column("activation_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "activation_stream_generation_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("activation_route_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "activation_source_cutover_attestation_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("activation_receiver_permit_sha256", sa.String(length=64), nullable=False),
        # Exact immutable V1 transaction-commit receipt plus the complete
        # durable Gen2 scalar projection.  The sealed V1 projection alone
        # omits some holder/term values; a future adapter takes those only
        # from the verified preissued bridge intent, then this insert trigger
        # checks every stored value against a locked parent row/current head.
        sa.Column("v1_parent_cluster_id", sa.String(length=128), nullable=False),
        sa.Column("v1_parent_local_site", sa.String(length=16), nullable=False),
        sa.Column("v1_parent_release_sha", sa.String(length=64), nullable=False),
        sa.Column("v1_parent_generation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "v1_writer_admission_commit_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "v1_writer_admission_commit_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "v1_writer_admission_receipt_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("v1_parent_prior_revision", sa.BigInteger(), nullable=False),
        sa.Column("v1_parent_next_revision", sa.BigInteger(), nullable=False),
        sa.Column("v1_parent_fence_generation", sa.BigInteger(), nullable=False),
        sa.Column("v1_parent_holder_site", sa.String(length=16), nullable=False),
        sa.Column("v1_parent_evidence_id", sa.String(length=128), nullable=False),
        sa.Column("v1_parent_revalidation_id", sa.String(length=128), nullable=False),
        sa.Column("v1_parent_writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("v1_parent_writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("v1_parent_term_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("v1_parent_term_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("v1_parent_admitted_at", sa.DateTime(timezone=True), nullable=False),
        # The certificate is preissued intent-only.  The derived binding digest
        # is the final local link to the parent / V2 commit; its cryptographic
        # calculation is verified by the future adapter, not by DDL.
        sa.Column(
            "v1_v2_writer_term_bridge_certificate_id",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "v1_v2_writer_term_bridge_intent_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "v1_v2_writer_term_bridge_certificate_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "v1_v2_writer_term_bridge_parent_binding_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "canonical_v1_v2_writer_term_bridge_certificate",
            sa.LargeBinary(),
            nullable=False,
        ),
        sa.Column("local_commit_record_id", sa.String(length=128), nullable=False),
        sa.Column("local_response_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_runtime_receipt", sa.LargeBinary(), nullable=False),
        sa.Column("runtime_commit_receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"instruction_schema = '{_INSTRUCTION_SCHEMA}' "
            f"AND atomic_commit_boundary = '{_ATOMIC_COMMIT_BOUNDARY}'",
            name="ck_v2wsrcb_instruction",
        ),
        sa.CheckConstraint(
            "v1_parent_cluster_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$' "
            "AND v1_parent_local_site IN ('webapp_fi', 'webapp_ir') "
            "AND v1_parent_release_sha ~ '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' "
            "AND v1_parent_generation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'",
            name="ck_v2wsrcb_v1_parent_binding",
        ),
        sa.CheckConstraint(
            "commit_id ~ '^v2-witness-strict-writer-g2-[0-9a-f]{64}$' "
            "AND attestation_consumption_id = "
            "('v2-witness-consume-g2-' || attestation_sha256)",
            name="ck_v2wsrcb_identity",
        ),
        sa.CheckConstraint(
            _nonzero_sha256_checks(
                "configuration_sha256",
                "attestation_sha256",
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
                "witness_ledger_entry_sha256",
                "witness_ledger_binding_sha256",
                "witnessed_term_proof_sha256",
                "activation_route_artifact_sha256",
                "activation_source_cutover_attestation_sha256",
                "activation_receiver_permit_sha256",
                "v1_writer_admission_commit_sha256",
                "v1_writer_admission_receipt_sha256",
                "v1_v2_writer_term_bridge_intent_sha256",
                "v1_v2_writer_term_bridge_certificate_sha256",
                "v1_v2_writer_term_bridge_parent_binding_sha256",
                "runtime_commit_receipt_sha256",
            )
            + f" AND witness_ledger_previous_head_sha256 ~ '{_SHA256}'",
            name="ck_v2wsrcb_hashes",
        ),
        sa.CheckConstraint(
            "writer_holder_site IN ('webapp_fi', 'webapp_ir') "
            "AND writer_epoch >= 1 "
            f"AND writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
            f"AND witness_transition_id ~ '{_V2_IDENTIFIER}'",
            name="ck_v2wsrcb_term",
        ),
        sa.CheckConstraint(
            "activation_mode IN ('normal_fi_writer', 'promoted_ir_writer') "
            f"AND activation_stream_generation_id ~ '{_STREAM_GENERATION_IDENTIFIER}'",
            name="ck_v2wsrcb_activation",
        ),
        sa.CheckConstraint("witness_sequence >= 1", name="ck_v2wsrcb_witness_sequence"),
        sa.CheckConstraint(
            "v1_parent_prior_revision >= 0 "
            "AND v1_parent_next_revision = v1_parent_prior_revision + 1 "
            "AND v1_parent_fence_generation >= 0 "
            "AND v1_parent_holder_site = v1_parent_local_site "
            "AND v1_parent_holder_site = writer_holder_site "
            "AND v1_parent_writer_epoch >= 1 "
            "AND v1_parent_writer_epoch = writer_epoch "
            f"AND v1_parent_writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
            "AND v1_parent_writer_lease_id = writer_lease_id "
            f"AND v1_parent_evidence_id ~ '{_V2_IDENTIFIER}' "
            f"AND v1_parent_revalidation_id ~ '{_V2_IDENTIFIER}' "
            "AND v1_parent_term_issued_at IS NOT NULL "
            "AND v1_parent_term_expires_at > v1_parent_term_issued_at "
            "AND v1_parent_admitted_at >= v1_parent_term_issued_at "
            "AND v1_parent_admitted_at < v1_parent_term_expires_at",
            name="ck_v2wsrcb_v1_parent_projection",
        ),
        sa.CheckConstraint(
            f"local_commit_record_id ~ '{_V2_IDENTIFIER}' "
            f"AND local_response_id ~ '{_V2_IDENTIFIER}' "
            f"AND attestation_consumption_id ~ '{_V2_IDENTIFIER}' "
            f"AND v1_v2_writer_term_bridge_certificate_id ~ '{_V2_IDENTIFIER}' "
            "AND local_commit_record_id <> local_response_id "
            "AND local_commit_record_id <> attestation_consumption_id "
            "AND local_response_id <> attestation_consumption_id "
            "AND octet_length(canonical_v1_v2_writer_term_bridge_certificate) "
            "BETWEEN 1 AND 262144 "
            "AND octet_length(canonical_runtime_receipt) BETWEEN 1 AND 262144",
            name="ck_v2wsrcb_local_response_bridge",
        ),
        sa.ForeignKeyConstraint(
            ["v1_writer_admission_commit_id"],
            ["operational_writer_admission_commits.id"],
            name="fk_v2wsrcb_owa_commit",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("commit_id", name="ux_v2wsrcb_commit_id"),
        sa.UniqueConstraint("attestation_sha256", name="ux_v2wsrcb_attestation"),
        sa.UniqueConstraint("attestation_consumption_id", name="ux_v2wsrcb_consumption"),
        sa.UniqueConstraint("local_commit_record_id", name="ux_v2wsrcb_local_commit"),
        sa.UniqueConstraint("local_response_id", name="ux_v2wsrcb_local_response"),
        sa.UniqueConstraint("runtime_commit_receipt_sha256", name="ux_v2wsrcb_runtime_receipt"),
        sa.UniqueConstraint("v1_writer_admission_commit_id", name="ux_v2wsrcb_owa_commit"),
        sa.UniqueConstraint(
            "v1_v2_writer_term_bridge_certificate_id",
            name="ux_v2wsrcb_bridge_certificate_id",
        ),
        sa.UniqueConstraint(
            "v1_v2_writer_term_bridge_certificate_sha256",
            name="ux_v2wsrcb_bridge_certificate_sha256",
        ),
        sa.UniqueConstraint(
            "v1_v2_writer_term_bridge_parent_binding_sha256",
            name="ux_v2wsrcb_bridge_parent_binding",
        ),
    )

    # The FK establishes parent existence.  This trigger locks the exact
    # immutable V1 parent and its current head, then verifies every scalar
    # projection required for a still-active transaction_commit admission.
    # It intentionally does not claim to verify the preissued bridge
    # certificate or compute the parent-binding digest; that must happen in
    # the fail-closed adapter before this short transaction.
    op.execute(
        f"""
        CREATE FUNCTION {_INSERT_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            admission_row operational_writer_admission_commits%ROWTYPE;
            admission_head operational_writer_admission_heads%ROWTYPE;
        BEGIN
            SELECT * INTO admission_row
            FROM operational_writer_admission_commits
            WHERE id = NEW.v1_writer_admission_commit_id
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'Gen2 V2 Witness strict writer commit requires an existing V1 writer-admission commit';
            END IF;

            SELECT * INTO admission_head
            FROM operational_writer_admission_heads
            WHERE id = admission_row.head_id
            -- The parent receipt itself is immutable and only needs a key
            -- share lock above.  Its mutable current head must be locked for
            -- update so a concurrent local fence cannot advance it after the
            -- active-term checks below but before this Gen2 row commits.
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'Gen2 V2 Witness strict writer parent lacks a locked current V1 admission head';
            END IF;

            IF admission_row.transition_kind IS DISTINCT FROM 'writer_admission'
                OR admission_row.operation_kind IS DISTINCT FROM 'transaction_commit'
                OR admission_row.cluster_id IS DISTINCT FROM NEW.v1_parent_cluster_id
                OR admission_row.local_site IS DISTINCT FROM NEW.v1_parent_local_site
                OR admission_row.release_sha IS DISTINCT FROM NEW.v1_parent_release_sha
                OR admission_row.generation_id IS DISTINCT FROM NEW.v1_parent_generation_id
                OR admission_row.prior_revision IS DISTINCT FROM NEW.v1_parent_prior_revision
                OR admission_row.next_revision IS DISTINCT FROM NEW.v1_parent_next_revision
                OR admission_row.next_fence_generation IS DISTINCT FROM NEW.v1_parent_fence_generation
                OR admission_row.holder_site IS DISTINCT FROM NEW.v1_parent_holder_site
                OR admission_row.evidence_id IS DISTINCT FROM NEW.v1_parent_evidence_id
                OR admission_row.revalidation_id IS DISTINCT FROM NEW.v1_parent_revalidation_id
                OR admission_row.writer_epoch IS DISTINCT FROM NEW.v1_parent_writer_epoch
                OR admission_row.writer_lease_id IS DISTINCT FROM NEW.v1_parent_writer_lease_id
                OR admission_row.term_issued_at IS DISTINCT FROM NEW.v1_parent_term_issued_at
                OR admission_row.term_expires_at IS DISTINCT FROM NEW.v1_parent_term_expires_at
                OR admission_row.admitted_at IS DISTINCT FROM NEW.v1_parent_admitted_at
                OR admission_row.commit_sha256 IS DISTINCT FROM NEW.v1_writer_admission_commit_sha256
                OR admission_row.receipt_sha256 IS DISTINCT FROM NEW.v1_writer_admission_receipt_sha256
                OR admission_row.local_site IS DISTINCT FROM NEW.writer_holder_site
                OR admission_row.holder_site IS DISTINCT FROM NEW.writer_holder_site
                OR admission_row.writer_epoch IS DISTINCT FROM NEW.writer_epoch
                OR admission_row.writer_lease_id IS DISTINCT FROM NEW.writer_lease_id
                OR admission_row.operation_opened_state_revision IS NULL
                OR admission_row.operation_opened_state_revision > admission_row.prior_revision
                OR admission_row.operation_fence_generation IS DISTINCT FROM admission_row.prior_fence_generation
                OR admission_row.operation_evidence_id IS DISTINCT FROM admission_row.evidence_id
                OR admission_row.operation_writer_epoch IS DISTINCT FROM admission_row.writer_epoch
                OR admission_row.operation_writer_lease_id IS DISTINCT FROM admission_row.writer_lease_id
                OR admission_row.operation_opened_at IS NULL
                OR admission_row.fenced IS NOT FALSE
                OR admission_row.requires_fresh_witness_revalidation IS NOT FALSE
                OR admission_row.term_issued_at IS NULL
                OR admission_row.term_expires_at IS NULL
                OR admission_row.admitted_at IS NULL
                OR NEW.committed_at < admission_row.committed_at
                OR NEW.committed_at < admission_row.admitted_at
                OR NEW.committed_at < admission_row.term_issued_at
                OR NEW.committed_at >= admission_row.term_expires_at
                OR admission_head.current_commit_id IS DISTINCT FROM admission_row.id
                OR admission_head.current_commit_sha256 IS DISTINCT FROM admission_row.commit_sha256
                OR admission_head.cluster_id IS DISTINCT FROM admission_row.cluster_id
                OR admission_head.local_site IS DISTINCT FROM admission_row.local_site
                OR admission_head.release_sha IS DISTINCT FROM admission_row.release_sha
                OR admission_head.generation_id IS DISTINCT FROM admission_row.generation_id
                OR admission_head.revision IS DISTINCT FROM admission_row.next_revision
                OR admission_head.fence_generation IS DISTINCT FROM admission_row.next_fence_generation
                OR admission_head.holder_site IS DISTINCT FROM admission_row.holder_site
                OR admission_head.writer_epoch IS DISTINCT FROM admission_row.writer_epoch
                OR admission_head.writer_lease_id IS DISTINCT FROM admission_row.writer_lease_id
                OR admission_head.evidence_id IS DISTINCT FROM admission_row.evidence_id
                OR admission_head.revalidation_id IS DISTINCT FROM admission_row.revalidation_id
                OR admission_head.term_issued_at IS DISTINCT FROM admission_row.term_issued_at
                OR admission_head.term_expires_at IS DISTINCT FROM admission_row.term_expires_at
                OR admission_head.fenced IS NOT FALSE
                OR admission_head.requires_fresh_witness_revalidation IS NOT FALSE THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'Gen2 V2 Witness strict writer commit is inconsistent with its active V1 transaction-commit admission';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_MUTATION_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'Gen2 V2 Witness strict writer bound commit rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrcb_validate_insert
        BEFORE INSERT ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_INSERT_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrcb_append_only_row
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrcb_append_only_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )


def downgrade() -> None:
    # A downgrade must not discard a durable local response, attestation
    # consumption, parent projection, or bridge binding.  Empty environments
    # may still deliberately roll the schema back before any campaign use.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'refusing destructive Gen2 V2 Witness strict writer bound downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS trg_v2wsrcb_append_only_truncate ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_v2wsrcb_append_only_row ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_v2wsrcb_validate_insert ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_MUTATION_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_INSERT_FUNCTION}()")
    op.drop_table(_TABLE)
