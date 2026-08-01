"""Durable local record for the V2 Witness-roundtrip strict writer boundary.

This is a PostgreSQL schema model only.  One immutable row represents both
halves of the future writer-only local transaction:

* the narrow local response record; and
* one consumption of the exact signed Witness attestation.

The row retains every non-secret pin accepted by the V2 strict-writer
instruction/receipt so retries can return the exact pre-existing record
instead of creating a second response.  It is deliberately not a runtime,
does not verify a Witness signature, and does not claim that an HTTP call,
Object-Storage operation, notification, or arbitrary business effect is
database-atomic.

The ``writer_admission_commit_id`` link is to the existing V1 local
writer-admission receipt.  The accompanying migration trigger verifies its
active writer term scalar fields and transaction-commit operation shape.  V1
evidence and a V2 witnessed-term proof have different contracts, so this
model intentionally does not pretend that a database comparison proves their
cryptographic equivalence; a future explicit bridge must establish that before
opening the transaction.

The older ``physical_strict_remote_ack_writer_response`` V1 file-ledger
boundary is intentionally not imported or used here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
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
from .operational_writer_admission import OperationalWriterAdmissionCommit


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA",
    "PhysicalWalV2WitnessRoundtripStrictWriterCommit",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-and-witness-attestation-consumption-v1"
)


_SHA256 = "^[0-9a-f]{64}$"
_V2_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_STREAM_GENERATION_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"

_INSTRUCTION_CHECK = (
    "instruction_schema = "
    "'gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v1' "
    "AND atomic_commit_boundary = "
    "'root-owned-atomic-local-response-and-witness-attestation-consumption-v1'"
)
_IDENTITY_CHECK = (
    "commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$' "
    "AND attestation_consumption_id = "
    "('v2-witness-consume-' || attestation_sha256)"
)
_HASHES_CHECK = " AND ".join(
    f"{column} ~ '{_SHA256}' AND {column} <> '{'0' * 64}'"
    for column in (
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
) + f" AND witness_ledger_previous_head_sha256 ~ '{_SHA256}'"
_TERM_CHECK = (
    "writer_holder_site IN ('webapp_fi', 'webapp_ir') "
    "AND writer_epoch >= 1 "
    f"AND writer_lease_id ~ '{_LEASE_IDENTIFIER}' "
    f"AND witness_transition_id ~ '{_V2_IDENTIFIER}'"
)
_ACTIVATION_CHECK = (
    "activation_mode IN ('normal_fi_writer', 'promoted_ir_writer') "
    f"AND activation_stream_generation_id ~ '{_STREAM_GENERATION_IDENTIFIER}'"
)
_LOCAL_RESPONSE_CHECK = (
    f"local_commit_record_id ~ '{_V2_IDENTIFIER}' "
    f"AND local_response_id ~ '{_V2_IDENTIFIER}' "
    f"AND attestation_consumption_id ~ '{_V2_IDENTIFIER}' "
    "AND local_commit_record_id <> local_response_id "
    "AND local_commit_record_id <> attestation_consumption_id "
    "AND local_response_id <> attestation_consumption_id "
    "AND octet_length(canonical_runtime_receipt) BETWEEN 1 AND 65536"
)


class PhysicalWalV2WitnessRoundtripStrictWriterCommit(Base):
    """One append-only local response plus one Witness-attestation consume.

    ``commit_id`` is the opaque deterministic V2 id computed by the verified
    strict-writer contract.  PostgreSQL preserves its exact identity and all
    pinned inputs, but does not duplicate that canonical cryptographic
    computation in DDL.  The one-time consumption id *is* derivable directly
    from ``attestation_sha256`` and is therefore constrained in the table.
    """

    __tablename__ = "physical_wal_v2_witness_roundtrip_strict_writer_commits"
    __table_args__ = (
        CheckConstraint(_INSTRUCTION_CHECK, name="ck_v2wsrc_instruction"),
        CheckConstraint(_IDENTITY_CHECK, name="ck_v2wsrc_identity"),
        CheckConstraint(_HASHES_CHECK, name="ck_v2wsrc_hashes"),
        CheckConstraint(_TERM_CHECK, name="ck_v2wsrc_term"),
        CheckConstraint(_ACTIVATION_CHECK, name="ck_v2wsrc_activation"),
        CheckConstraint("witness_sequence >= 1", name="ck_v2wsrc_witness_sequence"),
        CheckConstraint(_LOCAL_RESPONSE_CHECK, name="ck_v2wsrc_local_response"),
        ForeignKeyConstraint(
            ["writer_admission_commit_id"],
            [f"{OperationalWriterAdmissionCommit.__tablename__}.id"],
            name="fk_v2wsrc_owa_commit",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("commit_id", name="ux_v2wsrc_commit_id"),
        UniqueConstraint("attestation_sha256", name="ux_v2wsrc_attestation"),
        UniqueConstraint(
            "attestation_consumption_id",
            name="ux_v2wsrc_consumption",
        ),
        UniqueConstraint(
            "local_commit_record_id",
            name="ux_v2wsrc_local_commit",
        ),
        UniqueConstraint("local_response_id", name="ux_v2wsrc_local_response"),
        UniqueConstraint(
            "runtime_commit_receipt_sha256",
            name="ux_v2wsrc_runtime_receipt",
        ),
        UniqueConstraint(
            "writer_admission_commit_id",
            name="ux_v2wsrc_owa_commit",
        ),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Exact durable V2 strict-writer instruction pins (all non-secret).  The
    # per-attempt ``issued_at`` is intentionally absent: V2 excludes that
    # transient clock value from the signed runtime receipt and from the
    # pre/post exact-input comparison, so persisting it would turn an
    # idempotent retry into a false conflict.
    instruction_schema = Column(String(128), nullable=False)
    configuration_sha256 = Column(String(64), nullable=False)
    atomic_commit_boundary = Column(String(128), nullable=False)
    commit_id = Column(String(96), nullable=False)
    attestation_sha256 = Column(String(64), nullable=False)
    attestation_consumption_id = Column(String(96), nullable=False)
    ir_durable_assertion_sha256 = Column(String(64), nullable=False)
    context_certificate_sha256 = Column(String(64), nullable=False)
    context_sha256 = Column(String(64), nullable=False)
    source_envelope_sha256 = Column(String(64), nullable=False)
    source_request_sha256 = Column(String(64), nullable=False)
    destination_receipt_sha256 = Column(String(64), nullable=False)
    durable_ledger_entry_sha256 = Column(String(64), nullable=False)
    target_recovery_evidence_sha256 = Column(String(64), nullable=False)
    readback_attestation_sha256 = Column(String(64), nullable=False)
    stage_receipt_sha256 = Column(String(64), nullable=False)
    witness_sequence = Column(BigInteger, nullable=False)
    witness_ledger_entry_sha256 = Column(String(64), nullable=False)
    # The all-zero digest is a valid Witness-ledger genesis predecessor.
    witness_ledger_previous_head_sha256 = Column(String(64), nullable=False)
    witness_ledger_binding_sha256 = Column(String(64), nullable=False)

    writer_holder_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    witnessed_term_proof_sha256 = Column(String(64), nullable=False)
    witness_transition_id = Column(String(128), nullable=False)

    activation_mode = Column(String(32), nullable=False)
    activation_stream_generation_id = Column(String(128), nullable=False)
    activation_route_artifact_sha256 = Column(String(64), nullable=False)
    activation_source_cutover_attestation_sha256 = Column(String(64), nullable=False)
    activation_receiver_permit_sha256 = Column(String(64), nullable=False)

    # Existing immutable V1 writer-admission receipt, checked by the migration
    # trigger for writer/term/operation compatibility at insert time.
    writer_admission_commit_id = Column(Uuid(as_uuid=True), nullable=False)
    writer_admission_commit_sha256 = Column(String(64), nullable=False)

    # The exact signed V2 runtime receipt and its public local identities.
    local_commit_record_id = Column(String(128), nullable=False)
    local_response_id = Column(String(128), nullable=False)
    canonical_runtime_receipt = Column(LargeBinary, nullable=False)
    runtime_commit_receipt_sha256 = Column(String(64), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=False)
