"""Customer relation model for owner-customer lifecycle."""

import enum

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _customer_relation_status_values(enum_cls):
    return [status.value for status in enum_cls]


def _customer_tier_values(enum_cls):
    return [tier.value for tier in enum_cls]


class CustomerTier(str, enum.Enum):
    TIER_1 = "tier1"
    TIER_2 = "tier2"


class CustomerRelationStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DELETED = "deleted"


class CustomerRelation(Base):
    """Tracks the owner-customer relationship independently from user identity."""

    __tablename__ = "customer_relations"
    __table_args__ = (
        CheckConstraint("sync_version >= 1", name="ck_customer_relations_sync_version_positive"),
        Index("ix_customer_relations_owner_status", "owner_user_id", "status"),
        Index("ix_customer_relations_customer_status", "customer_user_id", "status"),
        Index("ix_customer_relations_expires_at", "expires_at"),
        Index("ix_customer_relations_customer_tier", "customer_tier"),
        Index(
            "ux_customer_relations_owner_management_active",
            "owner_user_id",
            "management_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ux_customer_relations_customer_active",
            "customer_user_id",
            unique=True,
            postgresql_where=text("customer_user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    invitation_token = Column(String, nullable=False, unique=True, index=True)
    management_name = Column(String(120), nullable=False)
    customer_tier = Column(
        Enum(
            CustomerTier,
            name="customertier",
            values_callable=_customer_tier_values,
        ),
        nullable=False,
        default=CustomerTier.TIER_1,
        index=True,
    )
    commission_rate = Column(Numeric(5, 2), nullable=True)
    min_trade_quantity = Column(Integer, nullable=True)
    max_trade_quantity = Column(Integer, nullable=True)
    max_daily_trades = Column(Integer, nullable=True)
    max_daily_commodity_volume = Column(Integer, nullable=True)
    trading_restricted_until = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(
            CustomerRelationStatus,
            name="customerrelationstatus",
            values_callable=_customer_relation_status_values,
        ),
        nullable=False,
        default=CustomerRelationStatus.PENDING,
        index=True,
    )

    expires_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    sync_version = Column(BigInteger, nullable=False, default=1, server_default=text("1"))
    __mapper_args__ = {
        "version_id_col": sync_version,
        "version_id_generator": False,
    }

    owner_user = relationship("User", foreign_keys=[owner_user_id])
    customer_user = relationship("User", foreign_keys=[customer_user_id])
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
