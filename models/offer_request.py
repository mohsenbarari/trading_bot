"""Durable request ledger for attempts against offers."""
from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
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

from core.offer_request_identity import generate_offer_request_public_id

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

    # Overtime workflow. A request waits in the owner queue, is delivered, is
    # presented with a decision deadline, and then reaches one terminal state.
    # Success reuses COMPLETED_TRADE so there is one meaning of "a trade
    # happened" across both workflows.
    OVERTIME_QUEUED = "overtime_queued"
    OVERTIME_DELIVERING = "overtime_delivering"
    OVERTIME_PRESENTED = "overtime_presented"
    OVERTIME_REJECTED_BY_OWNER = "overtime_rejected_by_owner"
    OVERTIME_DECISION_EXPIRED = "overtime_decision_expired"
    OVERTIME_CANCELLED_BY_REQUESTER = "overtime_cancelled_by_requester"
    OVERTIME_INVALIDATED = "overtime_invalidated"
    OVERTIME_DELIVERY_EXPIRED = "overtime_delivery_expired"
    OVERTIME_REJECTED_REQUESTER_LIMIT = "overtime_rejected_requester_limit"


class OfferRequestWorkflow(str, enum.Enum):
    """Which path decided the request: immediate execution, or owner approval."""

    DIRECT = "direct"
    OVERTIME = "overtime"


#: States in which an overtime request still holds its offer's logical lock and
#: counts against the requester's outstanding limits. Nothing outside this set
#: may keep an offer reserved.
OVERTIME_NONTERMINAL_STATUSES = (
    OfferRequestStatus.OVERTIME_QUEUED,
    OfferRequestStatus.OVERTIME_DELIVERING,
    OfferRequestStatus.OVERTIME_PRESENTED,
)

#: States in which the owner is being asked, or is about to be asked, to decide.
#: At most one of these may exist per (economic owner, offer home server).
OVERTIME_OWNER_OCCUPYING_STATUSES = (
    OfferRequestStatus.OVERTIME_DELIVERING,
    OfferRequestStatus.OVERTIME_PRESENTED,
)

#: Terminal overtime outcomes. Success reuses COMPLETED_TRADE and is listed
#: separately by the ledger service's full terminal set.
OVERTIME_TERMINAL_STATUSES = (
    OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
    OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
    OfferRequestStatus.OVERTIME_CANCELLED_BY_REQUESTER,
    OfferRequestStatus.OVERTIME_INVALIDATED,
    OfferRequestStatus.OVERTIME_DELIVERY_EXPIRED,
    OfferRequestStatus.OVERTIME_REJECTED_REQUESTER_LIMIT,
)

#: Terminals that start the requester-offer cooldown (decision 12).
OVERTIME_COOLDOWN_TRIGGER_STATUSES = (
    OfferRequestStatus.OVERTIME_REJECTED_BY_OWNER,
    OfferRequestStatus.OVERTIME_DECISION_EXPIRED,
)


def _status_sql_list(statuses) -> str:
    return ", ".join(f"'{status.value}'" for status in statuses)


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
        # One live overtime request per offer, regardless of lot or quantity.
        # Compare enum labels directly: `result_status::text` is not IMMUTABLE
        # and PostgreSQL rejects it in index predicates.
        Index(
            "ux_offer_requests_overtime_active_per_offer",
            "request_home_server",
            "offer_public_id",
            unique=True,
            postgresql_where=text(
                f"result_status IN ({_status_sql_list(OVERTIME_NONTERMINAL_STATUSES)})"
            ),
        ),
        # One request in front of any owner at a time, scoped to the offer home
        # server so a bot-home and a webapp-home offer can be decided together.
        Index(
            "ux_offer_requests_overtime_owner_occupied",
            "request_home_server",
            "offer_owner_user_id",
            unique=True,
            postgresql_where=text(
                f"result_status IN ({_status_sql_list(OVERTIME_OWNER_OCCUPYING_STATUSES)})"
            ),
        ),
        # FIFO promotion lookup within one owner queue.
        Index(
            "ix_offer_requests_overtime_queue_order",
            "request_home_server",
            "offer_owner_user_id",
            "queue_sequence",
            postgresql_where=text(
                f"result_status = '{OfferRequestStatus.OVERTIME_QUEUED.value}'"
            ),
        ),
        # Counting a requester's outstanding requests for the concurrency limits.
        Index(
            "ix_offer_requests_overtime_open_by_requester",
            "requester_user_id",
            postgresql_where=text(
                f"result_status IN ({_status_sql_list(OVERTIME_NONTERMINAL_STATUSES)})"
            ),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, nullable=False, default=1)

    # Opaque identifier: the only form a client or callback payload ever sees.
    request_public_id = Column(
        String(40),
        nullable=True,
        unique=True,
        index=True,
        default=generate_offer_request_public_id,
    )

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

    # ===== Overtime workflow =====
    workflow_kind = Column(
        Enum(
            OfferRequestWorkflow,
            name="offerrequestworkflow",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=OfferRequestWorkflow.DIRECT,
        server_default=OfferRequestWorkflow.DIRECT.value,
    )

    # Economic owner of the offer, snapshotted so the owner queue can be scoped
    # without joining offers and still resolves after the offer row is gone.
    offer_owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    offer_owner_user = relationship("User", foreign_keys=[offer_owner_user_id])

    # Monotonic within one owner queue; decides FIFO promotion order.
    queue_sequence = Column(BigInteger, nullable=True)

    # Set when the request becomes actionable for the owner. The decision clock
    # starts here, not at creation, so a queued request never burns its window.
    presented_at = Column(DateTime(timezone=True), nullable=True)
    decision_deadline_at = Column(DateTime(timezone=True), nullable=True)

    decided_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by_user = relationship("User", foreign_keys=[decided_by_user_id])
    terminal_reason = Column(String(64), nullable=True)

    # Local delivery references. The owner's Telegram clock may only start once
    # a message id is recorded here.
    telegram_delivery_job_id = Column(
        Integer,
        ForeignKey("telegram_delivery_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_message_id = Column(BigInteger, nullable=True)
    # Local-only receipt for the requester's private status message (queue mode).
    # Used to edit M10→M11 and terminal texts without a second send.
    requester_status_outbox_id = Column(Integer, nullable=True)

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
