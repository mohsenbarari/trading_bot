"""Immutable control-plane row for the V4 Phase-1 post-effect ACK capture.

This is deliberately a successor relation, not an alteration or a nullable
extension of the Gen2 strict-writer table.  A row records a signed, FI-local,
post-effect capture prepared beside one freshly inserted Gen2 commit.  It is
not a Phase-1 receipt, does not make the capture a completion, and grants no
writer, promotion, execution, or full-matrix authority.

The exact signed Gen2 runtime receipt is retained in addition to the signed
checkpoint bytes.  The child migration locks and byte-compares it with the
referenced Gen2 row on INSERT, so every Gen2 runtime pin remains tied to the
same root transaction rather than being recreated from an old external ACK.
No cryptographic verification is delegated to PostgreSQL; the future narrow
transaction participant must validate canonical bytes, signatures, freshness,
and all cross-pins before it adds either immutable row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
)

from .database import Base
from .physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-checkpoint-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS = (
    "prepared-post-effect-strict-ack-capture-pending-external-commit"
)

_PHASE_NAME = "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix"
_PHASE_ORACLE = "normal-fi-writer-v2-witness-roundtrip-strict-ack-oracle-v1"
_TRANSPORT_PROFILE = "fi-v2-witness-roundtrip-strict-ack-v1"
_ZERO_SHA256 = "0" * 64
_SHA256 = "^[0-9a-f]{64}$"
_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_CAPTURE_IDENTIFIER = "^v4-p1-post-effect-capture-[0-9a-f]{32}$"
_CHECKPOINT_IDENTIFIER = "^v4-p1-post-effect-checkpoint-[0-9a-f]{32}$"

_NONZERO_HASHES_CHECK = " AND ".join(
    f"{column} ~ '{_SHA256}' AND {column} <> '{_ZERO_SHA256}'"
    for column in (
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
    )
)

_V4_REQUEST_CHECK = (
    f"phase_name = '{_PHASE_NAME}' "
    "AND phase_sequence = 1 "
    f"AND phase_oracle = '{_PHASE_ORACLE}' "
    f"AND transport_profile = '{_TRANSPORT_PROFILE}' "
    "AND writer_holder_site = 'webapp_fi' "
    "AND source_site = 'webapp_fi' "
    "AND destination_site = 'webapp_ir' "
    "AND writer_epoch >= 1 "
    "AND witness_sequence >= 1 "
    f"AND writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
    f"AND witness_transition_id ~ '{_IDENTIFIER}'"
)

_ANCHOR_CHECK = (
    "anchor_genesis_sequence >= 0 "
    "AND anchor_previous_sequence >= 0 "
    "AND anchor_sequence = anchor_previous_sequence + 1 "
    "AND anchor_sequence >= 1 "
    f"AND anchor_genesis_head_sha256 ~ '{_SHA256}' "
    f"AND anchor_previous_head_sha256 ~ '{_SHA256}' "
    f"AND anchor_local_previous_record_sha256 ~ '{_SHA256}'"
)

_CONTROL_FLAGS_CHECK = (
    f"schema = '{PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_SCHEMA}' "
    f"AND status = '{PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_CHECKPOINT_STATUS}' "
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
    "AND direct_ir_to_fi_control = 'forbidden'"
)

_IDENTITY_CHECK = (
    f"checkpoint_id ~ '{_CHECKPOINT_IDENTIFIER}' "
    f"AND capture_id ~ '{_CAPTURE_IDENTIFIER}' "
    f"AND claim_id ~ '{_IDENTIFIER}' "
    f"AND strict_gen2_commit_id ~ '^v2-witness-strict-writer-g2-[0-9a-f]{{64}}$' "
    f"AND strict_v2_base_commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{{64}}$' "
    f"AND strict_local_commit_record_id ~ '{_IDENTIFIER}' "
    f"AND strict_local_response_id ~ '{_IDENTIFIER}' "
    f"AND strict_attestation_consumption_id = ('v2-witness-consume-g2-' || strict_attestation_sha256) "
    "AND strict_local_commit_record_id <> strict_local_response_id "
    "AND strict_local_commit_record_id <> strict_attestation_consumption_id "
    "AND strict_local_response_id <> strict_attestation_consumption_id"
)

_STRICT_CHECK = (
    f"strict_observation_schema = '{PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA}' "
    f"AND strict_instruction_schema = '{PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA}' "
    f"AND strict_atomic_commit_boundary = '{PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY}'"
)


class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpoint(Base):
    """One append-only, non-authorizing V4 Phase-1 capture record.

    ``checkpoint_durable`` remains false inside the signed grammar even when
    this row later exists: a signer cannot know the outer transaction outcome
    before it happens.  A separate future reconciliation verifier must prove
    durable co-existence of this row and its exact Gen2 parent; no caller may
    infer Phase completion from this table alone.
    """

    __tablename__ = "physical_full_matrix_v4_p1_post_effect_strict_ack_checkpoints"
    __table_args__ = (
        CheckConstraint(_CONTROL_FLAGS_CHECK, name="ck_v4p1peack_control_flags"),
        CheckConstraint(_V4_REQUEST_CHECK, name="ck_v4p1peack_v4_request"),
        CheckConstraint(_NONZERO_HASHES_CHECK, name="ck_v4p1peack_hashes"),
        CheckConstraint(_ANCHOR_CHECK, name="ck_v4p1peack_anchor"),
        CheckConstraint(_IDENTITY_CHECK, name="ck_v4p1peack_identity"),
        CheckConstraint(_STRICT_CHECK, name="ck_v4p1peack_strict_gen2"),
        CheckConstraint(
            "octet_length(canonical_checkpoint) BETWEEN 1 AND 524288 "
            "AND octet_length(strict_canonical_runtime_commit_receipt) BETWEEN 1 AND 262144",
            name="ck_v4p1peack_bounded_bytes",
        ),
        ForeignKeyConstraint(
            ["strict_gen2_commit_id"],
            [f"{PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__tablename__}.commit_id"],
            name="fk_v4p1peack_strict_gen2_commit",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("checkpoint_id", name="ux_v4p1peack_checkpoint_id"),
        UniqueConstraint("checkpoint_sha256", name="ux_v4p1peack_checkpoint_sha"),
        UniqueConstraint(
            "journaled_effect_start_identity_sha256",
            name="ux_v4p1peack_effect_start",
        ),
        UniqueConstraint("capture_id", name="ux_v4p1peack_capture_id"),
        UniqueConstraint("strict_gen2_commit_id", name="ux_v4p1peack_gen2_commit"),
        UniqueConstraint(
            "strict_runtime_commit_receipt_sha256",
            name="ux_v4p1peack_runtime_receipt",
        ),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Signed checkpoint envelope.
    schema = Column(String(128), nullable=False)
    status = Column(String(128), nullable=False)
    checkpoint_id = Column(String(128), nullable=False)
    checkpoint_sha256 = Column(String(64), nullable=False)
    canonical_checkpoint = Column(LargeBinary, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    signer_site = Column(String(16), nullable=False)
    signer_key_id = Column(String(96), nullable=False)

    # Complete immutable V4 Phase-1 request/binding projection.
    run_id = Column(Uuid(as_uuid=True), nullable=False)
    plan_sha256 = Column(String(64), nullable=False)
    phase_name = Column(String(96), nullable=False)
    phase_sequence = Column(BigInteger, nullable=False)
    phase_oracle = Column(String(128), nullable=False)
    transport_profile = Column(String(128), nullable=False)
    effect_key = Column(String(64), nullable=False)
    phase_request_sha256 = Column(String(64), nullable=False)
    readiness_binding_sha256 = Column(String(64), nullable=False)
    route_commitment_sha256 = Column(String(64), nullable=False)
    four_role_binding_sha256 = Column(String(64), nullable=False)
    writer_holder_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    witnessed_term_proof_sha256 = Column(String(64), nullable=False)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    roundtrip_attestation_sha256 = Column(String(64), nullable=False)
    roundtrip_configuration_sha256 = Column(String(64), nullable=False)
    witness_transition_id = Column(String(128), nullable=False)
    witness_sequence = Column(BigInteger, nullable=False)

    # Exact post-journal V4 effect-start anchor, not merely a V4 request label.
    claim_id = Column(String(128), nullable=False)
    journaled_effect_start_identity_sha256 = Column(String(64), nullable=False)
    journal_binding_sha256 = Column(String(64), nullable=False)
    baseline_plan_binding_sha256 = Column(String(64), nullable=False)
    anchor_genesis_sequence = Column(BigInteger, nullable=False)
    anchor_genesis_head_sha256 = Column(String(64), nullable=False)
    anchor_previous_sequence = Column(BigInteger, nullable=False)
    anchor_previous_head_sha256 = Column(String(64), nullable=False)
    anchor_sequence = Column(BigInteger, nullable=False)
    anchor_head_sha256 = Column(String(64), nullable=False)
    anchor_commitment_sha256 = Column(String(64), nullable=False)
    anchor_attestation_sha256 = Column(String(64), nullable=False)
    anchor_local_previous_record_sha256 = Column(String(64), nullable=False)
    anchor_local_event_sha256 = Column(String(64), nullable=False)
    anchor_occurred_at = Column(DateTime(timezone=True), nullable=False)

    # One-shot local handoff binding.
    capture_id = Column(String(128), nullable=False)
    capture_handoff_sha256 = Column(String(64), nullable=False)
    capture_started_at = Column(DateTime(timezone=True), nullable=False)

    # Exact Gen2 parent identity.  The raw signed receipt gives the future
    # verifier every remaining instruction pin; the insert trigger requires
    # byte equality with the referenced Gen2 row in this root transaction.
    strict_observation_schema = Column(String(128), nullable=False)
    strict_observation_sha256 = Column(String(64), nullable=False)
    strict_runtime_commit_receipt_sha256 = Column(String(64), nullable=False)
    strict_runtime_commit_pins_sha256 = Column(String(64), nullable=False)
    strict_instruction_schema = Column(String(128), nullable=False)
    strict_configuration_sha256 = Column(String(64), nullable=False)
    strict_v2_base_configuration_sha256 = Column(String(64), nullable=False)
    strict_atomic_commit_boundary = Column(String(128), nullable=False)
    strict_gen2_commit_id = Column(String(128), nullable=False)
    strict_v2_base_commit_id = Column(String(128), nullable=False)
    strict_attestation_sha256 = Column(String(64), nullable=False)
    strict_local_commit_record_id = Column(String(128), nullable=False)
    strict_local_response_id = Column(String(128), nullable=False)
    strict_attestation_consumption_id = Column(String(128), nullable=False)
    strict_committed_at = Column(DateTime(timezone=True), nullable=False)
    strict_canonical_runtime_commit_receipt = Column(LargeBinary, nullable=False)

    # Fixed negative authority flags.  An immutable checkpoint is never a
    # state transition or a permit, including after the outer transaction.
    strict_ack_post_effect_bound = Column(Boolean, nullable=False)
    capture_handoff_verified = Column(Boolean, nullable=False)
    checkpoint_durable = Column(Boolean, nullable=False)
    phase_completion_evidenced = Column(Boolean, nullable=False)
    writer_authorized = Column(Boolean, nullable=False)
    promotion_authorized = Column(Boolean, nullable=False)
    execution_authorized = Column(Boolean, nullable=False)
    full_matrix_authorized = Column(Boolean, nullable=False)
    full_matrix_executed = Column(Boolean, nullable=False)
    direct_fi_to_ir_control = Column(String(16), nullable=False)
    direct_ir_to_fi_control = Column(String(16), nullable=False)
