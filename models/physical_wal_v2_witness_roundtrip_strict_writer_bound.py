"""Durable Gen2 record for the V2 Witness-roundtrip strict-writer boundary.

This PostgreSQL schema model deliberately leaves the immutable Gen1
``physical_wal_v2_witness_roundtrip_strict_writer_commits`` table untouched.
One Gen2 row is the future local atomic boundary for all of the following
non-secret, already-verified facts:

* a narrow local response record;
* exactly one V2 Witness-attestation consumption; and
* the V1 transaction-commit parent plus a preissued, independently verified
  V1--V2 writer-term bridge certificate and its final parent-binding digest.

The preissued bridge certificate is intentionally intent-only: it contains no
final V1 parent UUID or hash, because it is obtained before the short local
transaction.  The deterministic parent-binding digest joins that certificate,
the exact V2 commit, and the actual V1 parent projection.  The canonical
bridge certificate and V2 runtime receipt are retained as bounded bytes so a
later forensic reader can verify the exact signed objects.  The schema cannot
verify their hashes or signatures (and does not add a ``pgcrypto`` dependency);
a future fail-closed adapter must verify their canonical encoding, digest,
signatures, freshness, intent, and parent-binding cross-pins before it opens
the local transaction.  This module is not a runtime, does not contact
Witness or Object Storage, and does not authorize a writer.

The follow-on immutable base-pin revision also persists the exact Gen1
prepared V2 configuration digest and deterministic base commit id from which
the Gen2 commit was derived.  Signed bytes remain forensic evidence; these
direct columns are required for exact durable reconciliation.
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
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2"
)


_SHA256 = "^[0-9a-f]{64}$"
_V2_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
_LEASE_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_STREAM_GENERATION_IDENTIFIER = "^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
_BINDING_CHECK = (
    "v1_parent_cluster_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$' "
    "AND v1_parent_local_site IN ('webapp_fi', 'webapp_ir') "
    "AND v1_parent_release_sha ~ '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' "
    "AND v1_parent_generation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'"
)
_IDENTITY_CHECK = (
    "commit_id ~ '^v2-witness-strict-writer-g2-[0-9a-f]{64}$' "
    "AND v2_base_commit_id ~ '^v2-witness-strict-writer-[0-9a-f]{64}$' "
    "AND attestation_consumption_id = "
    "('v2-witness-consume-g2-' || attestation_sha256)"
)
_HASHES_CHECK = " AND ".join(
    f"{column} ~ '{_SHA256}' AND {column} <> '{'0' * 64}'"
    for column in (
        "configuration_sha256",
        "v2_base_configuration_sha256",
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
_V1_PARENT_PROJECTION_CHECK = (
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
    "AND v1_parent_admitted_at < v1_parent_term_expires_at"
)
_LOCAL_RESPONSE_AND_BRIDGE_CHECK = (
    f"local_commit_record_id ~ '{_V2_IDENTIFIER}' "
    f"AND local_response_id ~ '{_V2_IDENTIFIER}' "
    f"AND attestation_consumption_id ~ '{_V2_IDENTIFIER}' "
    f"AND v1_v2_writer_term_bridge_certificate_id ~ '{_V2_IDENTIFIER}' "
    "AND local_commit_record_id <> local_response_id "
    "AND local_commit_record_id <> attestation_consumption_id "
    "AND local_response_id <> attestation_consumption_id "
    "AND octet_length(canonical_v1_v2_writer_term_bridge_certificate) "
    "BETWEEN 1 AND 262144 "
    "AND octet_length(canonical_runtime_receipt) BETWEEN 1 AND 262144"
)


class PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit(Base):
    """One immutable Gen2 local response / consumption / bridge binding.

    The V1 parent foreign key and migration trigger retain a complete durable
    scalar projection of the exact local writer-admission commit.  The sealed
    V1 projection alone does not carry every holder/term value, so the future
    bridge verifier must source those values from the preissued verified bridge
    intent, cross-check them against the locked parent, and establish the
    deterministic parent-binding digest before insertion;
    callers must not treat merely constructing this ORM object as admission.
    """

    __tablename__ = "physical_wal_v2_witness_roundtrip_strict_writer_bound_commits"
    __table_args__ = (
        CheckConstraint(
            "instruction_schema = "
            "'gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v2' "
            "AND atomic_commit_boundary = "
            "'root-owned-atomic-local-response-attestation-and-v1-v2-bridge-binding-v2'",
            name="ck_v2wsrcb_instruction",
        ),
        CheckConstraint(_BINDING_CHECK, name="ck_v2wsrcb_v1_parent_binding"),
        CheckConstraint(_IDENTITY_CHECK, name="ck_v2wsrcb_identity"),
        CheckConstraint(_HASHES_CHECK, name="ck_v2wsrcb_hashes"),
        CheckConstraint(_TERM_CHECK, name="ck_v2wsrcb_term"),
        CheckConstraint(_ACTIVATION_CHECK, name="ck_v2wsrcb_activation"),
        CheckConstraint(
            "witness_sequence >= 1",
            name="ck_v2wsrcb_witness_sequence",
        ),
        CheckConstraint(
            _V1_PARENT_PROJECTION_CHECK,
            name="ck_v2wsrcb_v1_parent_projection",
        ),
        CheckConstraint(
            _LOCAL_RESPONSE_AND_BRIDGE_CHECK,
            name="ck_v2wsrcb_local_response_bridge",
        ),
        ForeignKeyConstraint(
            ["v1_writer_admission_commit_id"],
            [f"{OperationalWriterAdmissionCommit.__tablename__}.id"],
            name="fk_v2wsrcb_owa_commit",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("commit_id", name="ux_v2wsrcb_commit_id"),
        UniqueConstraint(
            "v2_base_commit_id",
            name="ux_v2wsrcb_base_commit_id",
        ),
        UniqueConstraint("attestation_sha256", name="ux_v2wsrcb_attestation"),
        UniqueConstraint(
            "attestation_consumption_id",
            name="ux_v2wsrcb_consumption",
        ),
        UniqueConstraint(
            "local_commit_record_id",
            name="ux_v2wsrcb_local_commit",
        ),
        UniqueConstraint("local_response_id", name="ux_v2wsrcb_local_response"),
        UniqueConstraint(
            "runtime_commit_receipt_sha256",
            name="ux_v2wsrcb_runtime_receipt",
        ),
        UniqueConstraint(
            "v1_writer_admission_commit_id",
            name="ux_v2wsrcb_owa_commit",
        ),
        UniqueConstraint(
            "v1_v2_writer_term_bridge_certificate_id",
            name="ux_v2wsrcb_bridge_certificate_id",
        ),
        UniqueConstraint(
            "v1_v2_writer_term_bridge_certificate_sha256",
            name="ux_v2wsrcb_bridge_certificate_sha256",
        ),
        UniqueConstraint(
            "v1_v2_writer_term_bridge_parent_binding_sha256",
            name="ux_v2wsrcb_bridge_parent_binding",
        ),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Exact non-secret V2 Gen2 instruction pins.  Per-attempt issued_at is
    # intentionally excluded because the signed V2 receipt excludes it too.
    instruction_schema = Column(String(128), nullable=False)
    configuration_sha256 = Column(String(64), nullable=False)
    # These two opaque-prepare pins are distinct from the Gen2 configuration
    # and commit id; they must be durable queryable columns, not just receipt
    # bytes that a reconciliation path would have to parse after the fact.
    v2_base_configuration_sha256 = Column(String(64), nullable=False)
    atomic_commit_boundary = Column(String(128), nullable=False)
    commit_id = Column(String(128), nullable=False)
    v2_base_commit_id = Column(String(128), nullable=False)
    attestation_sha256 = Column(String(64), nullable=False)
    attestation_consumption_id = Column(String(128), nullable=False)
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
    # An all-zero predecessor remains valid for the Witness-ledger genesis.
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

    # Complete durable Gen2 scalar projection of the exact V1
    # transaction-commit parent.  The sealed V1 projection does not itself
    # carry every holder/term value; the adapter obtains those only from the
    # preissued verified bridge intent and the migration trigger cross-checks
    # every value against the locked immutable V1 record at insertion time.
    v1_parent_cluster_id = Column(String(128), nullable=False)
    v1_parent_local_site = Column(String(16), nullable=False)
    v1_parent_release_sha = Column(String(64), nullable=False)
    v1_parent_generation_id = Column(String(128), nullable=False)
    v1_writer_admission_commit_id = Column(Uuid(as_uuid=True), nullable=False)
    v1_writer_admission_commit_sha256 = Column(String(64), nullable=False)
    v1_writer_admission_receipt_sha256 = Column(String(64), nullable=False)
    v1_parent_prior_revision = Column(BigInteger, nullable=False)
    v1_parent_next_revision = Column(BigInteger, nullable=False)
    v1_parent_fence_generation = Column(BigInteger, nullable=False)
    v1_parent_holder_site = Column(String(16), nullable=False)
    v1_parent_evidence_id = Column(String(128), nullable=False)
    v1_parent_revalidation_id = Column(String(128), nullable=False)
    v1_parent_writer_epoch = Column(BigInteger, nullable=False)
    v1_parent_writer_lease_id = Column(String(128), nullable=False)
    v1_parent_term_issued_at = Column(DateTime(timezone=True), nullable=False)
    v1_parent_term_expires_at = Column(DateTime(timezone=True), nullable=False)
    v1_parent_admitted_at = Column(DateTime(timezone=True), nullable=False)

    # The adapter has already verified this independent, preissued intent
    # certificate, including its canonical byte representation, digest,
    # signature, freshness, and V2-instruction pins.  It also verifies the
    # deterministic parent-binding digest below against this actual V1 parent
    # projection; the certificate itself deliberately does not contain that
    # final parent.
    v1_v2_writer_term_bridge_certificate_id = Column(String(128), nullable=False)
    v1_v2_writer_term_bridge_intent_sha256 = Column(String(64), nullable=False)
    v1_v2_writer_term_bridge_certificate_sha256 = Column(String(64), nullable=False)
    v1_v2_writer_term_bridge_parent_binding_sha256 = Column(
        String(64),
        nullable=False,
    )
    canonical_v1_v2_writer_term_bridge_certificate = Column(
        LargeBinary,
        nullable=False,
    )

    # The exact signed V2 Gen2 runtime receipt and public local identities.
    local_commit_record_id = Column(String(128), nullable=False)
    local_response_id = Column(String(128), nullable=False)
    canonical_runtime_receipt = Column(LargeBinary, nullable=False)
    runtime_commit_receipt_sha256 = Column(String(64), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=False)
