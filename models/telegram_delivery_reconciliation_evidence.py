"""Append-only audit evidence for Telegram ambiguity reconciliation."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from .database import Base


class TelegramDeliveryReconciliationEvidence(Base):
    __tablename__ = "telegram_delivery_reconciliation_evidence"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "evidence_hash",
            "decision_action",
            name="ux_telegram_delivery_reconciliation_evidence_identity",
        ),
        CheckConstraint(
            "actor_kind IN ('worker', 'operator')",
            name="ck_telegram_delivery_reconciliation_actor_kind",
        ),
        Index(
            "ix_telegram_delivery_reconciliation_evidence_job",
            "job_id",
            "created_at",
            "id",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_job_state = Column(String(48), nullable=False)
    evidence_kind = Column(String(64), nullable=False)
    evidence_hash = Column(String(64), nullable=False)
    decision_action = Column(String(64), nullable=False)
    actor_kind = Column(String(16), nullable=False)
    actor_ref_hash = Column(String(64), nullable=True)
    reason_code = Column(String(160), nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
