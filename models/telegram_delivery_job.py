"""Foreign-local durable execution jobs for the shared Telegram queue.

The table is intentionally execution-only and must never be synchronized back
to Iran. Domain intents remain in their authoritative domain tables; this row
owns only the foreign Telegram execution lifecycle.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    JSON,
    Sequence,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)

from .database import Base


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


telegram_delivery_jobs_enqueued_seq = Sequence("telegram_delivery_jobs_enqueued_seq_seq")


class TelegramDeliveryJobRecord(Base):
    __tablename__ = "telegram_delivery_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="ux_telegram_delivery_jobs_dedupe_key"),
        UniqueConstraint(
            "feeder_kind",
            "source_natural_id",
            "source_version",
            "action_kind",
            "destination_key",
            name="ux_telegram_delivery_jobs_logical_identity",
        ),
        CheckConstraint("source_version >= 0", name="ck_telegram_delivery_jobs_source_version"),
        CheckConstraint("feeder_rank >= 0", name="ck_telegram_delivery_jobs_feeder_rank"),
        CheckConstraint("priority BETWEEN 0 AND 7", name="ck_telegram_delivery_jobs_priority"),
        CheckConstraint("priority_rank >= 0", name="ck_telegram_delivery_jobs_priority_rank"),
        CheckConstraint("attempt_count >= 0", name="ck_telegram_delivery_jobs_attempt_count"),
        CheckConstraint("lease_token >= 0", name="ck_telegram_delivery_jobs_lease_token"),
        CheckConstraint(
            "bot_identity IN ('primary', 'channel_editor')",
            name="ck_telegram_delivery_jobs_bot_identity",
        ),
        CheckConstraint(
            "(retention_legal_hold = false AND "
            "retention_hold_reason_code IS NULL AND retention_hold_set_at IS NULL) OR "
            "(retention_legal_hold = true AND "
            "retention_hold_reason_code IS NOT NULL AND retention_hold_set_at IS NOT NULL)",
            name="ck_telegram_delivery_jobs_retention_hold",
        ),
        CheckConstraint(
            "bot_identity = 'primary' OR ("
            "destination_class = 'channel' AND "
            "method IN ('editMessageText', 'editMessageReplyMarkup') AND "
            "action_kind IN ('partial_offer_edit', 'traded_offer_edit', "
            "'expired_offer_edit', 'cancelled_offer_edit', 'other_active_offer_edit', "
            "'invalid_action_button_edit', 'reconciliation_edit'))",
            name="ck_telegram_delivery_jobs_editor_route",
        ),
        Index(
            "ix_telegram_delivery_jobs_claim",
            "bot_identity",
            "priority",
            "priority_rank",
            "delivery_deadline_at",
            "eligible_at",
            "next_retry_at",
            "enqueued_seq",
            postgresql_where=text("state IN ('pending', 'pending_retry')"),
        ),
        Index(
            "ix_telegram_delivery_jobs_offer_edit_order",
            "bot_identity",
            "priority",
            "priority_rank",
            text("source_order_at DESC"),
            "enqueued_seq",
            postgresql_where=text(
                "state IN ('pending', 'pending_retry') AND "
                "feeder_kind = 'offer_edit'"
            ),
        ),
        Index(
            "ix_telegram_delivery_jobs_lease_recovery",
            "lease_until",
            "id",
            postgresql_where=text("state = 'leased' AND lease_until IS NOT NULL"),
        ),
        Index(
            "ix_telegram_delivery_jobs_source",
            "feeder_kind",
            "source_natural_id",
            "source_version",
        ),
        Index("ix_telegram_delivery_jobs_campaign", "campaign_id", "state", "enqueued_seq"),
        Index(
            "ix_telegram_delivery_jobs_bot_destination_state",
            "bot_identity",
            "destination_key",
            "state",
            "next_retry_at",
        ),
        Index(
            "ix_telegram_delivery_jobs_destination_gate",
            "destination_key",
            "state",
            "next_retry_at",
            "id",
            postgresql_where=text(
                "(dispatch_started_at IS NOT NULL AND state IN "
                "('leased', 'ambiguous', 'ambiguous_unresolved', "
                "'pending_reconcile')) OR "
                "(state = 'pending_retry' AND "
                "outcome_reason = 'telegram_rate_limited' AND "
                "next_retry_at IS NOT NULL) OR "
                "state = 'blocked_destination'"
            ),
        ),
        Index(
            "ix_telegram_delivery_jobs_hard_pause_gate",
            "state",
            "bot_identity",
            "destination_key",
            "id",
            postgresql_where=text(
                "state IN ('blocked_destination', 'blocked_bot', "
                "'blocked_gateway')"
            ),
        ),
        Index(
            "ix_telegram_delivery_jobs_bot_cooldown",
            "bot_identity",
            "bot_cooldown_until",
            "id",
            postgresql_where=text("bot_cooldown_until IS NOT NULL"),
        ),
        Index(
            "ix_telegram_delivery_jobs_recent_rate_limit",
            "bot_identity",
            "last_rate_limited_at",
            "destination_key",
            "id",
            postgresql_where=text("last_rate_limited_at IS NOT NULL"),
        ),
        Index(
            "ix_telegram_delivery_jobs_bot_probe_gate",
            "bot_identity",
            "state",
            "lease_until",
            "id",
            postgresql_where=text(
                "rate_limit_probe = true AND dispatch_started_at IS NOT NULL AND "
                "state IN ('leased', 'ambiguous', 'ambiguous_unresolved', "
                "'pending_reconcile')"
            ),
        ),
        Index("ix_telegram_delivery_jobs_run", "run_id", "state"),
        Index(
            "ix_telegram_delivery_jobs_retention",
            "terminal_at",
            "id",
            postgresql_where=text(
                "terminal_at IS NOT NULL AND retention_legal_hold = false"
            ),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    enqueued_seq = Column(
        BigInteger,
        telegram_delivery_jobs_enqueued_seq,
        nullable=False,
        server_default=telegram_delivery_jobs_enqueued_seq.next_value(),
    )
    dedupe_key = Column(String(1024), nullable=False)
    feeder_kind = Column(
        Enum(TelegramFeederKind, name="telegramdeliveryfeederkind", values_callable=_enum_values),
        nullable=False,
    )
    feeder_rank = Column(SmallInteger, nullable=False)
    source_natural_id = Column(String(256), nullable=False)
    source_version = Column(BigInteger, nullable=False)
    action_kind = Column(
        Enum(TelegramDeliveryAction, name="telegramdeliveryaction", values_callable=_enum_values),
        nullable=False,
    )
    bot_identity = Column(String(128), nullable=False)
    destination_key = Column(String(256), nullable=False)
    destination_class = Column(
        Enum(TelegramDestinationClass, name="telegramdestinationclass", values_callable=_enum_values),
        nullable=False,
    )
    method = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    template_version = Column(String(64), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    priority = Column(SmallInteger, nullable=False)
    priority_rank = Column(SmallInteger, nullable=False)
    delivery_deadline_at = Column(DateTime(timezone=True), nullable=True)
    eligible_at = Column(DateTime(timezone=True), nullable=True)
    freshness_deadline_at = Column(DateTime(timezone=True), nullable=True)
    # Domain creation time used only as a global, cross-feeder-cycle ordering
    # key. Nullable preserves stop-old/drain/start-new schema compatibility;
    # every new offer_edit enqueue must provide it.
    source_order_at = Column(DateTime(timezone=True), nullable=True)
    campaign_id = Column(String(192), nullable=True)
    run_id = Column(String(192), nullable=True)
    state = Column(
        Enum(TelegramDeliveryState, name="telegramdeliverystate", values_callable=_enum_values),
        nullable=False,
        default=TelegramDeliveryState.PENDING,
        server_default=text("'pending'"),
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    worker_id = Column(String(128), nullable=True)
    lease_token = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    lease_until = Column(DateTime(timezone=True), nullable=True)
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    rate_limit_probe = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    provider_ok = Column(Boolean, nullable=True)
    provider_status_code = Column(Integer, nullable=True)
    provider_error_code = Column(Integer, nullable=True)
    provider_response = Column(JSON, nullable=True)
    last_retry_after_seconds = Column(BigInteger, nullable=True)
    last_rate_limited_at = Column(DateTime(timezone=True), nullable=True)
    last_rate_limit_until = Column(DateTime(timezone=True), nullable=True)
    bot_cooldown_until = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error_message = Column(Text, nullable=True)
    outcome_reason = Column(String(160), nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    payload_redacted_at = Column(DateTime(timezone=True), nullable=True)
    retention_legal_hold = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    retention_hold_reason_code = Column(String(64), nullable=True)
    retention_hold_set_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
