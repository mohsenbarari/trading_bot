"""Accountant relation model for owner-accountant lifecycle."""

import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class AccountantRelationStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DELETED = "deleted"


class AccountantRelation(Base):
    """Tracks the owner-accountant relationship independently from invitations."""

    __tablename__ = "accountant_relations"
    __table_args__ = (
        Index("ix_accountant_relations_owner_status", "owner_user_id", "status"),
        Index("ix_accountant_relations_accountant_status", "accountant_user_id", "status"),
        Index("ix_accountant_relations_expires_at", "expires_at"),
        Index(
            "ux_accountant_relations_owner_display_active",
            "owner_user_id",
            "relation_display_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ux_accountant_relations_accountant_active",
            "accountant_user_id",
            unique=True,
            postgresql_where=text("accountant_user_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    accountant_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    invitation_token = Column(String, nullable=False, unique=True, index=True)
    global_account_name = Column(String, nullable=False, index=True)
    relation_display_name = Column(String, nullable=False)
    duty_description = Column(String(255), nullable=True)
    mobile_number = Column(String, nullable=False, index=True)
    status = Column(Enum(AccountantRelationStatus), nullable=False, default=AccountantRelationStatus.PENDING, index=True)

    expires_at = Column(DateTime(timezone=True), nullable=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    owner_user = relationship("User", foreign_keys=[owner_user_id])
    accountant_user = relationship("User", foreign_keys=[accountant_user_id])
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])