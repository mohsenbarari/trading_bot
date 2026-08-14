"""Read-only health, alert, and shadow-order evidence for Telegram delivery."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Mapping

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.metrics import registry
from core.telegram_delivery_queue_contract import (
    CLAIMABLE_DELIVERY_STATES,
    FINAL_DELIVERY_STATES,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_runtime_gate import (
    ACTIVE_TELEGRAM_RUNTIME_GATE_STATES,
    TelegramDeliveryRuntimeGate,
)

from .telegram_delivery_queue_service import _enum_value, _require_foreign


_SHADOW_HASH_DOMAIN = b"telegram-delivery-shadow-v1\x00"
_TERMINAL_FAILURE_STATES = (
    TelegramDeliveryState.PERMANENT_UNDELIVERABLE,
    TelegramDeliveryState.TERMINAL_FAILED,
    TelegramDeliveryState.QUARANTINED,
)
_BLOCKED_STATES = (
    TelegramDeliveryState.BLOCKED_DESTINATION,
    TelegramDeliveryState.BLOCKED_BOT,
    TelegramDeliveryState.BLOCKED_GATEWAY,
)
_AMBIGUOUS_STATES = (
    TelegramDeliveryState.AMBIGUOUS,
    TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
    TelegramDeliveryState.PENDING_RECONCILE,
)


@dataclass(frozen=True, slots=True)
class TelegramQueueHealthThresholds:
    warning_ready_depth: int = 500
    stop_ready_depth: int = 5_000
    warning_oldest_ready_age_seconds: float = 10.0
    stop_oldest_ready_age_seconds: float = 30.0
    stop_ambiguous_count: int = 0
    stop_unresolved_count: int = 0
    stop_blocked_count: int = 0
    stop_missed_freshness_count: int = 0
    stop_terminal_failure_count: int = 0
    stop_expired_lease_count: int = 10
    stop_provider_outcome_age_seconds: float = 30.0

    def __post_init__(self) -> None:
        numeric = asdict(self)
        for name, value in numeric.items():
            if isinstance(value, bool) or float(value) < 0:
                raise ValueError(f"telegram_health_threshold_invalid:{name}")
        if self.warning_ready_depth > self.stop_ready_depth:
            raise ValueError("telegram_health_ready_warning_exceeds_stop")
        if (
            self.warning_oldest_ready_age_seconds
            > self.stop_oldest_ready_age_seconds
        ):
            raise ValueError("telegram_health_age_warning_exceeds_stop")


@dataclass(frozen=True, slots=True)
class TelegramQueueHealthAlert:
    code: str
    severity: str
    observed: float
    threshold: float


@dataclass(frozen=True, slots=True)
class TelegramQueueShadowCandidate:
    lane_rank: int
    bot_role: str
    effective_priority: int
    action: str
    destination_class: str
    method: str
    correlation_hash: str


def _age_seconds(value: datetime | None, *, now: datetime) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


def _bounded_method(value: Any) -> str:
    method = str(value or "")
    allowed = {
        "sendMessage",
        "editMessageText",
        "editMessageReplyMarkup",
        "deleteMessage",
        "answerCallbackQuery",
        "banChatMember",
        "unbanChatMember",
    }
    return method if method in allowed else "other"


def _correlation_hash(value: Any) -> str:
    digest = hashlib.sha256()
    digest.update(_SHADOW_HASH_DOMAIN)
    digest.update(str(value or "").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _retry_after_bucket(value: Any) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if seconds <= 1:
        return "le_1s"
    if seconds <= 5:
        return "le_5s"
    if seconds <= 30:
        return "le_30s"
    if seconds <= 120:
        return "le_120s"
    return "gt_120s"


def _alerts(
    snapshot: Mapping[str, Any],
    thresholds: TelegramQueueHealthThresholds,
) -> tuple[TelegramQueueHealthAlert, ...]:
    alerts: list[TelegramQueueHealthAlert] = []

    def add_if(
        *, code: str, severity: str, observed: float, threshold: float
    ) -> None:
        if observed > threshold:
            alerts.append(
                TelegramQueueHealthAlert(
                    code=code,
                    severity=severity,
                    observed=float(observed),
                    threshold=float(threshold),
                )
            )

    ready_depth = float(snapshot["ready_depth"])
    oldest_ready = float(snapshot["oldest_ready_age_seconds"])
    add_if(
        code="ready_depth_stop",
        severity="critical",
        observed=ready_depth,
        threshold=thresholds.stop_ready_depth,
    )
    if ready_depth <= thresholds.stop_ready_depth:
        add_if(
            code="ready_depth_warning",
            severity="warning",
            observed=ready_depth,
            threshold=thresholds.warning_ready_depth,
        )
    add_if(
        code="oldest_ready_age_stop",
        severity="critical",
        observed=oldest_ready,
        threshold=thresholds.stop_oldest_ready_age_seconds,
    )
    if oldest_ready <= thresholds.stop_oldest_ready_age_seconds:
        add_if(
            code="oldest_ready_age_warning",
            severity="warning",
            observed=oldest_ready,
            threshold=thresholds.warning_oldest_ready_age_seconds,
        )
    for code, field, threshold in (
        ("ambiguous_delivery_stop", "ambiguous_count", thresholds.stop_ambiguous_count),
        ("unresolved_delivery_stop", "unresolved_count", thresholds.stop_unresolved_count),
        ("blocked_delivery_stop", "blocked_count", thresholds.stop_blocked_count),
        (
            "missed_freshness_deadline_stop",
            "missed_freshness_count",
            thresholds.stop_missed_freshness_count,
        ),
        (
            "terminal_failure_stop",
            "terminal_failure_count",
            thresholds.stop_terminal_failure_count,
        ),
        (
            "expired_lease_stop",
            "expired_lease_count",
            thresholds.stop_expired_lease_count,
        ),
    ):
        add_if(
            code=code,
            severity="critical",
            observed=float(snapshot[field]),
            threshold=float(threshold),
        )
    add_if(
        code="provider_outcome_apply_age_stop",
        severity="critical",
        observed=float(snapshot["oldest_pending_provider_outcome_age_seconds"]),
        threshold=thresholds.stop_provider_outcome_age_seconds,
    )
    return tuple(sorted(alerts, key=lambda alert: (alert.severity, alert.code)))


async def inspect_telegram_delivery_queue_health(
    db: AsyncSession,
    *,
    current_server: str,
    thresholds: TelegramQueueHealthThresholds | None = None,
    sample_window_seconds: float = 60.0,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded-label snapshot using SELECT statements only."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    window_seconds = max(1.0, float(sample_window_seconds))
    window_start = current_time - timedelta(seconds=window_seconds)
    active_states = tuple(
        state for state in TelegramDeliveryState if state not in FINAL_DELIVERY_STATES
    )
    job_scope_filter = (
        (TelegramDeliveryJobRecord.run_id == str(run_id)),
    ) if run_id is not None else ()
    ready_filter = (
        *job_scope_filter,
        TelegramDeliveryJobRecord.state.in_(tuple(CLAIMABLE_DELIVERY_STATES)),
        or_(
            TelegramDeliveryJobRecord.eligible_at.is_(None),
            TelegramDeliveryJobRecord.eligible_at <= current_time,
        ),
        or_(
            TelegramDeliveryJobRecord.next_retry_at.is_(None),
            TelegramDeliveryJobRecord.next_retry_at <= current_time,
        ),
    )

    state_rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.state,
                func.count(TelegramDeliveryJobRecord.id),
            )
            .where(*job_scope_filter)
            .group_by(TelegramDeliveryJobRecord.state)
        )
    ).all()
    state_counts = {
        _enum_value(state): int(count) for state, count in state_rows
    }
    active_count = sum(state_counts.get(state.value, 0) for state in active_states)

    pair_rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.priority,
                TelegramDeliveryJobRecord.destination_class,
                func.count(TelegramDeliveryJobRecord.id),
                func.min(TelegramDeliveryJobRecord.created_at),
            )
            .where(*ready_filter)
            .group_by(
                TelegramDeliveryJobRecord.priority,
                TelegramDeliveryJobRecord.destination_class,
            )
            .order_by(
                TelegramDeliveryJobRecord.priority.asc(),
                TelegramDeliveryJobRecord.destination_class.asc(),
            )
        )
    ).all()
    ready_by_priority_destination = [
        {
            "priority": int(priority),
            "destination_class": _enum_value(destination_class),
            "depth": int(count),
            "oldest_age_seconds": _age_seconds(oldest, now=current_time),
        }
        for priority, destination_class, count, oldest in pair_rows
    ]
    ready_depth = sum(row["depth"] for row in ready_by_priority_destination)
    oldest_ready_age = max(
        (row["oldest_age_seconds"] for row in ready_by_priority_destination),
        default=0.0,
    )

    async def grouped_counts(column) -> dict[str, int]:
        rows = (
            await db.execute(
                select(column, func.count(TelegramDeliveryJobRecord.id))
                .where(*ready_filter)
                .group_by(column)
                .order_by(column.asc())
            )
        ).all()
        return {_enum_value(key): int(count) for key, count in rows}

    ready_by_action = await grouped_counts(TelegramDeliveryJobRecord.action_kind)
    ready_by_bot_role = await grouped_counts(TelegramDeliveryJobRecord.bot_identity)
    method_rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.method,
                func.count(TelegramDeliveryJobRecord.id),
            )
            .where(*ready_filter)
            .group_by(TelegramDeliveryJobRecord.method)
        )
    ).all()
    ready_by_method: dict[str, int] = {}
    for method, count in method_rows:
        key = _bounded_method(method)
        ready_by_method[key] = ready_by_method.get(key, 0) + int(count)

    aggregate = (
        await db.execute(
            select(
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state.in_(_AMBIGUOUS_STATES)
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state
                    == TelegramDeliveryState.AMBIGUOUS_UNRESOLVED
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state.in_(_BLOCKED_STATES)
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state.in_(_TERMINAL_FAILURE_STATES),
                    TelegramDeliveryJobRecord.terminal_at.is_not(None),
                    TelegramDeliveryJobRecord.terminal_at >= window_start,
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.LEASED,
                    TelegramDeliveryJobRecord.lease_until.is_not(None),
                    TelegramDeliveryJobRecord.lease_until <= current_time,
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.state.in_(active_states),
                    TelegramDeliveryJobRecord.freshness_deadline_at.is_not(None),
                    TelegramDeliveryJobRecord.freshness_deadline_at <= current_time,
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.last_rate_limited_at.is_not(None),
                    TelegramDeliveryJobRecord.last_rate_limited_at >= window_start,
                ),
                func.coalesce(
                    func.sum(TelegramDeliveryJobRecord.provider_attempt_count).filter(
                        TelegramDeliveryJobRecord.updated_at >= window_start
                    ),
                    0,
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.created_at >= window_start
                ),
                func.count(TelegramDeliveryJobRecord.id).filter(
                    TelegramDeliveryJobRecord.sent_at.is_not(None),
                    TelegramDeliveryJobRecord.sent_at >= window_start,
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.SENT,
                ),
            ).where(*job_scope_filter)
        )
    ).one()
    (
        ambiguous_count,
        unresolved_count,
        blocked_count,
        terminal_failure_count,
        expired_lease_count,
        missed_freshness_count,
        rate_limited_job_count,
        provider_attempt_count,
        ingress_count,
        sent_count,
    ) = (int(value or 0) for value in aggregate)

    retry_rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.last_retry_after_seconds,
                func.count(TelegramDeliveryJobRecord.id),
            )
            .where(
                *job_scope_filter,
                TelegramDeliveryJobRecord.last_rate_limited_at.is_not(None),
            )
            .group_by(TelegramDeliveryJobRecord.last_retry_after_seconds)
        )
    ).all()
    retry_after_buckets: dict[str, int] = {}
    for retry_after, count in retry_rows:
        bucket = _retry_after_bucket(retry_after)
        retry_after_buckets[bucket] = retry_after_buckets.get(bucket, 0) + int(count)

    provider_stmt = select(
        func.count(TelegramDeliveryProviderOutcomeRecord.id),
        func.min(TelegramDeliveryProviderOutcomeRecord.created_at),
    ).where(
        TelegramDeliveryProviderOutcomeRecord.apply_state
        == TELEGRAM_PROVIDER_OUTCOME_PENDING
    )
    if run_id is not None:
        provider_stmt = provider_stmt.join(
            TelegramDeliveryJobRecord,
            TelegramDeliveryJobRecord.id
            == TelegramDeliveryProviderOutcomeRecord.job_id,
        ).where(TelegramDeliveryJobRecord.run_id == str(run_id))
    pending_provider_count, oldest_pending_provider = (
        await db.execute(provider_stmt)
    ).one()
    gate_rows = (
        await db.execute(
            select(
                TelegramDeliveryRuntimeGate.scope,
                TelegramDeliveryRuntimeGate.state,
                func.count(TelegramDeliveryRuntimeGate.gate_key),
            )
            .where(
                TelegramDeliveryRuntimeGate.state.in_(
                    ACTIVE_TELEGRAM_RUNTIME_GATE_STATES
                )
            )
            .group_by(
                TelegramDeliveryRuntimeGate.scope,
                TelegramDeliveryRuntimeGate.state,
            )
            .order_by(
                TelegramDeliveryRuntimeGate.scope.asc(),
                TelegramDeliveryRuntimeGate.state.asc(),
            )
        )
    ).all()
    active_runtime_gates = [
        {"scope": str(scope), "state": str(state), "count": int(count)}
        for scope, state, count in gate_rows
    ]

    goodput = sent_count / window_seconds
    ingress_rate = ingress_count / window_seconds
    drain_estimate = ready_depth / goodput if ready_depth and goodput > 0 else None
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "observed_at": current_time.isoformat(),
        "scope": "run" if run_id is not None else "all",
        "sample_window_seconds": window_seconds,
        "active_count": active_count,
        "ready_depth": ready_depth,
        "oldest_ready_age_seconds": oldest_ready_age,
        "ambiguous_count": ambiguous_count,
        "unresolved_count": unresolved_count,
        "blocked_count": blocked_count,
        "terminal_failure_count": terminal_failure_count,
        "expired_lease_count": expired_lease_count,
        "missed_freshness_count": missed_freshness_count,
        "pending_provider_outcome_count": int(pending_provider_count or 0),
        "oldest_pending_provider_outcome_age_seconds": _age_seconds(
            oldest_pending_provider, now=current_time
        ),
        "provider_attempt_count": provider_attempt_count,
        "rate_limited_job_count_window": rate_limited_job_count,
        "rate_limited_job_ratio_window": (
            rate_limited_job_count / provider_attempt_count
            if provider_attempt_count
            else 0.0
        ),
        "ingress_rate_per_second": ingress_rate,
        "goodput_per_second": goodput,
        "estimated_drain_seconds": drain_estimate,
        "state_counts": dict(sorted(state_counts.items())),
        "ready_by_priority_destination": ready_by_priority_destination,
        "ready_by_action": dict(sorted(ready_by_action.items())),
        "ready_by_bot_role": dict(sorted(ready_by_bot_role.items())),
        "ready_by_method": dict(sorted(ready_by_method.items())),
        "retry_after_buckets": dict(sorted(retry_after_buckets.items())),
        "active_runtime_gates": active_runtime_gates,
    }
    applied_thresholds = thresholds or TelegramQueueHealthThresholds()
    alerts = _alerts(snapshot, applied_thresholds)
    snapshot["thresholds"] = asdict(applied_thresholds)
    snapshot["alerts"] = [asdict(alert) for alert in alerts]
    snapshot["decision"] = (
        "stop" if any(alert.severity == "critical" for alert in alerts)
        else "warning" if alerts
        else "continue"
    )
    snapshot["stop_reasons"] = [
        alert.code for alert in alerts if alert.severity == "critical"
    ]
    return snapshot


async def build_telegram_delivery_shadow_plan(
    db: AsyncSession,
    *,
    current_server: str,
    limit_per_lane: int = 25,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect readiness/order without lease, lock, mutation, or provider I/O."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    limit = max(1, min(100, int(limit_per_lane)))
    trade_overdue = (
        (TelegramDeliveryJobRecord.action_kind == TelegramDeliveryAction.TRADE_RESULT)
        & TelegramDeliveryJobRecord.delivery_deadline_at.is_not(None)
        & (TelegramDeliveryJobRecord.delivery_deadline_at <= current_time)
    )
    effective_priority = case((trade_overdue, 0), else_=TelegramDeliveryJobRecord.priority)
    candidates: list[TelegramQueueShadowCandidate] = []
    job_scope_filter = (
        (TelegramDeliveryJobRecord.run_id == str(run_id)),
    ) if run_id is not None else ()
    for bot_role in ("primary", "channel_editor"):
        rows = (
            await db.execute(
                select(TelegramDeliveryJobRecord, effective_priority.label("effective_priority"))
                .where(
                    *job_scope_filter,
                    TelegramDeliveryJobRecord.bot_identity == bot_role,
                    TelegramDeliveryJobRecord.state.in_(tuple(CLAIMABLE_DELIVERY_STATES)),
                    or_(
                        TelegramDeliveryJobRecord.eligible_at.is_(None),
                        TelegramDeliveryJobRecord.eligible_at <= current_time,
                    ),
                    or_(
                        TelegramDeliveryJobRecord.next_retry_at.is_(None),
                        TelegramDeliveryJobRecord.next_retry_at <= current_time,
                    ),
                )
                .order_by(
                    effective_priority.asc(),
                    TelegramDeliveryJobRecord.priority_rank.asc(),
                    TelegramDeliveryJobRecord.source_order_at.desc().nullslast(),
                    TelegramDeliveryJobRecord.delivery_deadline_at.asc().nullslast(),
                    TelegramDeliveryJobRecord.enqueued_seq.asc(),
                )
                .limit(limit)
            )
        ).all()
        for rank, (job, priority) in enumerate(rows, start=1):
            candidates.append(
                TelegramQueueShadowCandidate(
                    lane_rank=rank,
                    bot_role=bot_role,
                    effective_priority=int(priority),
                    action=_enum_value(job.action_kind),
                    destination_class=_enum_value(job.destination_class),
                    method=_bounded_method(job.method),
                    correlation_hash=_correlation_hash(job.dedupe_key),
                )
            )
    return {
        "schema_version": 1,
        "observed_at": current_time.isoformat(),
        "scope": "run" if run_id is not None else "all",
        "mode": "read_only_shadow",
        "planner_scope": "readiness_and_order_evidence_not_dispatch_prediction",
        "provider_calls": 0,
        "database_mutations": 0,
        "candidate_count": len(candidates),
        "candidates": [asdict(candidate) for candidate in candidates],
    }


def publish_telegram_delivery_health_metrics(snapshot: Mapping[str, Any]) -> None:
    """Publish only low-cardinality aggregate labels to the metrics registry."""
    registry.gauge(
        "trading_bot_telegram_delivery_queue_ready_depth",
        "Ready Telegram delivery jobs by priority and destination class.",
        value=float(snapshot.get("ready_depth", 0)),
        priority="all",
        destination_class="all",
    )
    for row in snapshot.get("ready_by_priority_destination", ()):
        if not isinstance(row, Mapping):
            continue
        labels = {
            "priority": str(row.get("priority", "unknown")),
            "destination_class": str(row.get("destination_class", "unknown")),
        }
        registry.gauge(
            "trading_bot_telegram_delivery_queue_ready_depth",
            "Ready Telegram delivery jobs by priority and destination class.",
            value=float(row.get("depth", 0)),
            **labels,
        )
        registry.gauge(
            "trading_bot_telegram_delivery_queue_oldest_ready_age_seconds",
            "Age of the oldest ready Telegram delivery job.",
            value=float(row.get("oldest_age_seconds", 0)),
            **labels,
        )
    for field in (
        "ambiguous_count",
        "unresolved_count",
        "blocked_count",
        "terminal_failure_count",
        "expired_lease_count",
        "missed_freshness_count",
        "pending_provider_outcome_count",
    ):
        registry.gauge(
            "trading_bot_telegram_delivery_queue_condition_count",
            "Telegram delivery queue operational conditions by bounded kind.",
            value=float(snapshot.get(field, 0)),
            kind=field,
        )
    registry.gauge(
        "trading_bot_telegram_delivery_queue_goodput_per_second",
        "Successfully delivered Telegram jobs per second in the sample window.",
        value=float(snapshot.get("goodput_per_second", 0)),
    )
    registry.gauge(
        "trading_bot_telegram_delivery_queue_ingress_per_second",
        "Telegram delivery jobs created per second in the sample window.",
        value=float(snapshot.get("ingress_rate_per_second", 0)),
    )
    registry.gauge(
        "trading_bot_telegram_delivery_queue_stop",
        "Whether any configured Telegram delivery stop threshold is active.",
        value=1.0 if snapshot.get("decision") == "stop" else 0.0,
    )
