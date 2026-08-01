"""add durable V2 Witness-roundtrip strict-writer local commit records

Revision ID: 0v2strictdb01
Revises: 0writeradm01

This revision is schema-only.  It creates one append-only local row for the
future V2 Witness-roundtrip strict-writer transaction.  That row is both the
durable local response record and the one-time consumption of the exact
Witness attestation.  It neither enables a writer nor verifies signatures,
contacts a peer, reaches Object Storage, runs an external effect, starts a
worker, or makes any application runtime call.

The row links to the immutable V1 local writer-admission commit.  The insert
trigger proves the V1 record is a transaction-commit writer admission for the
same local site / epoch / lease and is still within its recorded term.  It
does not claim that a V1 evidence id and a V2 witnessed-term proof are the
same cryptographic assertion; a future explicit bridge must prove that before
it opens the local transaction.

The old V1 file-ledger strict remote-ack boundary is intentionally not wired
by this migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0v2strictdb01"
down_revision: Union[str, Sequence[str], None] = "0writeradm01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ZERO_SHA256 = "0" * 64
_INSTRUCTION_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v1"
)
_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-and-witness-attestation-consumption-v1"
)
_SHA256 = "^[0-9a-f]{64}$"
_V2_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_STREAM_GENERATION_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"

_MUTATION_FUNCTION = "trading_bot_v2wsrc_reject_mutation"
_INSERT_FUNCTION = "trading_bot_v2wsrc_validate_insert"


def _nonzero_sha256_checks(*columns: str) -> str:
    return " AND ".join(
        f"{column} ~ '{_SHA256}' AND {column} <> '{_ZERO_SHA256}'"
        for column in columns
    )


def upgrade() -> None:
    # This is intentionally one table rather than a response ledger plus a
    # separate consumption ledger.  One committed row is the sole local
    # source of truth for both facts, so an idempotent retry cannot durably
    # create one without the other.
    op.create_table(
        "physical_wal_v2_witness_roundtrip_strict_writer_commits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # V2's per-attempt issued_at is deliberately excluded: it is not in
        # the signed runtime receipt nor V2's exact pre/post input comparison.
        sa.Column("instruction_schema", sa.String(length=128), nullable=False),
        sa.Column("configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("atomic_commit_boundary", sa.String(length=128), nullable=False),
        sa.Column("commit_id", sa.String(length=96), nullable=False),
        sa.Column("attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("attestation_consumption_id", sa.String(length=96), nullable=False),
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
        sa.Column(
            "writer_admission_commit_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "writer_admission_commit_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("local_commit_record_id", sa.String(length=128), nullable=False),
        sa.Column("local_response_id", sa.String(length=128), nullable=False),
        sa.Column("canonical_runtime_receipt", sa.LargeBinary(), nullable=False),
        sa.Column(
            "runtime_commit_receipt_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"instruction_schema = '{_INSTRUCTION_SCHEMA}' "
            f"AND atomic_commit_boundary = '{_ATOMIC_COMMIT_BOUNDARY}'",
            name="ck_v2wsrc_instruction",
        ),
        sa.CheckConstraint(
            "commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$' "
            "AND attestation_consumption_id = "
            "('v2-witness-consume-' || attestation_sha256)",
            name="ck_v2wsrc_identity",
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
                "writer_admission_commit_sha256",
                "runtime_commit_receipt_sha256",
            )
            + f" AND witness_ledger_previous_head_sha256 ~ '{_SHA256}'",
            name="ck_v2wsrc_hashes",
        ),
        sa.CheckConstraint(
            "writer_holder_site IN ('webapp_fi', 'webapp_ir') "
            "AND writer_epoch >= 1 "
            f"AND writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
            f"AND witness_transition_id ~ '{_V2_IDENTIFIER}'",
            name="ck_v2wsrc_term",
        ),
        sa.CheckConstraint(
            "activation_mode IN ('normal_fi_writer', 'promoted_ir_writer') "
            f"AND activation_stream_generation_id ~ '{_STREAM_GENERATION_IDENTIFIER}'",
            name="ck_v2wsrc_activation",
        ),
        sa.CheckConstraint(
            "witness_sequence >= 1",
            name="ck_v2wsrc_witness_sequence",
        ),
        sa.CheckConstraint(
            f"local_commit_record_id ~ '{_V2_IDENTIFIER}' "
            f"AND local_response_id ~ '{_V2_IDENTIFIER}' "
            f"AND attestation_consumption_id ~ '{_V2_IDENTIFIER}' "
            "AND local_commit_record_id <> local_response_id "
            "AND local_commit_record_id <> attestation_consumption_id "
            "AND local_response_id <> attestation_consumption_id "
            "AND octet_length(canonical_runtime_receipt) BETWEEN 1 AND 65536",
            name="ck_v2wsrc_local_response",
        ),
        sa.ForeignKeyConstraint(
            ["writer_admission_commit_id"],
            ["operational_writer_admission_commits.id"],
            name="fk_v2wsrc_owa_commit",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("commit_id", name="ux_v2wsrc_commit_id"),
        sa.UniqueConstraint("attestation_sha256", name="ux_v2wsrc_attestation"),
        sa.UniqueConstraint("attestation_consumption_id", name="ux_v2wsrc_consumption"),
        sa.UniqueConstraint("local_commit_record_id", name="ux_v2wsrc_local_commit"),
        sa.UniqueConstraint("local_response_id", name="ux_v2wsrc_local_response"),
        sa.UniqueConstraint(
            "runtime_commit_receipt_sha256",
            name="ux_v2wsrc_runtime_receipt",
        ),
        sa.UniqueConstraint(
            "writer_admission_commit_id",
            name="ux_v2wsrc_owa_commit",
        ),
    )

    # The FK establishes parent existence.  This trigger adds the exact V1
    # writer-admission shape that a foreign key cannot express.  It locks the
    # immutable parent row so a future local transaction cannot attach a
    # strict V2 response to a stale or non-writer V1 receipt.
    op.execute(
        f"""
        CREATE FUNCTION {_INSERT_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            admission_row operational_writer_admission_commits%ROWTYPE;
        BEGIN
            SELECT * INTO admission_row
            FROM operational_writer_admission_commits
            WHERE id = NEW.writer_admission_commit_id
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'V2 Witness strict writer commit requires an existing writer-admission commit';
            END IF;

            IF admission_row.transition_kind IS DISTINCT FROM 'writer_admission'
                OR admission_row.operation_kind IS DISTINCT FROM 'transaction_commit'
                OR admission_row.local_site IS DISTINCT FROM NEW.writer_holder_site
                OR admission_row.holder_site IS DISTINCT FROM NEW.writer_holder_site
                OR admission_row.writer_epoch IS DISTINCT FROM NEW.writer_epoch
                OR admission_row.writer_lease_id IS DISTINCT FROM NEW.writer_lease_id
                OR admission_row.operation_writer_epoch IS DISTINCT FROM NEW.writer_epoch
                OR admission_row.operation_writer_lease_id IS DISTINCT FROM NEW.writer_lease_id
                OR admission_row.operation_evidence_id IS DISTINCT FROM admission_row.evidence_id
                OR admission_row.commit_sha256 IS DISTINCT FROM NEW.writer_admission_commit_sha256
                OR admission_row.fenced IS NOT FALSE
                OR admission_row.requires_fresh_witness_revalidation IS NOT FALSE
                OR admission_row.term_issued_at IS NULL
                OR admission_row.term_expires_at IS NULL
                OR admission_row.admitted_at IS NULL
                OR NEW.committed_at < admission_row.admitted_at
                OR NEW.committed_at < admission_row.term_issued_at
                OR NEW.committed_at >= admission_row.term_expires_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'V2 Witness strict writer commit is inconsistent with its active V1 writer admission';
            END IF;

            -- The V1 evidence id and V2 witnessed_term_proof_sha256 are not
            -- interchangeable fields.  A future runtime must validate their
            -- explicit bridge before this insert; the database only preserves
            -- the exact V2 proof and compatible V1 scalar term here.
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
                MESSAGE = 'V2 Witness strict writer commit rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrc_validate_insert
        BEFORE INSERT ON physical_wal_v2_witness_roundtrip_strict_writer_commits
        FOR EACH ROW EXECUTE FUNCTION {_INSERT_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrc_append_only_row
        BEFORE UPDATE OR DELETE ON physical_wal_v2_witness_roundtrip_strict_writer_commits
        FOR EACH ROW EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v2wsrc_append_only_truncate
        BEFORE TRUNCATE ON physical_wal_v2_witness_roundtrip_strict_writer_commits
        FOR EACH STATEMENT EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )


def downgrade() -> None:
    # A downgrade must not silently discard an attestation consumption or the
    # local response it made durable.  An empty-environment downgrade remains
    # possible for a deliberate pre-rollout reset.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM physical_wal_v2_witness_roundtrip_strict_writer_commits
            ) THEN
                RAISE EXCEPTION
                    'refusing destructive V2 Witness strict writer downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v2wsrc_append_only_truncate "
        "ON physical_wal_v2_witness_roundtrip_strict_writer_commits"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v2wsrc_append_only_row "
        "ON physical_wal_v2_witness_roundtrip_strict_writer_commits"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_v2wsrc_validate_insert "
        "ON physical_wal_v2_witness_roundtrip_strict_writer_commits"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_MUTATION_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_INSERT_FUNCTION}()")
    op.drop_table("physical_wal_v2_witness_roundtrip_strict_writer_commits")
