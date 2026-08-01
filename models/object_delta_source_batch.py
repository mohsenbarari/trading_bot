"""ORM schema for source-side Object-delta batch evidence.

These models do not publish, acknowledge, fetch, or apply Object Storage
payloads.  They are intentionally isolated from ``models.object_delta`` so
the source-ledger migration can land without rewriting a concurrently edited
schema module.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)

from .database import Base


_GENESIS_SHA256 = "0" * 64


class ObjectDeltaSourceBatchLedger(Base):
    """An immutable source batch and its verified encrypted Object identity."""

    __tablename__ = "object_delta_source_batch_ledger"
    __table_args__ = (
        CheckConstraint("first_sequence >= 1", name="ck_object_delta_source_batch_ledger_first_sequence"),
        CheckConstraint(
            "last_sequence >= first_sequence",
            name="ck_object_delta_source_batch_ledger_sequence_range",
        ),
        CheckConstraint("writer_epoch >= 1", name="ck_object_delta_source_batch_ledger_writer_epoch"),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_object_delta_source_batch_ledger_writer_lease",
        ),
        CheckConstraint(
            "char_length(prior_chain_sha256) = 64 AND char_length(batch_sha256) = 64 AND char_length(payload_sha256) = 64 AND char_length(ciphertext_sha256) = 64",
            name="ck_object_delta_source_batch_ledger_hashes",
        ),
        CheckConstraint(
            "payload_bytes >= 1 AND ciphertext_bytes >= 1",
            name="ck_object_delta_source_batch_ledger_bytes",
        ),
        CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024",
            name="ck_object_delta_source_batch_ledger_object_key",
        ),
        CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024",
            name="ck_object_delta_source_batch_ledger_object_version",
        ),
        UniqueConstraint(
            "stream_id",
            "first_sequence",
            name="ux_object_delta_source_batch_ledger_stream_first_sequence",
        ),
        UniqueConstraint(
            "stream_id",
            "batch_sha256",
            name="ux_object_delta_source_batch_ledger_stream_batch_hash",
        ),
        UniqueConstraint(
            "object_key",
            "object_version_id",
            name="ux_object_delta_source_batch_ledger_object_version",
        ),
        Index(
            "ix_object_delta_source_batch_ledger_stream_last_sequence",
            "stream_id",
            "last_sequence",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(
        Integer,
        ForeignKey("object_delta_streams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    first_sequence = Column(BigInteger, nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    prior_chain_sha256 = Column(String(64), nullable=False)
    batch_sha256 = Column(String(64), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload_bytes = Column(BigInteger, nullable=False)
    object_key = Column(String(1024), nullable=False)
    object_version_id = Column(String(1024), nullable=False)
    ciphertext_sha256 = Column(String(64), nullable=False)
    ciphertext_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaOutboundAckCursor(Base):
    """The mutable source acknowledgement frontier; ledger rows stay immutable."""

    __tablename__ = "object_delta_outbound_ack_cursors"
    __table_args__ = (
        CheckConstraint(
            "last_acknowledged_sequence >= 0",
            name="ck_object_delta_outbound_ack_cursors_last_sequence",
        ),
        CheckConstraint(
            "char_length(last_acknowledged_batch_sha256) = 64",
            name="ck_object_delta_outbound_ack_cursors_last_batch_hash",
        ),
        UniqueConstraint("stream_id", name="ux_object_delta_outbound_ack_cursors_stream"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stream_id = Column(
        Integer,
        ForeignKey("object_delta_streams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    last_acknowledged_sequence = Column(BigInteger, nullable=False, server_default=text("0"))
    last_acknowledged_batch_sha256 = Column(
        String(64),
        nullable=False,
        server_default=text(f"'{_GENESIS_SHA256}'"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
