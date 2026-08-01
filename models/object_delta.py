"""Durable schema primitives for a future Object Storage delta data plane.

These models intentionally contain no publisher, receiver, background worker,
or Object Storage integration.  They make the transaction boundaries required
by ``core.object_delta_import_plan`` representable in PostgreSQL while the
existing peer HTTP sync path remains unchanged.

The source allocator must lock one ``ObjectDeltaStream`` row, allocate its
``next_sequence``, insert the matching ``ObjectDeltaOutboxEntry``, and advance
the counter in the same transaction as the authoritative change and its
``ChangeLog`` evidence.  A later, separate adapter will be responsible for
turning those durable rows into authenticated encrypted Objects.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from .database import Base


_WEBAPP_SITE_CHECK = "source_site IN ('webapp_fi', 'webapp_ir') AND destination_site IN ('webapp_fi', 'webapp_ir') AND source_site <> destination_site"


class ObjectDeltaStream(Base):
    """A source-side logical sequence allocator scoped to one stream generation."""

    __tablename__ = "object_delta_streams"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_object_delta_streams_sites"),
        CheckConstraint("char_length(campaign_id) BETWEEN 8 AND 128", name="ck_object_delta_streams_campaign_id"),
        CheckConstraint("char_length(release_sha) = 40", name="ck_object_delta_streams_release_sha"),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_object_delta_streams_generation_id",
        ),
        CheckConstraint("next_sequence >= 1", name="ck_object_delta_streams_next_sequence"),
        UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            name="ux_object_delta_streams_identity",
        ),
        # PostgreSQL requires an exact candidate key for the composite
        # cutover FK below.  Keeping the stream id in that key makes the
        # duplicated identity an enforced match rather than an application
        # convention.
        UniqueConstraint(
            "id",
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            name="ux_object_delta_streams_id_identity",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    # This is the next logical sequence to reserve, never a ChangeLog ID.
    next_sequence = Column(BigInteger, nullable=False, server_default=text("1"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ObjectDeltaSourceCutover(Base):
    """Durable source baseline evidence for one fresh Object-delta stream.

    This is only the source-side record shape.  It does not acquire the write
    gate, export a snapshot, publish a baseline, or activate a worker.
    After the append-only guard migration, a normal runtime cutover must be
    inserted once in its complete ``baseline_published`` form; the historical
    pending state never authorizes source outbox allocation.
    """

    __tablename__ = "object_delta_source_cutovers"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_object_delta_source_cutovers_sites"),
        CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_object_delta_source_cutovers_campaign_id",
        ),
        CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_object_delta_source_cutovers_release_sha",
        ),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_object_delta_source_cutovers_generation_id",
        ),
        CheckConstraint(
            "char_length(registry_fingerprint) = 16",
            name="ck_object_delta_source_cutovers_registry_fingerprint",
        ),
        CheckConstraint(
            "writer_epoch >= 1",
            name="ck_object_delta_source_cutovers_writer_epoch",
        ),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_writer_lease",
        ),
        CheckConstraint(
            "char_length(source_generation) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_source_generation",
        ),
        CheckConstraint(
            "char_length(snapshot_id) BETWEEN 1 AND 128",
            name="ck_object_delta_source_cutovers_snapshot_id",
        ),
        CheckConstraint(
            "char_length(alembic_revision) BETWEEN 8 AND 64",
            name="ck_object_delta_source_cutovers_alembic_revision",
        ),
        CheckConstraint(
            "snapshot_manifest_object_key IS NULL OR char_length(snapshot_manifest_object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_source_cutovers_snapshot_manifest_object_key",
        ),
        CheckConstraint(
            "snapshot_manifest_object_version_id IS NULL OR char_length(snapshot_manifest_object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_source_cutovers_snapshot_manifest_version",
        ),
        CheckConstraint(
            "snapshot_manifest_ciphertext_sha256 IS NULL OR char_length(snapshot_manifest_ciphertext_sha256) = 64",
            name="ck_object_delta_source_cutovers_snapshot_manifest_hash",
        ),
        CheckConstraint(
            "snapshot_manifest_ciphertext_bytes IS NULL OR snapshot_manifest_ciphertext_bytes >= 1",
            name="ck_object_delta_source_cutovers_snapshot_manifest_bytes",
        ),
        CheckConstraint(
            "baseline_manifest_object_key IS NULL OR char_length(baseline_manifest_object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_source_cutovers_baseline_manifest_object_key",
        ),
        CheckConstraint(
            "baseline_manifest_object_version_id IS NULL OR char_length(baseline_manifest_object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_source_cutovers_baseline_manifest_version",
        ),
        CheckConstraint(
            "baseline_manifest_ciphertext_sha256 IS NULL OR char_length(baseline_manifest_ciphertext_sha256) = 64",
            name="ck_object_delta_source_cutovers_baseline_manifest_hash",
        ),
        CheckConstraint(
            "baseline_manifest_ciphertext_bytes IS NULL OR baseline_manifest_ciphertext_bytes >= 1",
            name="ck_object_delta_source_cutovers_baseline_manifest_bytes",
        ),
        CheckConstraint(
            "char_length(database_sha256) = 64 AND char_length(uploads_sha256) = 64",
            name="ck_object_delta_source_cutovers_local_snapshot_hashes",
        ),
        CheckConstraint(
            "state IN ('outbox_active_baseline_pending', 'baseline_published')",
            name="ck_object_delta_source_cutovers_state",
        ),
        CheckConstraint(
            "state <> 'baseline_published' OR ("
            "snapshot_manifest_object_key IS NOT NULL AND "
            "snapshot_manifest_object_version_id IS NOT NULL AND "
            "snapshot_manifest_ciphertext_sha256 IS NOT NULL AND "
            "snapshot_manifest_ciphertext_bytes IS NOT NULL AND "
            "baseline_manifest_object_key IS NOT NULL AND "
            "baseline_manifest_object_version_id IS NOT NULL AND "
            "baseline_manifest_ciphertext_sha256 IS NOT NULL AND "
            "baseline_manifest_ciphertext_bytes IS NOT NULL)",
            name="ck_object_delta_source_cutovers_published_object_evidence",
        ),
        ForeignKeyConstraint(
            (
                "stream_id",
                "source_site",
                "destination_site",
                "campaign_id",
                "release_sha",
                "stream_generation_id",
            ),
            (
                "object_delta_streams.id",
                "object_delta_streams.source_site",
                "object_delta_streams.destination_site",
                "object_delta_streams.campaign_id",
                "object_delta_streams.release_sha",
                "object_delta_streams.stream_generation_id",
            ),
            name="fk_object_delta_source_cutovers_stream_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("stream_id", name="ux_object_delta_source_cutovers_stream"),
        UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            name="ux_object_delta_source_cutovers_identity",
        ),
        UniqueConstraint("write_gate_id", name="ux_object_delta_source_cutovers_write_gate"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(Integer, nullable=False)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    # The root-only cutover coordinator must supply this; there is no ORM or
    # database default that could manufacture a gate outside that authority.
    write_gate_id = Column(UUID(as_uuid=True), nullable=False)
    registry_fingerprint = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    source_generation = Column(String(128), nullable=False)
    snapshot_id = Column(String(128), nullable=False)
    alembic_revision = Column(String(64), nullable=False)
    # The original durable shape preserves nullable publication evidence for
    # historical pending records.  The later append-only database guard rejects
    # new incomplete records, so normal runtime cutovers always carry all of
    # these immutable Object-version receipts at INSERT time.
    snapshot_manifest_object_key = Column(String(1024), nullable=True)
    snapshot_manifest_object_version_id = Column(String(1024), nullable=True)
    snapshot_manifest_ciphertext_sha256 = Column(String(64), nullable=True)
    snapshot_manifest_ciphertext_bytes = Column(BigInteger, nullable=True)
    baseline_manifest_object_key = Column(String(1024), nullable=True)
    baseline_manifest_object_version_id = Column(String(1024), nullable=True)
    baseline_manifest_ciphertext_sha256 = Column(String(64), nullable=True)
    baseline_manifest_ciphertext_bytes = Column(BigInteger, nullable=True)
    database_sha256 = Column(String(64), nullable=False)
    uploads_sha256 = Column(String(64), nullable=False)
    state = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ObjectDeltaOutboxEntry(Base):
    """One immutable, source-side logical change ready for a future batch builder."""

    __tablename__ = "object_delta_outbox"
    __table_args__ = (
        CheckConstraint("logical_sequence >= 1", name="ck_object_delta_outbox_sequence"),
        CheckConstraint("writer_epoch >= 1", name="ck_object_delta_outbox_writer_epoch"),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_object_delta_outbox_writer_lease",
        ),
        CheckConstraint(
            "char_length(sync_item_sha256) = 64",
            name="ck_object_delta_outbox_sync_item_hash",
        ),
        UniqueConstraint("stream_id", "logical_sequence", name="ux_object_delta_outbox_stream_sequence"),
        UniqueConstraint("stream_id", "change_log_id", name="ux_object_delta_outbox_stream_change_log"),
        Index("ix_object_delta_outbox_stream_sequence", "stream_id", "logical_sequence"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(
        Integer,
        ForeignKey("object_delta_streams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_sequence = Column(BigInteger, nullable=False)
    # The pre-existing durable evidence is intentionally retained by FK.
    change_log_id = Column(Integer, ForeignKey("change_log.id", ondelete="RESTRICT"), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    # A future adapter stores only the strict canonical db_change envelope here.
    canonical_sync_item = Column(JSON, nullable=False)
    sync_item_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaReceiverCursor(Base):
    """The durable receiver cursor described by ``ReceiverStreamCursor``."""

    __tablename__ = "object_delta_receiver_cursors"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_object_delta_receiver_cursors_sites"),
        CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_object_delta_receiver_cursors_campaign_id",
        ),
        CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_object_delta_receiver_cursors_release_sha",
        ),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_object_delta_receiver_cursors_generation_id",
        ),
        CheckConstraint("last_sequence >= 0", name="ck_object_delta_receiver_cursors_last_sequence"),
        CheckConstraint(
            "char_length(last_batch_sha256) = 64",
            name="ck_object_delta_receiver_cursors_last_batch_hash",
        ),
        UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            name="ux_object_delta_receiver_cursors_identity",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    last_batch_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ObjectDeltaImportReceipt(Base):
    """Immutable receiver receipt for one verified encrypted Object version.

    ``object_key`` and ``object_version_id`` are unique only because the
    future runtime fixes and authenticates the Object Storage bucket outside
    this table.  Bucket and endpoint identifiers must never be inferred from
    the untrusted batch payload.
    """

    __tablename__ = "object_delta_import_receipts"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_object_delta_import_receipts_sites"),
        CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_object_delta_import_receipts_campaign_id",
        ),
        CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_object_delta_import_receipts_release_sha",
        ),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_object_delta_import_receipts_generation_id",
        ),
        CheckConstraint("first_sequence >= 1", name="ck_object_delta_import_receipts_first_sequence"),
        CheckConstraint(
            "last_sequence >= first_sequence",
            name="ck_object_delta_import_receipts_sequence_range",
        ),
        CheckConstraint("writer_epoch >= 1", name="ck_object_delta_import_receipts_writer_epoch"),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_object_delta_import_receipts_writer_lease",
        ),
        CheckConstraint(
            "char_length(prior_chain_sha256) = 64 AND char_length(batch_sha256) = 64 AND char_length(payload_sha256) = 64 AND char_length(ciphertext_sha256) = 64",
            name="ck_object_delta_import_receipts_hashes",
        ),
        CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_import_receipts_object_key",
        ),
        CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_import_receipts_object_version",
        ),
        CheckConstraint(
            "ciphertext_bytes >= 1",
            name="ck_object_delta_import_receipts_ciphertext_bytes",
        ),
        UniqueConstraint(
            "object_key",
            "object_version_id",
            name="ux_object_delta_import_receipts_object_version",
        ),
        # The delivery-nonce receipt is inserted in the same receiver
        # transaction as this immutable receipt.  It must be able to use a
        # composite foreign key that proves the nonce belongs to this exact
        # stream/term/range/batch identity, not merely to a coincidentally
        # named Object version.  ``object_key``/``object_version_id`` remain
        # globally unique, but PostgreSQL requires this exact candidate key
        # for the stronger child FK.
        UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "writer_epoch",
            "writer_lease_id",
            "first_sequence",
            "last_sequence",
            "batch_sha256",
            "object_key",
            "object_version_id",
            name="ux_object_delta_import_receipts_nonce_binding",
        ),
        UniqueConstraint(
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "first_sequence",
            name="ux_object_delta_import_receipts_stream_first_sequence",
        ),
        Index(
            "ix_object_delta_import_receipts_stream_last_sequence",
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "last_sequence",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    first_sequence = Column(BigInteger, nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    prior_chain_sha256 = Column(String(64), nullable=False)
    batch_sha256 = Column(String(64), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    object_key = Column(String(1024), nullable=False)
    object_version_id = Column(String(1024), nullable=False)
    ciphertext_sha256 = Column(String(64), nullable=False)
    ciphertext_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
