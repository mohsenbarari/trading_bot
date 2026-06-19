"""Durable request ledger for attempts against offers."""
from __future__ import annotations

import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class OfferRequestStatus(str, enum.Enum):
    RECEIVED = "received"
    AUTHORIZED = "authorized"
    REJECTED_BUSINESS_RULE = "rejected_business_rule"
    REJECTED_OFFER_EXPIRED = "rejected_offer_expired"
    REJECTED_LOT_UNAVAILABLE = "rejected_lot_unavailable"
    REJECTED_CONFLICT = "rejected_conflict"
    COMPLETED_TRADE = "completed_trade"
    DUPLICATE_REPLAY = "duplicate_replay"
    FAILED_INTERNAL = "failed_internal"


class OfferRequestSourceSurface(str, enum.Enum):
    WEBAPP = "webapp"
    TELEGRAM_BOT = "telegram_bot"
    INTERNAL_FORWARD = "internal_forward"


class OfferRequest(Base):
    __tablename__ = "offer_requests"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_offer_requests_requested_quantity_positive"),
        Index("ix_offer_requests_offer_public_id", "offer_public_id"),
        Index("ix_offer_requests_local_offer_id", "local_offer_id"),
        Index("ix_offer_requests_requester_user_id", "requester_user_id"),
        Index("ix_offer_requests_actor_user_id", "actor_user_id"),
        Index("ix_offer_requests_received_at", "received_at"),
        Index("ix_offer_requests_result_status", "result_status"),
        Index(
            "ux_offer_requests_home_idempotency_key",
            "request_home_server",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, nullable=False, default=1)

    request_home_server = Column(String(16), nullable=False, index=True)
    local_offer_id = Column(Integer, ForeignKey("offers.id", ondelete="SET NULL"), nullable=True)
    offer_public_id = Column(String(40), nullable=False)
    offer = relationship("Offer", foreign_keys=[local_offer_id])

    requester_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requester_user = relationship("User", foreign_keys=[requester_user_id])
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_user = relationship("User", foreign_keys=[actor_user_id])

    request_source_surface = Column(
        Enum(
            OfferRequestSourceSurface,
            name="offerrequestsourcesurface",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    request_source_server = Column(String(16), nullable=False)
    requested_quantity = Column(Integer, nullable=False)
    idempotency_key = Column(String(128), nullable=True)

    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)
    result_status = Column(
        Enum(
            OfferRequestStatus,
            name="offerrequeststatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OfferRequestStatus.RECEIVED,
        index=True,
    )

    public_failure_code = Column(String(64), nullable=True)
    public_failure_message = Column(String(240), nullable=True)
    internal_failure_code = Column(String(96), nullable=True)
    internal_failure_context = Column(JSON, nullable=True)
    resulting_trade_id = Column(Integer, ForeignKey("trades.id", ondelete="SET NULL"), nullable=True, index=True)
    resulting_trade = relationship("Trade", foreign_keys=[resulting_trade_id])

    customer_relation_id = Column(Integer, ForeignKey("customer_relations.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_relation = relationship("CustomerRelation", foreign_keys=[customer_relation_id])
    customer_owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_owner_user = relationship("User", foreign_keys=[customer_owner_user_id])
    customer_tier_snapshot = Column(String(32), nullable=True)
    customer_management_name_snapshot = Column(String(120), nullable=True)
    customer_commission_rate_snapshot = Column(Numeric(5, 2), nullable=True)
    customer_commission_context = Column(JSON, nullable=True)

    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __mapper_args__ = {
        "version_id_col": version_id,
    }
