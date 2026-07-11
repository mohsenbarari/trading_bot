"""Iran-local natural-identity reservation for pending invitations."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from .database import Base


class InvitationIdentityReservation(Base):
    __tablename__ = "invitation_identity_reservations"
    __table_args__ = (
        CheckConstraint(
            "length(btrim(normalized_mobile)) > 0",
            name="ck_invitation_identity_reservations_mobile_not_blank",
        ),
        CheckConstraint(
            "length(btrim(normalized_account_name)) > 0",
            name="ck_invitation_identity_reservations_account_not_blank",
        ),
        UniqueConstraint("invitation_id", name="ux_invitation_identity_reservations_invitation_id"),
        UniqueConstraint("normalized_mobile", name="ux_invitation_identity_reservations_mobile"),
        UniqueConstraint("normalized_account_name", name="ux_invitation_identity_reservations_account"),
        Index("ix_invitation_identity_reservations_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    invitation_id = Column(
        Integer,
        ForeignKey("invitations.id", ondelete="CASCADE"),
        nullable=False,
    )
    normalized_mobile = Column(String(32), nullable=False)
    normalized_account_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
