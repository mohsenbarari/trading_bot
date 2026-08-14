"""Durable foreign-local bot/gateway execution gates and resume journal."""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.sql import func

from .database import Base


ACTIVE_TELEGRAM_RUNTIME_GATE_STATES = (
    "cooldown",
    "blocked",
    "resume_requested",
    "database_applied",
)


class TelegramDeliveryRuntimeGate(Base):
    __tablename__ = "telegram_delivery_runtime_gates"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('bot', 'gateway')",
            name="ck_telegram_delivery_runtime_gates_scope",
        ),
        CheckConstraint(
            "state IN ('active', 'cooldown', 'blocked', 'resume_requested', "
            "'database_applied')",
            name="ck_telegram_delivery_runtime_gates_state",
        ),
        CheckConstraint(
            "((scope = 'bot' AND bot_identity IN ('primary', 'channel_editor') "
            "AND gate_key = 'bot:' || bot_identity) OR "
            "(scope = 'gateway' AND bot_identity IS NULL "
            "AND gate_key = 'gateway:telegram'))",
            name="ck_telegram_delivery_runtime_gates_identity",
        ),
        CheckConstraint(
            "version >= 0 AND attempt_count >= 0",
            name="ck_telegram_delivery_runtime_gates_counters",
        ),
        CheckConstraint(
            "evidence_hash IS NULL OR length(evidence_hash) = 64",
            name="ck_telegram_delivery_runtime_gates_evidence_hash",
        ),
        CheckConstraint(
            "requested_by_hash IS NULL OR length(requested_by_hash) = 64",
            name="ck_telegram_delivery_runtime_gates_requested_by_hash",
        ),
    )

    gate_key = Column(String(192), primary_key=True)
    scope = Column(String(16), nullable=False)
    bot_identity = Column(String(128), nullable=True)
    state = Column(String(32), nullable=False, default="active", server_default=text("'active'"))
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    reason_code = Column(String(160), nullable=True)
    provider_status_code = Column(Integer, nullable=True)
    retry_after_seconds = Column(Integer, nullable=True)
    evidence_hash = Column(String(64), nullable=True)
    version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    request_id = Column(String(128), nullable=True)
    requested_by_hash = Column(String(64), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    attempt_history = Column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    preflight_evidence = Column(JSON, nullable=True)
    resumed_job_ids = Column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::json"),
    )
    last_error_class = Column(String(160), nullable=True)
    last_error_detail = Column(Text, nullable=True)
    resume_requested_at = Column(DateTime(timezone=True), nullable=True)
    database_applied_at = Column(DateTime(timezone=True), nullable=True)
    redis_applied_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
