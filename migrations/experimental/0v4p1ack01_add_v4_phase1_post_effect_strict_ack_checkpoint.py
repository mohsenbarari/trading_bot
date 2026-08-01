"""add append-only V4 Phase-1 post-effect Strict-ACK checkpoints

Revision ID: 0v4p1ack01
Revises: 0v2basepin01

This child creates a separate immutable control-plane relation for the first
V4 phase.  It never alters, rewrites, or backfills the Gen2 strict-writer
relation: an old external ACK has no V4 post-effect identity and must not be
turned into a V4 Phase-1 result.

The future named root-transaction participant must add the Gen2 row first,
then this checkpoint row before one outer commit.  The INSERT trigger locks
the exact Gen2 parent and requires its signed runtime receipt bytes and
selected direct pins to agree.  PostgreSQL intentionally does not parse or
verify cryptographic material; canonical/signature verification remains a
pre-transaction, fail-closed responsibility of that narrow participant.

Every authority/completion flag is permanently false in this initial grammar.
In particular, the signed capture's ``checkpoint_durable`` is false because
the signer cannot know an outer commit outcome before it happens.  The later
existence of this append-only row is not a V4 phase receipt or an execution
permit; a separately owned reconciliation verifier is still required.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0v4p1ack01"
down_revision: Union[str, Sequence[str], None] = "0v2basepin01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "physical_full_matrix_v4_p1_post_effect_strict_ack_checkpoints"
_GEN2_TABLE = "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"
_SCHEMA = "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-checkpoint-v1"
_STATUS = "prepared-post-effect-strict-ack-capture-pending-external-commit"
_PHASE_NAME = "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix"
_PHASE_ORACLE = "normal-fi-writer-v2-witness-roundtrip-strict-ack-oracle-v1"
_TRANSPORT_PROFILE = "fi-v2-witness-roundtrip-strict-ack-v1"
_GEN2_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2"
_GEN2_BOUNDARY = "root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2"
_ZERO_SHA256 = "0" * 64
_SHA256 = "^[0-9a-f]{64}$"
_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_CAPTURE_IDENTIFIER = "^v4-p1-post-effect-capture-[0-9a-f]{32}$"
_CHECKPOINT_IDENTIFIER = "^v4-p1-post-effect-checkpoint-[0-9a-f]{32}$"
_MUTATION_FUNCTION = "trading_bot_v4p1peack_reject_mutation"
_INSERT_FUNCTION = "trading_bot_v4p1peack_validate_insert"


def _nonzero_sha256_checks(*columns: str) -> str:
    return " AND ".join(
        f"{column} ~ '{_SHA256}' AND {column} <> '{_ZERO_SHA256}'"
        for column in columns
    )


def upgrade() -> None:
    # This is intentionally a successor table.  Do not ALTER the Gen2 table
    # or parse/backfill historical receipt bytes: a historical ACK did not
    # carry this V4 post-effect capture identity.
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # Signed checkpoint envelope.
        sa.Column("schema", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("checkpoint_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_checkpoint", sa.LargeBinary(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signer_site", sa.String(length=16), nullable=False),
        sa.Column("signer_key_id", sa.String(length=96), nullable=False),
        # Complete V4 request/binding projection.
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("phase_name", sa.String(length=96), nullable=False),
        sa.Column("phase_sequence", sa.BigInteger(), nullable=False),
        sa.Column("phase_oracle", sa.String(length=128), nullable=False),
        sa.Column("transport_profile", sa.String(length=128), nullable=False),
        sa.Column("effect_key", sa.String(length=64), nullable=False),
        sa.Column("phase_request_sha256", sa.String(length=64), nullable=False),
        sa.Column("readiness_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("route_commitment_sha256", sa.String(length=64), nullable=False),
        sa.Column("four_role_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("writer_holder_site", sa.String(length=16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("writer_lease_id", sa.String(length=128), nullable=False),
        sa.Column("witnessed_term_proof_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_site", sa.String(length=16), nullable=False),
        sa.Column("destination_site", sa.String(length=16), nullable=False),
        sa.Column("roundtrip_attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("roundtrip_configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("witness_transition_id", sa.String(length=128), nullable=False),
        sa.Column("witness_sequence", sa.BigInteger(), nullable=False),
        # Exact V4 effect-start anchor.
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column(
            "journaled_effect_start_identity_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("journal_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("baseline_plan_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_genesis_sequence", sa.BigInteger(), nullable=False),
        sa.Column("anchor_genesis_head_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_previous_sequence", sa.BigInteger(), nullable=False),
        sa.Column("anchor_previous_head_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_sequence", sa.BigInteger(), nullable=False),
        sa.Column("anchor_head_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_commitment_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "anchor_local_previous_record_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("anchor_local_event_sha256", sa.String(length=64), nullable=False),
        sa.Column("anchor_occurred_at", sa.DateTime(timezone=True), nullable=False),
        # One-shot local capture handoff.
        sa.Column("capture_id", sa.String(length=128), nullable=False),
        sa.Column("capture_handoff_sha256", sa.String(length=64), nullable=False),
        sa.Column("capture_started_at", sa.DateTime(timezone=True), nullable=False),
        # Exact Gen2 pending-commit projection and its raw signed receipt.
        sa.Column("strict_observation_schema", sa.String(length=128), nullable=False),
        sa.Column("strict_observation_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "strict_runtime_commit_receipt_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("strict_runtime_commit_pins_sha256", sa.String(length=64), nullable=False),
        sa.Column("strict_instruction_schema", sa.String(length=128), nullable=False),
        sa.Column("strict_configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "strict_v2_base_configuration_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("strict_atomic_commit_boundary", sa.String(length=128), nullable=False),
        sa.Column("strict_gen2_commit_id", sa.String(length=128), nullable=False),
        sa.Column("strict_v2_base_commit_id", sa.String(length=128), nullable=False),
        sa.Column("strict_attestation_sha256", sa.String(length=64), nullable=False),
        sa.Column("strict_local_commit_record_id", sa.String(length=128), nullable=False),
        sa.Column("strict_local_response_id", sa.String(length=128), nullable=False),
        sa.Column(
            "strict_attestation_consumption_id", sa.String(length=128), nullable=False
        ),
        sa.Column("strict_committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "strict_canonical_runtime_commit_receipt", sa.LargeBinary(), nullable=False
        ),
        # Fixed negative authority flags.
        sa.Column("strict_ack_post_effect_bound", sa.Boolean(), nullable=False),
        sa.Column("capture_handoff_verified", sa.Boolean(), nullable=False),
        sa.Column("checkpoint_durable", sa.Boolean(), nullable=False),
        sa.Column("phase_completion_evidenced", sa.Boolean(), nullable=False),
        sa.Column("writer_authorized", sa.Boolean(), nullable=False),
        sa.Column("promotion_authorized", sa.Boolean(), nullable=False),
        sa.Column("execution_authorized", sa.Boolean(), nullable=False),
        sa.Column("full_matrix_authorized", sa.Boolean(), nullable=False),
        sa.Column("full_matrix_executed", sa.Boolean(), nullable=False),
        sa.Column("direct_fi_to_ir_control", sa.String(length=16), nullable=False),
        sa.Column("direct_ir_to_fi_control", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["strict_gen2_commit_id"],
            [f"{_GEN2_TABLE}.commit_id"],
            name="fk_v4p1peack_strict_gen2_commit",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("checkpoint_id", name="ux_v4p1peack_checkpoint_id"),
        sa.UniqueConstraint("checkpoint_sha256", name="ux_v4p1peack_checkpoint_sha"),
        sa.UniqueConstraint(
            "journaled_effect_start_identity_sha256", name="ux_v4p1peack_effect_start"
        ),
        sa.UniqueConstraint("capture_id", name="ux_v4p1peack_capture_id"),
        sa.UniqueConstraint("strict_gen2_commit_id", name="ux_v4p1peack_gen2_commit"),
        sa.UniqueConstraint(
            "strict_runtime_commit_receipt_sha256", name="ux_v4p1peack_runtime_receipt"
        ),
        sa.CheckConstraint(
            f"schema = '{_SCHEMA}' AND status = '{_STATUS}' "
            "AND signer_site = 'webapp_fi' "
            "AND strict_ack_post_effect_bound IS TRUE "
            "AND capture_handoff_verified IS TRUE "
            "AND checkpoint_durable IS FALSE "
            "AND phase_completion_evidenced IS FALSE "
            "AND writer_authorized IS FALSE "
            "AND promotion_authorized IS FALSE "
            "AND execution_authorized IS FALSE "
            "AND full_matrix_authorized IS FALSE "
            "AND full_matrix_executed IS FALSE "
            "AND direct_fi_to_ir_control = 'forbidden' "
            "AND direct_ir_to_fi_control = 'forbidden'",
            name="ck_v4p1peack_control_flags",
        ),
        sa.CheckConstraint(
            f"phase_name = '{_PHASE_NAME}' AND phase_sequence = 1 "
            f"AND phase_oracle = '{_PHASE_ORACLE}' "
            f"AND transport_profile = '{_TRANSPORT_PROFILE}' "
            "AND writer_holder_site = 'webapp_fi' "
            "AND source_site = 'webapp_fi' "
            "AND destination_site = 'webapp_ir' "
            "AND writer_epoch >= 1 AND witness_sequence >= 1 "
            f"AND writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
            f"AND witness_transition_id ~ '{_IDENTIFIER}'",
            name="ck_v4p1peack_v4_request",
        ),
        sa.CheckConstraint(
            _nonzero_sha256_checks(
                "checkpoint_sha256",
                "plan_sha256",
                "readiness_binding_sha256",
                "route_commitment_sha256",
                "four_role_binding_sha256",
                "witnessed_term_proof_sha256",
                "roundtrip_attestation_sha256",
                "roundtrip_configuration_sha256",
                "effect_key",
                "phase_request_sha256",
                "journaled_effect_start_identity_sha256",
                "journal_binding_sha256",
                "baseline_plan_binding_sha256",
                "anchor_head_sha256",
                "anchor_commitment_sha256",
                "anchor_attestation_sha256",
                "anchor_local_event_sha256",
                "capture_handoff_sha256",
                "strict_observation_sha256",
                "strict_runtime_commit_receipt_sha256",
                "strict_runtime_commit_pins_sha256",
                "strict_configuration_sha256",
                "strict_v2_base_configuration_sha256",
                "strict_attestation_sha256",
            ),
            name="ck_v4p1peack_hashes",
        ),
        sa.CheckConstraint(
            "anchor_genesis_sequence >= 0 "
            "AND anchor_previous_sequence >= 0 "
            "AND anchor_sequence = anchor_previous_sequence + 1 "
            "AND anchor_sequence >= 1 "
            f"AND anchor_genesis_head_sha256 ~ '{_SHA256}' "
            f"AND anchor_previous_head_sha256 ~ '{_SHA256}' "
            f"AND anchor_local_previous_record_sha256 ~ '{_SHA256}'",
            name="ck_v4p1peack_anchor",
        ),
        sa.CheckConstraint(
            f"checkpoint_id ~ '{_CHECKPOINT_IDENTIFIER}' "
            f"AND capture_id ~ '{_CAPTURE_IDENTIFIER}' "
            f"AND claim_id ~ '{_IDENTIFIER}' "
            "AND strict_gen2_commit_id ~ '^v2-witness-strict-writer-g2-[0-9a-f]{64}$' "
            "AND strict_v2_base_commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$' "
            f"AND strict_local_commit_record_id ~ '{_IDENTIFIER}' "
            f"AND strict_local_response_id ~ '{_IDENTIFIER}' "
            "AND strict_attestation_consumption_id = "
            "('v2-witness-consume-g2-' || strict_attestation_sha256) "
            "AND strict_local_commit_record_id <> strict_local_response_id "
            "AND strict_local_commit_record_id <> strict_attestation_consumption_id "
            "AND strict_local_response_id <> strict_attestation_consumption_id",
            name="ck_v4p1peack_identity",
        ),
        sa.CheckConstraint(
            f"strict_observation_schema = '{_GEN2_SCHEMA}' "
            f"AND strict_instruction_schema = '{_GEN2_SCHEMA}' "
            f"AND strict_atomic_commit_boundary = '{_GEN2_BOUNDARY}'",
            name="ck_v4p1peack_strict_gen2",
        ),
        sa.CheckConstraint(
            "octet_length(canonical_checkpoint) BETWEEN 1 AND 524288 "
            "AND octet_length(strict_canonical_runtime_commit_receipt) BETWEEN 1 AND 262144",
            name="ck_v4p1peack_bounded_bytes",
        ),
    )

    # FK existence alone is not enough: retain an explicit key-share lock and
    # compare the parent row while the root transaction is open.  This makes a
    # same-transaction successor fail closed if any strict Gen2 field or its
    # exact signed receipt is substituted.
    op.execute(
        f"""
        CREATE FUNCTION {_INSERT_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            strict_row {_GEN2_TABLE}%ROWTYPE;
        BEGIN
            SELECT * INTO strict_row
            FROM {_GEN2_TABLE}
            WHERE commit_id = NEW.strict_gen2_commit_id
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503',
                    MESSAGE = 'V4 Phase-1 post-effect checkpoint requires its exact Gen2 parent';
            END IF;

            IF strict_row.instruction_schema IS DISTINCT FROM NEW.strict_instruction_schema
                OR strict_row.configuration_sha256 IS DISTINCT FROM NEW.strict_configuration_sha256
                OR strict_row.v2_base_configuration_sha256 IS DISTINCT FROM NEW.strict_v2_base_configuration_sha256
                OR strict_row.atomic_commit_boundary IS DISTINCT FROM NEW.strict_atomic_commit_boundary
                OR strict_row.commit_id IS DISTINCT FROM NEW.strict_gen2_commit_id
                OR strict_row.v2_base_commit_id IS DISTINCT FROM NEW.strict_v2_base_commit_id
                OR strict_row.attestation_sha256 IS DISTINCT FROM NEW.strict_attestation_sha256
                OR strict_row.attestation_consumption_id IS DISTINCT FROM NEW.strict_attestation_consumption_id
                OR strict_row.local_commit_record_id IS DISTINCT FROM NEW.strict_local_commit_record_id
                OR strict_row.local_response_id IS DISTINCT FROM NEW.strict_local_response_id
                OR strict_row.runtime_commit_receipt_sha256 IS DISTINCT FROM NEW.strict_runtime_commit_receipt_sha256
                OR strict_row.canonical_runtime_receipt IS DISTINCT FROM NEW.strict_canonical_runtime_commit_receipt
                OR strict_row.committed_at IS DISTINCT FROM NEW.strict_committed_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'V4 Phase-1 post-effect checkpoint Gen2 strict pin mismatch';
            END IF;

            -- Cross-pin the V4 binding's writer/term/Witness facts directly
            -- to the locked Gen2 row.  This is supplementary to the exact
            -- signed receipt byte comparison, not a replacement for it.
            IF strict_row.writer_holder_site IS DISTINCT FROM NEW.writer_holder_site
                OR strict_row.writer_epoch IS DISTINCT FROM NEW.writer_epoch
                OR strict_row.writer_lease_id IS DISTINCT FROM NEW.writer_lease_id
                OR strict_row.witnessed_term_proof_sha256 IS DISTINCT FROM NEW.witnessed_term_proof_sha256
                OR strict_row.witness_transition_id IS DISTINCT FROM NEW.witness_transition_id
                OR strict_row.witness_sequence IS DISTINCT FROM NEW.witness_sequence
                OR strict_row.attestation_sha256 IS DISTINCT FROM NEW.roundtrip_attestation_sha256 THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'V4 Phase-1 post-effect checkpoint V4-to-Gen2 binding mismatch';
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
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = 'V4 Phase-1 post-effect Strict-ACK checkpoint rows are append-only';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v4p1peack_validate_insert
        BEFORE INSERT ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_INSERT_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v4p1peack_append_only_row
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_v4p1peack_append_only_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {_MUTATION_FUNCTION}();
        """
    )


def downgrade() -> None:
    # Never discard an already-captured post-effect/Gen2 correlation.  An
    # empty development database can still deliberately revert the schema.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION
                    'refusing destructive V4 Phase-1 post-effect Strict-ACK checkpoint downgrade: durable rows exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS trg_v4p1peack_append_only_truncate ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_v4p1peack_append_only_row ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_v4p1peack_validate_insert ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_MUTATION_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS {_INSERT_FUNCTION}()")
    op.drop_table(_TABLE)
