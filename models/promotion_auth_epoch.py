"""Durable authentication cutover state for one active WebApp writer term.

This is deliberately a *singleton* state record rather than a promotion
controller.  A separately authorized coordinator may update it only after it
has proved the local Writer Witness term.  The record lets request admission
reject access JWTs minted before the most recent successful auth cutover.

It does not acquire a Witness term, route traffic, publish a promotion proof,
or start any worker.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from .database import Base


class PromotionAuthEpoch(Base):
    """The current monotonic authentication epoch for the active WebApp writer.

    ``id`` is fixed at one so callers can lock one durable row before
    invalidating sessions.  ``minimum_token_iat`` is a logical whole-second
    cutoff: an access token is admissible only when its validated ``iat`` is
    at or after that value.  Keeping that value alongside the precise
    ``cutover_at`` avoids the unsafe sub-second ambiguity of JWT NumericDate.
    """

    __tablename__ = "promotion_auth_epochs"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_promotion_auth_epochs_singleton"),
        CheckConstraint("writer_epoch >= 1", name="ck_promotion_auth_epochs_writer_epoch"),
        CheckConstraint(
            "minimum_token_iat >= 0",
            name="ck_promotion_auth_epochs_minimum_token_iat",
        ),
        CheckConstraint(
            "writer_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_promotion_auth_epochs_writer_site",
        ),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epochs_writer_lease",
        ),
        CheckConstraint(
            "char_length(witness_transition_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epochs_witness_transition",
        ),
        CheckConstraint(
            "char_length(operation_id) = 36",
            name="ck_promotion_auth_epochs_operation_id",
        ),
    )

    id = Column(Integer, primary_key=True, default=1)
    operation_id = Column(String(36), nullable=False)
    writer_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    witness_transition_id = Column(String(128), nullable=False)
    cutover_at = Column(DateTime(timezone=True), nullable=False)
    minimum_token_iat = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PromotionAuthEpochOperation(Base):
    """Append-only operation identity ledger for replay rejection.

    The singleton is the currently active cutoff; this ledger retains every
    consumed promotion operation ID so an old operation cannot be replayed
    after a later Writer Witness term has advanced the singleton.
    """

    __tablename__ = "promotion_auth_epoch_operations"
    __table_args__ = (
        CheckConstraint("writer_epoch >= 1", name="ck_promotion_auth_epoch_ops_writer_epoch"),
        CheckConstraint(
            "minimum_token_iat >= 0",
            name="ck_promotion_auth_epoch_ops_minimum_token_iat",
        ),
        CheckConstraint(
            "writer_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_promotion_auth_epoch_ops_writer_site",
        ),
        CheckConstraint(
            "char_length(writer_lease_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epoch_ops_writer_lease",
        ),
        CheckConstraint(
            "char_length(witness_transition_id) BETWEEN 1 AND 128",
            name="ck_promotion_auth_epoch_ops_witness_transition",
        ),
        CheckConstraint(
            "char_length(operation_id) = 36",
            name="ck_promotion_auth_epoch_ops_operation_id",
        ),
        UniqueConstraint("operation_id", name="ux_promotion_auth_epoch_ops_operation"),
        UniqueConstraint("writer_epoch", name="ux_promotion_auth_epoch_ops_writer_epoch"),
    )

    id = Column(Integer, primary_key=True)
    operation_id = Column(String(36), nullable=False)
    writer_site = Column(String(16), nullable=False)
    writer_epoch = Column(BigInteger, nullable=False)
    writer_lease_id = Column(String(128), nullable=False)
    witness_transition_id = Column(String(128), nullable=False)
    cutover_at = Column(DateTime(timezone=True), nullable=False)
    minimum_token_iat = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
