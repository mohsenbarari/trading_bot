"""Durable, append-only facts for a future Object-delta source publisher.

These ORM models deliberately store a publication attempt as a sequence of
one-way evidence rows rather than a mutable ``state`` column:

``reservation -> sealed ciphertext -> exact Object receipt -> source
attestation -> source-ledger binding``.

Consequently a later adapter cannot overwrite a previously sealed ciphertext,
receipt, attestation, or terminal ledger association while advancing an
attempt.  The models are schema only: they do not open the spool, encrypt,
contact Object Storage, verify signatures, or start a publisher.
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


class ObjectDeltaSourcePublicationAttempt(Base):
    """One immutable deterministic publication intent reserved before encryption.

    ``attempt_id`` is the deterministic ``odsp-v1:`` identifier from the
    pure publication-attempt contract.  ``object_key`` is independently
    unique because it intentionally omits some control-plane facts; a future
    adapter must lock and read both unique keys before deciding reserve versus
    replay.
    """

    __tablename__ = "object_delta_source_publication_attempts"
    __table_args__ = (
        CheckConstraint(_WEBAPP_SITE_CHECK, name="ck_od_spa_sites"),
        CheckConstraint(
            "attempt_id ~ '^odsp-v1:[0-9a-f]{64}$'",
            name="ck_od_spa_attempt_id",
        ),
        CheckConstraint(
            "char_length(campaign_id) BETWEEN 8 AND 128",
            name="ck_od_spa_campaign",
        ),
        CheckConstraint("release_sha ~ '^[0-9a-f]{40}$'", name="ck_od_spa_release"),
        CheckConstraint(
            "char_length(stream_generation_id) BETWEEN 8 AND 128",
            name="ck_od_spa_generation",
        ),
        CheckConstraint("writer_epoch >= 1", name="ck_od_spa_writer_epoch"),
        CheckConstraint(
            "writer_lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'",
            name="ck_od_spa_writer_lease",
        ),
        CheckConstraint(
            "first_sequence >= 1 AND last_sequence >= first_sequence "
            "AND last_sequence - first_sequence <= 99999",
            name="ck_od_spa_sequence_range",
        ),
        CheckConstraint(
            "prior_chain_sha256 ~ '^[0-9a-f]{64}$' "
            "AND payload_sha256 ~ '^[0-9a-f]{64}$' "
            "AND transport_policy_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_cutover_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spa_hashes",
        ),
        CheckConstraint(
            "payload_bytes BETWEEN 1 AND 107374182400",
            name="ck_od_spa_payload_bytes",
        ),
        CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024 "
            "AND object_key ~ '^[A-Za-z0-9][A-Za-z0-9._/=-]*$' "
            "AND object_key NOT LIKE '%/../%'",
            name="ck_od_spa_object_key",
        ),
        CheckConstraint(
            "destination_age_recipient ~ '^age1[ac-hj-np-z02-9]{20,128}$'",
            name="ck_od_spa_destination_recipient",
        ),
        CheckConstraint(
            "source_cutover_artifact_bytes BETWEEN 1 AND 131072",
            name="ck_od_spa_cutover_artifact",
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
            name="fk_od_spa_stream_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="ux_od_spa_attempt_id"),
        UniqueConstraint("object_key", name="ux_od_spa_object_key"),
        # PostgreSQL needs this exact candidate key for the receipt FK below,
        # even though each component is independently unique.
        UniqueConstraint(
            "attempt_id",
            "object_key",
            name="ux_od_spa_attempt_object_key",
        ),
        # A new intent cannot fork a stream frontier merely by changing an
        # otherwise-keyed control field.  Overlap/frontier validation still
        # belongs to the locked source-ledger adapter.
        UniqueConstraint(
            "stream_id",
            "first_sequence",
            name="ux_od_spa_stream_first_sequence",
        ),
        Index("ix_od_spa_stream_first", "stream_id", "first_sequence"),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(String(72), nullable=False)
    stream_id = Column(Integer, nullable=False)
    source_site = Column(String(16), nullable=False)
    destination_site = Column(String(16), nullable=False)
    campaign_id = Column(String(128), nullable=False)
    release_sha = Column(String(40), nullable=False)
    stream_generation_id = Column(String(128), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    first_sequence = Column(BigInteger, nullable=False)
    last_sequence = Column(BigInteger, nullable=False)
    prior_chain_sha256 = Column(String(64), nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    payload_bytes = Column(BigInteger, nullable=False)
    object_key = Column(String(1024), nullable=False)
    destination_age_recipient = Column(String(132), nullable=False)
    transport_policy_sha256 = Column(String(64), nullable=False)
    source_cutover_artifact_sha256 = Column(String(64), nullable=False)
    source_cutover_artifact_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaSourcePublicationSeal(Base):
    """Exact root-only spool evidence persisted before every possible PUT."""

    __tablename__ = "object_delta_source_publication_seals"
    __table_args__ = (
        CheckConstraint(
            "ciphertext_sha256 ~ '^[0-9a-f]{64}$' "
            "AND spool_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_sps_hashes",
        ),
        CheckConstraint(
            "ciphertext_bytes BETWEEN 1 AND 107375230976 "
            "AND spool_bytes BETWEEN 1 AND 107375230976",
            name="ck_od_sps_bytes",
        ),
        CheckConstraint(
            "ciphertext_sha256 = spool_sha256 AND ciphertext_bytes = spool_bytes",
            name="ck_od_sps_exact_spool",
        ),
        ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_attempts.attempt_id"],
            name="fk_od_sps_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="ux_od_sps_attempt"),
        # Exact candidate key used by the receipt's ciphertext-binding FK.
        UniqueConstraint(
            "attempt_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            name="ux_od_sps_attempt_ciphertext",
        ),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(String(72), nullable=False)
    ciphertext_sha256 = Column(String(64), nullable=False)
    ciphertext_bytes = Column(BigInteger, nullable=False)
    spool_sha256 = Column(String(64), nullable=False)
    spool_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaSourcePublicationReceipt(Base):
    """One exact Object-VersionId read-back receipt for a sealed attempt."""

    __tablename__ = "object_delta_source_publication_receipts"
    __table_args__ = (
        CheckConstraint(
            "char_length(object_key) BETWEEN 3 AND 1024 "
            "AND object_key ~ '^[A-Za-z0-9][A-Za-z0-9._/=-]*$' "
            "AND object_key NOT LIKE '%/../%'",
            name="ck_od_spr_object_key",
        ),
        CheckConstraint(
            "char_length(object_version_id) BETWEEN 1 AND 1024 "
            "AND object_version_id ~ '^[A-Za-z0-9._~+/=-]+$' "
            "AND lower(object_version_id) <> 'null'",
            name="ck_od_spr_object_version",
        ),
        CheckConstraint(
            "ciphertext_sha256 ~ '^[0-9a-f]{64}$' "
            "AND transport_receipt_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spr_hashes",
        ),
        CheckConstraint(
            "ciphertext_bytes BETWEEN 1 AND 107375230976 "
            "AND transport_receipt_artifact_bytes BETWEEN 1 AND 32768",
            name="ck_od_spr_bytes",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "object_key"],
            [
                "object_delta_source_publication_attempts.attempt_id",
                "object_delta_source_publication_attempts.object_key",
            ],
            name="fk_od_spr_attempt_key",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "ciphertext_sha256", "ciphertext_bytes"],
            [
                "object_delta_source_publication_seals.attempt_id",
                "object_delta_source_publication_seals.ciphertext_sha256",
                "object_delta_source_publication_seals.ciphertext_bytes",
            ],
            name="fk_od_spr_seal_ciphertext",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="ux_od_spr_attempt"),
        UniqueConstraint(
            "object_key",
            "object_version_id",
            name="ux_od_spr_object_version",
        ),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(String(72), nullable=False)
    object_key = Column(String(1024), nullable=False)
    object_version_id = Column(String(1024), nullable=False)
    ciphertext_sha256 = Column(String(64), nullable=False)
    ciphertext_bytes = Column(BigInteger, nullable=False)
    transport_receipt_artifact_sha256 = Column(String(64), nullable=False)
    transport_receipt_artifact_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaSourcePublicationAttestation(Base):
    """Verified canonical source-attestation artifact facts after receipt persistence."""

    __tablename__ = "object_delta_source_publication_attestations"
    __table_args__ = (
        CheckConstraint(
            "source_key_id ~ '^ed25519-sha256:[0-9a-f]{64}$'",
            name="ck_od_spat_source_key",
        ),
        CheckConstraint(
            "batch_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_attestation_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_od_spat_hashes",
        ),
        CheckConstraint(
            "source_attestation_artifact_bytes BETWEEN 1 AND 8454144",
            name="ck_od_spat_artifact_bytes",
        ),
        ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_receipts.attempt_id"],
            name="fk_od_spat_receipt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="ux_od_spat_attempt"),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(String(72), nullable=False)
    source_key_id = Column(String(79), nullable=False)
    batch_sha256 = Column(String(64), nullable=False)
    source_attestation_artifact_sha256 = Column(String(64), nullable=False)
    source_attestation_artifact_bytes = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ObjectDeltaSourcePublicationLedgerBinding(Base):
    """Terminal, one-to-one binding of an attested attempt to source ledger evidence.

    A future adapter must insert this row in the same database transaction as
    source-ledger append/replay.  The migration's binding trigger additionally
    checks the stream, term, range, payload, Object receipt, ciphertext, and
    batch hash against the referenced immutable ledger row.
    """

    __tablename__ = "object_delta_source_publication_ledger_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id"],
            ["object_delta_source_publication_attestations.attempt_id"],
            name="fk_od_splb_attestation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_batch_ledger_id"],
            ["object_delta_source_batch_ledger.id"],
            name="fk_od_splb_source_ledger",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="ux_od_splb_attempt"),
        UniqueConstraint("source_batch_ledger_id", name="ux_od_splb_source_ledger"),
    )

    id = Column(Integer, primary_key=True)
    attempt_id = Column(String(72), nullable=False)
    source_batch_ledger_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
