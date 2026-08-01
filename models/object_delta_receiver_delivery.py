"""Durable nonce receipts for the future Object-delta receiver.

No worker or receiver runtime is enabled by this model.  A future dedicated
adapter must insert one row in the same transaction as its immutable import
receipt and cursor so a signed controller delivery packet cannot be consumed
twice after a process restart.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from .database import Base


_WEBAPP_SITE_CHECK = (
    "source_site IN ('webapp_fi', 'webapp_ir') "
    "AND destination_site IN ('webapp_fi', 'webapp_ir') "
    "AND source_site <> destination_site"
)


class ObjectDeltaReceiverDeliveryNonceReceipt(Base):
    """One immutable consumption of a signed controller delivery nonce."""

    __tablename__ = "object_delta_receiver_delivery_nonce_receipts"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_od_rdnr_sites"),
        CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_od_rdnr_campaign",
        ),
        CheckConstraint(
            "char_length(release_sha) = 40",
            name="ck_od_rdnr_release",
        ),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_od_rdnr_generation",
        ),
        CheckConstraint(
            "char_length(controller_key_id) = 79",
            name="ck_od_rdnr_controller_key",
        ),
        CheckConstraint(
            "char_length(nonce) BETWEEN 32 AND 128",
            name="ck_od_rdnr_nonce",
        ),
        CheckConstraint(
            "char_length(bucket) BETWEEN 3 AND 63",
            name="ck_od_rdnr_bucket",
        ),
        CheckConstraint(
            "char_length(destination_age_recipient) BETWEEN 24 AND 132",
            name="ck_od_rdnr_destination_recipient",
        ),
        CheckConstraint(
            "char_length(packet_claim_sha256) = 64 AND char_length(batch_sha256) = 64",
            name="ck_od_rdnr_hashes",
        ),
        CheckConstraint(
            "writer_epoch >= 1",
            name="ck_od_rdnr_writer_epoch",
        ),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_od_rdnr_writer_lease",
        ),
        CheckConstraint(
            "first_sequence >= 1 AND last_sequence >= first_sequence",
            name="ck_od_rdnr_sequence_range",
        ),
        CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024",
            name="ck_od_rdnr_object_key",
        ),
        CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024",
            name="ck_od_rdnr_object_version",
        ),
        ForeignKeyConstraint(
            [
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
            ],
            [
                "object_delta_import_receipts.source_site",
                "object_delta_import_receipts.destination_site",
                "object_delta_import_receipts.campaign_id",
                "object_delta_import_receipts.release_sha",
                "object_delta_import_receipts.stream_generation_id",
                "object_delta_import_receipts.writer_epoch",
                "object_delta_import_receipts.writer_lease_id",
                "object_delta_import_receipts.first_sequence",
                "object_delta_import_receipts.last_sequence",
                "object_delta_import_receipts.batch_sha256",
                "object_delta_import_receipts.object_key",
                "object_delta_import_receipts.object_version_id",
            ],
            name="fk_od_rdnr_import_binding",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "controller_key_id",
            "nonce",
            name="ux_od_rdnr_controller_nonce",
        ),
        Index(
            "ix_od_rdnr_stream_sequence",
            "source_site",
            "destination_site",
            "campaign_id",
            "release_sha",
            "stream_generation_id",
            "first_sequence",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    controller_key_id = Column(String(79), nullable=False)
    nonce = Column(String(128), nullable=False)
    packet_claim_sha256 = Column(String(64), nullable=False)
    bucket = Column(String(63), nullable=False)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    destination_age_recipient = Column(String(132), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    first_sequence = Column(BigInteger, nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    batch_sha256 = Column(String(64), nullable=False)
    object_key = Column(String(1024), nullable=False)
    object_version_id = Column(String(1024), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
