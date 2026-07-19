"""Immutable three-site DR event, destination, gap, conflict, and effect ledgers."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from .database import Base


class DrProducerCursor(Base):
    __tablename__ = "dr_producer_cursors"
    origin_authority = Column(String(16), primary_key=True)
    origin_physical_site = Column(String(16), primary_key=True)
    producer_epoch = Column(BigInteger, primary_key=True)
    last_sequence = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrDestinationCursor(Base):
    """Contiguous stream position visible to one authorized destination."""

    __tablename__ = "dr_destination_cursors"
    origin_authority = Column(String(16), primary_key=True)
    origin_physical_site = Column(String(16), primary_key=True)
    producer_epoch = Column(BigInteger, primary_key=True)
    destination_site = Column(String(16), primary_key=True)
    last_sequence = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrEvent(Base):
    __tablename__ = "dr_events"
    __table_args__ = (
        UniqueConstraint(
            "origin_physical_site",
            "producer_epoch",
            "producer_sequence",
            name="ux_dr_events_stream_sequence",
        ),
        CheckConstraint("producer_epoch >= 1", name="ck_dr_events_epoch_positive"),
        CheckConstraint("producer_sequence >= 1", name="ck_dr_events_sequence_positive"),
        CheckConstraint("operation IN ('INSERT', 'UPDATE', 'DELETE')", name="ck_dr_events_operation"),
        Index("ix_dr_events_aggregate", "aggregate_type", "aggregate_id", "aggregate_version"),
        Index("ix_dr_events_stream", "origin_physical_site", "producer_epoch", "producer_sequence"),
        UniqueConstraint(
            "origin_physical_site",
            "producer_epoch",
            "transaction_id",
            "transaction_position",
            name="ux_dr_events_transaction_position",
        ),
    )

    event_id = Column(String(36), primary_key=True)
    protocol_version = Column(Integer, nullable=False, default=1)
    origin_authority = Column(String(16), nullable=False)
    origin_physical_site = Column(String(16), nullable=False)
    producer_epoch = Column(BigInteger, nullable=False)
    producer_sequence = Column(BigInteger, nullable=False)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(255), nullable=False)
    aggregate_db_id = Column(String(64), nullable=True)
    aggregate_version = Column(BigInteger, nullable=True)
    operation = Column(String(10), nullable=False)
    canonical_payload = Column(JSON, nullable=False)
    canonical_payload_hash = Column(String(64), nullable=False)
    envelope_hash = Column(String(64), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    causation_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(255), nullable=True)
    writer_epoch = Column(BigInteger, nullable=True)
    tombstone = Column(Boolean, nullable=False, default=False)
    transaction_id = Column(String(36), nullable=True)
    transaction_position = Column(Integer, nullable=True)
    transaction_size = Column(Integer, nullable=True)
    transaction_hash = Column(String(64), nullable=True)
    # Per-destination sequence and atomic visibility group.  A global source
    # transaction may contain WebApp-private rows that Bot-FI must never see;
    # destination streams prevent unauthorized rows from becoming false gaps.
    destination_streams = Column(JSON, nullable=True)
    # Local PostgreSQL transaction identity is never transported.  A deferred
    # database trigger uses it to prove every authoritative row mutation has a
    # same-transaction event before commit.
    source_xid = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrEventDelivery(Base):
    __tablename__ = "dr_event_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'inflight', 'acknowledged', 'blocked_gap', 'quarantined')",
            name="ck_dr_event_deliveries_status",
        ),
        Index("ix_dr_event_deliveries_ready", "destination_site", "status", "next_attempt_at"),
    )

    event_id = Column(String(36), ForeignKey("dr_events.event_id", ondelete="RESTRICT"), primary_key=True)
    destination_site = Column(String(16), primary_key=True)
    status = Column(String(20), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledgement_hash = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    relay_site = Column(String(16), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrEventReceipt(Base):
    __tablename__ = "dr_event_receipts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'applied', 'duplicate', 'blocked_gap', 'quarantined')",
            name="ck_dr_event_receipts_status",
        ),
        UniqueConstraint(
            "destination_site", "origin_physical_site", "producer_epoch", "producer_sequence",
            name="ux_dr_event_receipts_destination_stream",
        ),
    )

    event_id = Column(String(36), primary_key=True)
    destination_site = Column(String(16), primary_key=True)
    origin_physical_site = Column(String(16), nullable=False)
    producer_epoch = Column(BigInteger, nullable=False)
    producer_sequence = Column(BigInteger, nullable=False)
    envelope_hash = Column(String(64), nullable=False)
    received_from_site = Column(String(16), nullable=False)
    relay_site = Column(String(16), nullable=True)
    status = Column(String(20), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    applied_at = Column(DateTime(timezone=True), nullable=True)


class DrStreamCheckpoint(Base):
    __tablename__ = "dr_stream_checkpoints"
    destination_site = Column(String(16), primary_key=True)
    origin_physical_site = Column(String(16), primary_key=True)
    producer_epoch = Column(BigInteger, primary_key=True)
    contiguous_received_sequence = Column(BigInteger, nullable=False, default=0)
    contiguous_applied_sequence = Column(BigInteger, nullable=False, default=0)
    last_envelope_hash = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrProjectionVersion(Base):
    """Highest applied authority term for one aggregate at one destination."""

    __tablename__ = "dr_projection_versions"
    __table_args__ = (
        CheckConstraint("producer_epoch >= 1", name="ck_dr_projection_versions_epoch"),
        CheckConstraint("producer_sequence >= 1", name="ck_dr_projection_versions_sequence"),
        CheckConstraint(
            "origin_authority IN ('foreign', 'webapp')",
            name="ck_dr_projection_versions_authority",
        ),
    )

    destination_site = Column(String(16), primary_key=True)
    origin_authority = Column(String(16), primary_key=True)
    aggregate_type = Column(String(64), primary_key=True)
    aggregate_id = Column(String(255), primary_key=True)
    origin_physical_site = Column(String(16), nullable=False)
    producer_epoch = Column(BigInteger, nullable=False)
    producer_sequence = Column(BigInteger, nullable=False)
    event_id = Column(String(36), nullable=False)
    envelope_hash = Column(String(64), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrConflictQuarantine(Base):
    __tablename__ = "dr_conflict_quarantine"
    __table_args__ = (
        Index("ix_dr_conflict_unresolved", "resolved_at", "created_at"),
    )

    quarantine_id = Column(String(36), primary_key=True)
    destination_site = Column(String(16), nullable=False)
    origin_physical_site = Column(String(16), nullable=False)
    producer_epoch = Column(BigInteger, nullable=False)
    producer_sequence = Column(BigInteger, nullable=False)
    event_id = Column(String(36), nullable=False)
    reason = Column(String(64), nullable=False)
    expected_hash = Column(String(64), nullable=True)
    received_hash = Column(String(64), nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(128), nullable=True)
    resolution = Column(Text, nullable=True)


class DrReplayNonce(Base):
    __tablename__ = "dr_replay_nonces"
    key_id = Column(String(64), primary_key=True)
    nonce = Column(String(64), primary_key=True)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    request_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrEffectOutbox(Base):
    __tablename__ = "dr_effect_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="ux_dr_effect_outbox_idempotency"),
        CheckConstraint(
            "status IN ('pending', 'inflight', 'succeeded', 'failed', 'ambiguous', 'cancelled_stale_epoch')",
            name="ck_dr_effect_outbox_status",
        ),
        Index("ix_dr_effect_outbox_ready", "executor_site", "status", "next_attempt_at"),
    )

    effect_id = Column(String(36), primary_key=True)
    event_id = Column(String(36), ForeignKey("dr_events.event_id", ondelete="RESTRICT"), nullable=False)
    origin_physical_site = Column(String(16), nullable=False)
    executor_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=True)
    effect_type = Column(String(32), nullable=False)
    provider = Column(String(32), nullable=False)
    destination_key_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    claimed_by = Column(String(128), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    provider_receipt_hash = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrEffectFanout(Base):
    """Transactionally durable request to derive recipient-specific effects."""

    __tablename__ = "dr_effect_fanouts"
    __table_args__ = (
        CheckConstraint(
            "fanout_type IN ('market_offer_webpush', 'notification_webpush')",
            name="ck_dr_effect_fanouts_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'expanded', 'skipped')",
            name="ck_dr_effect_fanouts_status",
        ),
        Index("ix_dr_effect_fanouts_ready", "status", "created_at"),
    )

    event_id = Column(
        String(36), ForeignKey("dr_events.event_id", ondelete="RESTRICT"), primary_key=True
    )
    aggregate_type = Column(String(64), nullable=False)
    aggregate_db_id = Column(String(64), nullable=False)
    origin_physical_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    fanout_type = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    recipient_count = Column(Integer, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrBlobManifest(Base):
    __tablename__ = "dr_blob_manifests"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_dr_blob_manifest_size"),
        CheckConstraint("state IN ('local', 'uploaded', 'tombstoned')", name="ck_dr_blob_manifest_state"),
    )

    content_hash = Column(String(64), primary_key=True)
    size_bytes = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=False)
    local_path = Column(String(512), nullable=False)
    # Object coordinates are assigned by the encryption-only blob worker.  The
    # application process never receives the client-side encryption keyring.
    object_key = Column(String(512), nullable=True, unique=True)
    object_version_id = Column(String(255), nullable=True)
    object_etag = Column(String(255), nullable=True)
    object_ciphertext_hash = Column(String(64), nullable=True)
    object_ciphertext_size = Column(BigInteger, nullable=True)
    encryption_key_id = Column(String(64), nullable=True)
    encryption_algorithm = Column(String(32), nullable=True)
    state = Column(String(16), nullable=False, default="local")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    tombstoned_at = Column(DateTime(timezone=True), nullable=True)
    retain_until = Column(DateTime(timezone=True), nullable=True)
    local_deleted_at = Column(DateTime(timezone=True), nullable=True)


class DrFileIntent(Base):
    __tablename__ = "dr_file_intents"
    __table_args__ = (
        UniqueConstraint("chat_file_id", name="ux_dr_file_intents_chat_file"),
        CheckConstraint("writer_epoch >= 1", name="ck_dr_file_intents_writer_epoch"),
    )

    intent_id = Column(String(36), primary_key=True)
    chat_file_id = Column(String(36), ForeignKey("chat_files.id", ondelete="CASCADE"), nullable=False)
    content_hash = Column(String(64), ForeignKey("dr_blob_manifests.content_hash", ondelete="RESTRICT"), nullable=False)
    origin_physical_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrBlobDelivery(Base):
    __tablename__ = "dr_blob_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_upload', 'available', 'acknowledged', 'failed', 'quarantined')",
            name="ck_dr_blob_deliveries_status",
        ),
        Index("ix_dr_blob_deliveries_ready", "destination_site", "status", "next_attempt_at"),
    )

    content_hash = Column(String(64), ForeignKey("dr_blob_manifests.content_hash", ondelete="RESTRICT"), primary_key=True)
    destination_site = Column(String(16), primary_key=True)
    status = Column(String(24), nullable=False, default="pending_upload")
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledgement_hash = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrBlobReceipt(Base):
    __tablename__ = "dr_blob_receipts"
    content_hash = Column(String(64), primary_key=True)
    destination_site = Column(String(16), primary_key=True)
    origin_physical_site = Column(String(16), primary_key=True)
    object_version_id = Column(String(255), nullable=True)
    size_bytes = Column(BigInteger, nullable=False)
    object_ciphertext_hash = Column(String(64), nullable=False)
    object_ciphertext_size = Column(BigInteger, nullable=False)
    encryption_key_id = Column(String(64), nullable=False)
    encryption_algorithm = Column(String(32), nullable=False)
    local_path = Column(String(512), nullable=False)
    receipt_hash = Column(String(64), nullable=False)
    source_acknowledgement_hash = Column(String(64), nullable=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DrRecoveryManifest(Base):
    __tablename__ = "dr_recovery_manifests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'verified', 'invalidated')",
            name="ck_dr_recovery_manifests_status",
        ),
        CheckConstraint(
            "manifest_kind IN ('promotion', 'origin')",
            name="ck_dr_recovery_manifests_kind",
        ),
    )

    manifest_id = Column(String(36), primary_key=True)
    manifest_kind = Column(String(16), nullable=False)
    physical_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    release_sha = Column(String(64), nullable=False)
    database_lsn = Column(String(64), nullable=False)
    database_snapshot = Column(String(255), nullable=False)
    database_fingerprint_hash = Column(String(64), nullable=False)
    database_row_count = Column(BigInteger, nullable=False)
    event_checkpoint_hash = Column(String(64), nullable=False)
    blob_set_hash = Column(String(64), nullable=False)
    blob_count = Column(BigInteger, nullable=False)
    manifest_hash = Column(String(64), nullable=False, unique=True)
    status = Column(String(16), nullable=False, default="prepared")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    verified_at = Column(DateTime(timezone=True), nullable=True)


class DrDurabilityState(Base):
    __tablename__ = "dr_durability_state"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_dr_durability_state_singleton"),
        CheckConstraint(
            "connectivity_mode IN ('online', 'isolated', 'ambiguous')",
            name="ck_dr_durability_state_mode",
        ),
    )

    singleton_id = Column(Integer, primary_key=True, default=1)
    connectivity_mode = Column(String(16), nullable=False, default="ambiguous")
    event_journal_healthy = Column(Boolean, nullable=False, default=False)
    blob_journal_healthy = Column(Boolean, nullable=False, default=False)
    evidence_hash = Column(String(64), nullable=True)
    evidence_expires_at = Column(DateTime(timezone=True), nullable=True)
    updated_by = Column(String(128), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
