"""Iran-local durable result for one policy-controlled Invitation SMS."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.sql import func

from .database import Base


class InvitationSMSDelivery(Base):
    __tablename__ = "invitation_sms_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('disabled', 'pending', 'accepted', 'failed', 'ambiguous')",
            name="ck_invitation_sms_deliveries_known_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 1",
            name="ck_invitation_sms_deliveries_attempt_count",
        ),
        UniqueConstraint("invitation_id", name="ux_invitation_sms_deliveries_invitation_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    invitation_id = Column(
        Integer,
        ForeignKey("invitations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(16), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
