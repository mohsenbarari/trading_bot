"""Local durable observation and audit of WebApp writer ownership."""

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, String, Text
from sqlalchemy.sql import func

from .database import Base


class WebappWriterState(Base):
    __tablename__ = "webapp_writer_state"
    __table_args__ = (
        CheckConstraint("authority = 'webapp'", name="ck_webapp_writer_state_authority"),
        CheckConstraint("writer_epoch >= 1", name="ck_webapp_writer_state_epoch_positive"),
        CheckConstraint(
            "active_site IS NULL OR active_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_webapp_writer_state_active_site",
        ),
        CheckConstraint(
            "control_state IN ('active', 'fenced', 'handoff')",
            name="ck_webapp_writer_state_control_state",
        ),
        CheckConstraint(
            "(control_state = 'active' AND active_site IS NOT NULL) OR "
            "(control_state <> 'active' AND active_site IS NULL)",
            name="ck_webapp_writer_state_active_consistency",
        ),
    )

    authority = Column(String(16), primary_key=True, default="webapp")
    active_site = Column(String(16), nullable=True)
    writer_epoch = Column(BigInteger, nullable=False)
    control_state = Column(String(16), nullable=False)
    transition_id = Column(String(36), nullable=False)
    readiness_evidence_hash = Column(String(64), nullable=True)
    readiness_evidence_id = Column(String(64), nullable=True)
    readiness_approved_by = Column(String(128), nullable=True)
    readiness_approved_at = Column(DateTime(timezone=True), nullable=True)
    readiness_expires_at = Column(DateTime(timezone=True), nullable=True)
    updated_by = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WebappWriterTransition(Base):
    __tablename__ = "webapp_writer_transitions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff')",
            name="ck_webapp_writer_transitions_action",
        ),
        CheckConstraint(
            "previous_epoch >= 1 AND new_epoch >= previous_epoch",
            name="ck_webapp_writer_transitions_epoch",
        ),
        Index("ix_webapp_writer_transitions_created_at", "created_at"),
    )

    transition_id = Column(String(36), primary_key=True)
    authority = Column(String(16), nullable=False, default="webapp")
    action = Column(String(16), nullable=False)
    previous_active_site = Column(String(16), nullable=True)
    new_active_site = Column(String(16), nullable=True)
    previous_epoch = Column(BigInteger, nullable=False)
    new_epoch = Column(BigInteger, nullable=False)
    operator = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    evidence_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
