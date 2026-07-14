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
    witness_lease_id = Column(String(64), nullable=True)
    witness_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    witness_proof_hash = Column(String(64), nullable=True)
    witness_transition_id = Column(String(64), nullable=True)
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
            "action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff', 'lease_refresh')",
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
    witness_proof_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WebappWriterWitnessState(Base):
    __tablename__ = "webapp_writer_witness_state"
    __table_args__ = (
        CheckConstraint("authority = 'webapp'", name="ck_webapp_writer_witness_authority"),
        CheckConstraint("writer_epoch >= 0", name="ck_webapp_writer_witness_epoch"),
        CheckConstraint(
            "holder_site IS NULL OR holder_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_webapp_writer_witness_holder",
        ),
        CheckConstraint(
            "lease_status IN ('vacant', 'leased', 'draining')",
            name="ck_webapp_writer_witness_status",
        ),
        CheckConstraint(
            "(lease_status = 'vacant' AND holder_site IS NULL AND lease_id IS NULL "
            "AND issued_at IS NULL AND expires_at IS NULL) OR "
            "(lease_status <> 'vacant' AND holder_site IS NOT NULL AND lease_id IS NOT NULL "
            "AND issued_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_webapp_writer_witness_consistency",
        ),
    )

    authority = Column(String(16), primary_key=True, default="webapp")
    holder_site = Column(String(16), nullable=True)
    writer_epoch = Column(BigInteger, nullable=False, default=0)
    lease_id = Column(String(64), nullable=True)
    lease_status = Column(String(16), nullable=False, default="vacant")
    issued_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    transition_id = Column(String(64), nullable=False)
    updated_by = Column(String(128), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WebappWriterWitnessReceipt(Base):
    __tablename__ = "webapp_writer_witness_receipts"
    __table_args__ = (
        CheckConstraint(
            "action IN ('acquire', 'renew', 'drain')",
            name="ck_webapp_writer_witness_receipt_action",
        ),
        Index("ix_webapp_writer_witness_receipts_created_at", "created_at"),
    )

    request_id = Column(String(64), primary_key=True)
    request_hash = Column(String(64), nullable=False)
    action = Column(String(16), nullable=False)
    transition_id = Column(String(64), nullable=False)
    response_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
