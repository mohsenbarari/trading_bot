"""PostgreSQL schema models for the V1 local writer-admission CAS boundary.

The two tables in this module are deliberately *durable local control facts*,
not a writer, Witness client, traffic controller, or application integration.
They give a future transaction adapter one place to atomically:

1. lock the one exact local binding head;
2. append the next immutable transition/receipt row; and
3. advance that head only when the expected revision, fence generation, state
   digest, and previous commit digest still match.

``control_boundary`` / ``control_role_label`` / ``control_policy_sha256`` are
policy metadata.  In particular, a PostgreSQL role is *not* evidence of a Unix
uid, a root-owned service, a Witness term, or permission to start a writer.
The future host-local controller must establish those properties outside this
schema before it opens an admission transaction.

No model here opens a connection, registers an ORM event, contacts Object
Storage, communicates FI-to-IR, or changes worker/traffic state.
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
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)

from .database import Base


__all__ = (
    "OPERATIONAL_WRITER_ADMISSION_COMMIT_KINDS",
    "OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA",
    "OperationalWriterAdmissionCommit",
    "OperationalWriterAdmissionHead",
)


OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA = (
    "gold-trade-operational-writer-admission-local-transaction-adapter-v1"
)

# ``bootstrap`` persists the pure V1 fenced startup state (revision zero).
# ``runtime_restore`` is retained because the pure V1 restore operation also
# advances the durable revision/fence generation before fresh revalidation.
OPERATIONAL_WRITER_ADMISSION_COMMIT_KINDS = (
    "bootstrap",
    "runtime_restore",
    "witness_revalidation",
    "local_fence",
    "writer_admission",
)


_BINDING_CHECK = (
    "cluster_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$' "
    "AND local_site IN ('webapp_fi', 'webapp_ir') "
    "AND release_sha ~ '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' "
    "AND generation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'"
)
_DIGEST_CHECK = (
    "state_sha256 ~ '^[0-9a-f]{64}$' "
    "AND receipt_sha256 ~ '^[0-9a-f]{64}$' "
    "AND current_commit_sha256 ~ '^[0-9a-f]{64}$'"
)
_TERM_CHECK = """
(
    holder_site IS NULL
    AND writer_epoch IS NULL
    AND writer_lease_id IS NULL
    AND evidence_id IS NULL
    AND revalidation_id IS NULL
    AND term_issued_at IS NULL
    AND term_expires_at IS NULL
)
OR
(
    holder_site = local_site
    AND writer_epoch >= 1
    AND writer_epoch = highest_writer_epoch
    AND writer_lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND evidence_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND revalidation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
    AND term_issued_at IS NOT NULL
    AND term_expires_at > term_issued_at
)
"""
_FENCE_CHECK = """
(
    (fenced = true AND fence_reason ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$')
    OR (fenced = false AND fence_reason IS NULL)
)
"""
_CONTROL_METADATA_CHECK = """
control_boundary = 'gold-trade-operational-writer-admission-local-transaction-adapter-v1'
AND control_role_label ~ '^[a-z][a-z0-9-]{2,127}$'
AND control_policy_sha256 ~ '^[0-9a-f]{64}$'
"""


class OperationalWriterAdmissionHead(Base):
    """The one mutable CAS head for one exact local writer binding.

    A future adapter must only advance this row through the migration's
    commit-chain trigger in the same database transaction as the application
    work it guards.  This ORM class makes no such transaction and is not an
    authority boundary by itself.
    """

    __tablename__ = "operational_writer_admission_heads"
    __table_args__ = (
        CheckConstraint(_BINDING_CHECK, name="ck_owa_heads_binding"),
        CheckConstraint(
            "revision >= 0 AND prior_revision = revision - 1",
            name="ck_owa_heads_revision_chain",
        ),
        CheckConstraint(
            "highest_writer_epoch >= 0 AND fence_generation >= 0",
            name="ck_owa_heads_nonnegative_counters",
        ),
        CheckConstraint(_TERM_CHECK, name="ck_owa_heads_term"),
        CheckConstraint(_FENCE_CHECK, name="ck_owa_heads_fence"),
        CheckConstraint(
            "revalidated_runtime_instance_id IS NULL "
            "OR revalidated_runtime_instance_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'",
            name="ck_owa_heads_runtime_instance",
        ),
        CheckConstraint(_DIGEST_CHECK, name="ck_owa_heads_digests"),
        CheckConstraint(_CONTROL_METADATA_CHECK, name="ck_owa_heads_control_metadata"),
        UniqueConstraint(
            "cluster_id",
            "local_site",
            "release_sha",
            "generation_id",
            name="ux_owa_heads_binding",
        ),
        # Supports a composite FK from the immutable commit row, so an audit
        # row cannot silently refer to a head for a different release/site.
        UniqueConstraint(
            "id",
            "cluster_id",
            "local_site",
            "release_sha",
            "generation_id",
            name="ux_owa_heads_id_binding",
        ),
        # This mirrors the deferred circular FK added by the Alembic revision:
        # a head may only point at a receipt row owned by that same head.  The
        # companion commit FK and the trigger chain make bootstrap atomic.
        ForeignKeyConstraint(
            ["id", "current_commit_id"],
            [
                "operational_writer_admission_commits.head_id",
                "operational_writer_admission_commits.id",
            ],
            name="fk_owa_heads_current_commit",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(String(128), nullable=False)
    local_site = Column(String(16), nullable=False)
    release_sha = Column(String(64), nullable=False)
    generation_id = Column(String(128), nullable=False)

    # The persisted pure V1 state projection.
    revision = Column(BigInteger, nullable=False)
    prior_revision = Column(BigInteger, nullable=False)
    highest_writer_epoch = Column(BigInteger, nullable=False)
    holder_site = Column(String(16), nullable=True)
    writer_epoch = Column(BigInteger, nullable=True)
    writer_lease_id = Column(String(128), nullable=True)
    evidence_id = Column(String(128), nullable=True)
    revalidation_id = Column(String(128), nullable=True)
    term_issued_at = Column(DateTime(timezone=True), nullable=True)
    term_expires_at = Column(DateTime(timezone=True), nullable=True)
    revalidated_runtime_instance_id = Column(String(128), nullable=True)
    clock_floor = Column(DateTime(timezone=True), nullable=True)
    fence_generation = Column(BigInteger, nullable=False)
    fenced = Column(Boolean, nullable=False)
    fence_reason = Column(String(128), nullable=True)
    requires_fresh_witness_revalidation = Column(Boolean, nullable=False)

    # These are opaque, canonical-digest pins.  The schema does not claim it
    # can compute/verify the V1 capability state or a Witness signature.
    state_sha256 = Column(String(64), nullable=False)
    receipt_sha256 = Column(String(64), nullable=False)
    current_commit_id = Column(Uuid(as_uuid=True), nullable=False)
    current_commit_sha256 = Column(String(64), nullable=False)

    # Declarative policy metadata only; it is intentionally not a database
    # authentication/authorization assertion and never denotes Unix root.
    control_boundary = Column(String(128), nullable=False)
    control_role_label = Column(String(128), nullable=False)
    control_policy_sha256 = Column(String(64), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OperationalWriterAdmissionCommit(Base):
    """One append-only local admission state transition and receipt snapshot.

    The V1 pure state machine remains the source of semantic validation.  The
    fields duplicated here let a transaction adapter compare the immutable
    receipt to both the prior and next durable head while retaining a complete
    auditable state projection.
    """

    __tablename__ = "operational_writer_admission_commits"
    __table_args__ = (
        CheckConstraint(_BINDING_CHECK, name="ck_owa_commits_binding"),
        CheckConstraint(
            "transition_kind IN "
            "('bootstrap', 'runtime_restore', 'witness_revalidation', 'local_fence', 'writer_admission')",
            name="ck_owa_commits_transition_kind",
        ),
        CheckConstraint(
            "prior_revision >= -1 AND next_revision = prior_revision + 1",
            name="ck_owa_commits_revision_chain",
        ),
        CheckConstraint(
            "prior_fence_generation >= 0 AND next_fence_generation >= 0 "
            "AND highest_writer_epoch >= 0",
            name="ck_owa_commits_nonnegative_counters",
        ),
        CheckConstraint(_TERM_CHECK, name="ck_owa_commits_term"),
        CheckConstraint(_FENCE_CHECK, name="ck_owa_commits_fence"),
        CheckConstraint(
            "revalidated_runtime_instance_id IS NULL "
            "OR revalidated_runtime_instance_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'",
            name="ck_owa_commits_runtime_instance",
        ),
        CheckConstraint(
            # Use a capturing group here rather than ``(?:...)``: SQLAlchemy
            # treats ``:0`` in a text clause as a bind-marker candidate.
            "prior_state_sha256 ~ '^(0{64}|[0-9a-f]{64})$' "
            "AND previous_commit_sha256 ~ '^(0{64}|[0-9a-f]{64})$' "
            "AND state_sha256 ~ '^[0-9a-f]{64}$' "
            "AND receipt_sha256 ~ '^[0-9a-f]{64}$' "
            "AND commit_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_owa_commits_digests",
        ),
        CheckConstraint(_CONTROL_METADATA_CHECK, name="ck_owa_commits_control_metadata"),
        CheckConstraint(
            "(transition_kind = 'writer_admission' "
            "AND operation_kind IN ('transaction_commit', 'external_effect') "
            "AND operation_opened_state_revision >= 0 "
            "AND operation_opened_state_revision <= prior_revision "
            "AND operation_fence_generation = prior_fence_generation "
            "AND operation_evidence_id = evidence_id "
            "AND operation_writer_epoch = writer_epoch "
            "AND operation_writer_lease_id = writer_lease_id "
            "AND operation_opened_at IS NOT NULL "
            "AND admitted_at IS NOT NULL "
            "AND clock_floor = admitted_at) "
            "OR "
            "(transition_kind <> 'writer_admission' "
            "AND operation_kind IS NULL "
            "AND operation_opened_state_revision IS NULL "
            "AND operation_fence_generation IS NULL "
            "AND operation_evidence_id IS NULL "
            "AND operation_writer_epoch IS NULL "
            "AND operation_writer_lease_id IS NULL "
            "AND operation_opened_at IS NULL "
            "AND admitted_at IS NULL)",
            name="ck_owa_commits_writer_operation",
        ),
        ForeignKeyConstraint(
            [
                "head_id",
                "cluster_id",
                "local_site",
                "release_sha",
                "generation_id",
            ],
            [
                "operational_writer_admission_heads.id",
                "operational_writer_admission_heads.cluster_id",
                "operational_writer_admission_heads.local_site",
                "operational_writer_admission_heads.release_sha",
                "operational_writer_admission_heads.generation_id",
            ],
            name="fk_owa_commits_head_binding",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("head_id", "id", name="ux_owa_commits_head_id"),
        UniqueConstraint("head_id", "next_revision", name="ux_owa_commits_head_revision"),
        UniqueConstraint("receipt_sha256", name="ux_owa_commits_receipt_sha256"),
        UniqueConstraint("commit_sha256", name="ux_owa_commits_commit_sha256"),
        UniqueConstraint(
            "head_id",
            "receipt_sha256",
            name="ux_owa_commits_head_receipt_sha256",
        ),
        Index(
            "ux_owa_commits_head_revalidation_once",
            "head_id",
            "revalidation_id",
            unique=True,
            postgresql_where=text("transition_kind = 'witness_revalidation'"),
        ),
        Index(
            "ux_owa_commits_head_evidence_once",
            "head_id",
            "evidence_id",
            unique=True,
            postgresql_where=text("transition_kind = 'witness_revalidation'"),
        ),
        Index(
            "ix_owa_commits_head_committed_at",
            "head_id",
            "committed_at",
        ),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    head_id = Column(Uuid(as_uuid=True), nullable=False)
    cluster_id = Column(String(128), nullable=False)
    local_site = Column(String(16), nullable=False)
    release_sha = Column(String(64), nullable=False)
    generation_id = Column(String(128), nullable=False)
    transition_kind = Column(String(32), nullable=False)

    prior_revision = Column(BigInteger, nullable=False)
    next_revision = Column(BigInteger, nullable=False)
    prior_fence_generation = Column(BigInteger, nullable=False)
    next_fence_generation = Column(BigInteger, nullable=False)
    prior_state_sha256 = Column(String(64), nullable=False)
    previous_commit_sha256 = Column(String(64), nullable=False)

    highest_writer_epoch = Column(BigInteger, nullable=False)
    holder_site = Column(String(16), nullable=True)
    writer_epoch = Column(BigInteger, nullable=True)
    writer_lease_id = Column(String(128), nullable=True)
    evidence_id = Column(String(128), nullable=True)
    revalidation_id = Column(String(128), nullable=True)
    term_issued_at = Column(DateTime(timezone=True), nullable=True)
    term_expires_at = Column(DateTime(timezone=True), nullable=True)
    revalidated_runtime_instance_id = Column(String(128), nullable=True)
    clock_floor = Column(DateTime(timezone=True), nullable=True)
    fenced = Column(Boolean, nullable=False)
    fence_reason = Column(String(128), nullable=True)
    requires_fresh_witness_revalidation = Column(Boolean, nullable=False)

    state_sha256 = Column(String(64), nullable=False)
    receipt_sha256 = Column(String(64), nullable=False)
    commit_sha256 = Column(String(64), nullable=False)

    # Present only for a real V1 writer-admission receipt.  This is evidence
    # for a future transaction adapter; the schema does not execute an effect
    # or claim that an external effect can be made database-atomic.
    operation_kind = Column(String(32), nullable=True)
    operation_opened_state_revision = Column(BigInteger, nullable=True)
    operation_fence_generation = Column(BigInteger, nullable=True)
    operation_evidence_id = Column(String(128), nullable=True)
    operation_writer_epoch = Column(BigInteger, nullable=True)
    operation_writer_lease_id = Column(String(128), nullable=True)
    operation_opened_at = Column(DateTime(timezone=True), nullable=True)
    admitted_at = Column(DateTime(timezone=True), nullable=True)

    control_boundary = Column(String(128), nullable=False)
    control_role_label = Column(String(128), nullable=False)
    control_policy_sha256 = Column(String(64), nullable=False)
    committed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
