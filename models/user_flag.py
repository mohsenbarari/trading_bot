"""Durable, explainable flags that require an administrator's review."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class UserFlag(Base):
    """A review case, not an automatic account restriction.

    ``flag_type`` and ``reason_code`` are strings on purpose: future detectors
    can add reasons without changing the database enum.  At most one open case
    of a given type exists for a user; repeated observations update that case.
    """

    __tablename__ = "user_flags"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_user_flags_status",
        ),
        CheckConstraint("trigger_count >= 1", name="ck_user_flags_trigger_count_positive"),
        Index("ix_user_flags_status_last_flagged", "status", "last_flagged_at"),
        Index("ix_user_flags_user_type", "user_id", "flag_type"),
        Index(
            "ux_user_flags_open_user_type",
            "user_id",
            "flag_type",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    flag_type = Column(String(64), nullable=False)
    reason_code = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False, default="open", server_default=text("'open'"))
    severity = Column(String(24), nullable=False, default="warning", server_default=text("'warning'"))
    details = Column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    trigger_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    first_flagged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_flagged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("User", foreign_keys=[user_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_user_id])
