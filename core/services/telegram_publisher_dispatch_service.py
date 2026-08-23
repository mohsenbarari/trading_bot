"""Durable, payload-free B2B dispatch for Telegram publisher lanes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.metrics import registry as metrics_registry
from core.server_routing import SERVER_FOREIGN
from core.telegram_delivery_queue_contract import FINAL_DELIVERY_STATES
from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_IDENTITIES,
    TelegramMultiPublisherContractError,
    TelegramPublisherB2BEnvelope,
    TelegramPublisherB2BMessageType,
    TelegramPublisherDispatchState,
    parse_telegram_publisher_b2b_envelope,
    render_telegram_publisher_b2b_envelope,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_publisher_dispatch_command import TelegramPublisherDispatchCommand
from models.telegram_publisher_lane_heartbeat import TelegramPublisherLaneHeartbeat


class TelegramPublisherDispatchError(RuntimeError):
    """Raised when a B2B command would violate its durable lane contract."""


@dataclass(frozen=True, slots=True)
class TelegramPublisherLaneSelection:
    publisher_bot_identity: str
    in_flight_count: int


@dataclass(frozen=True, slots=True)
class TelegramPublisherDispatchLease:
    command: TelegramPublisherDispatchCommand
    lease_token: int


@dataclass(frozen=True, slots=True)
class TelegramPublisherInboundDispatchResult:
    publisher_bot_identity: str
    command_id: str
    duplicate: bool
    acknowledgement_text: str


@dataclass(frozen=True, slots=True)
class TelegramPublisherDispatchCycleReport:
    claimed_count: int
    sent_count: int
    retry_due_count: int


_healthy_publisher_identities: set[str] = set()


def set_telegram_publisher_lane_health(
    publisher_bot_identity: str,
    *,
    healthy: bool,
) -> None:
    identity = _publisher_identity(publisher_bot_identity)
    if healthy:
        _healthy_publisher_identities.add(identity)
    else:
        _healthy_publisher_identities.discard(identity)


def healthy_telegram_publisher_lane_identities() -> tuple[str, ...]:
    return tuple(sorted(_healthy_publisher_identities))


def _require_foreign(current_server: str) -> None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramPublisherDispatchError("telegram_publisher_dispatch_is_foreign_local")


def _publisher_identity(value: Any) -> str:
    identity = str(value or "").strip()
    if identity not in TELEGRAM_PUBLISHER_IDENTITIES:
        raise TelegramPublisherDispatchError("telegram_publisher_identity_invalid")
    return identity


def _positive_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool):
        raise TelegramPublisherDispatchError(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramPublisherDispatchError(reason) from exc
    if parsed <= 0:
        raise TelegramPublisherDispatchError(reason)
    return parsed


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TelegramPublisherDispatchError("telegram_publisher_dispatch_time_invalid")
    return value.astimezone(timezone.utc)


def select_telegram_publisher_lane(
    *,
    healthy_publishers: Iterable[str],
    in_flight_counts: Mapping[str, int],
    round_robin_sequence: int,
) -> TelegramPublisherLaneSelection:
    """Choose least-in-flight with deterministic round-robin tie breaking."""
    healthy = tuple(sorted({_publisher_identity(value) for value in healthy_publishers}))
    if not healthy:
        raise TelegramPublisherDispatchError("telegram_publisher_no_healthy_lane")
    sequence = _positive_int(
        round_robin_sequence,
        reason="telegram_publisher_round_robin_sequence_invalid",
    )
    counts: dict[str, int] = {}
    for identity in healthy:
        try:
            count = int(in_flight_counts.get(identity, 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise TelegramPublisherDispatchError(
                "telegram_publisher_in_flight_count_invalid"
            ) from exc
        if count < 0:
            raise TelegramPublisherDispatchError("telegram_publisher_in_flight_count_invalid")
        counts[identity] = count
    minimum = min(counts.values())
    tied = tuple(identity for identity in healthy if counts[identity] == minimum)
    selected = tied[(sequence - 1) % len(tied)]
    return TelegramPublisherLaneSelection(
        publisher_bot_identity=selected,
        in_flight_count=counts[selected],
    )


async def select_telegram_publisher_lane_for_job(
    db: AsyncSession,
    *,
    healthy_publishers: Iterable[str],
    round_robin_sequence: int,
) -> TelegramPublisherLaneSelection:
    healthy = tuple(sorted({_publisher_identity(value) for value in healthy_publishers}))
    if not healthy:
        raise TelegramPublisherDispatchError("telegram_publisher_no_healthy_lane")
    rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.bot_identity,
                func.count(TelegramDeliveryJobRecord.id),
            )
            .where(
                TelegramDeliveryJobRecord.bot_identity.in_(healthy),
                TelegramDeliveryJobRecord.state.notin_(tuple(FINAL_DELIVERY_STATES)),
            )
            .group_by(TelegramDeliveryJobRecord.bot_identity)
        )
    ).all()
    return select_telegram_publisher_lane(
        healthy_publishers=healthy,
        in_flight_counts={str(identity): int(count) for identity, count in rows},
        round_robin_sequence=round_robin_sequence,
    )


async def get_or_create_telegram_publisher_dispatch_command(
    db: AsyncSession,
    *,
    current_server: str,
    job: TelegramDeliveryJobRecord,
    publisher_bot_identity: str,
    now: datetime,
) -> TelegramPublisherDispatchCommand:
    _require_foreign(current_server)
    publisher = _publisher_identity(publisher_bot_identity)
    if str(getattr(job, "bot_identity", "") or "").strip() != publisher:
        raise TelegramPublisherDispatchError("telegram_publisher_dispatch_job_owner_mismatch")
    job_id = _positive_int(getattr(job, "id", None), reason="telegram_publisher_dispatch_job_invalid")
    sequence = _positive_int(
        getattr(job, "enqueued_seq", None),
        reason="telegram_publisher_dispatch_sequence_invalid",
    )
    current_time = _utc(now)
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.job_id == job_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is not None:
        if str(command.publisher_bot_identity) != publisher:
            raise TelegramPublisherDispatchError("telegram_publisher_dispatch_command_owner_mismatch")
        return command
    insert_stmt = (
        postgresql_insert(TelegramPublisherDispatchCommand)
        .values(
            command_id=str(uuid4()),
            job_id=job_id,
            publisher_bot_identity=publisher,
            dispatch_sequence=sequence,
            state=TelegramPublisherDispatchState.PENDING.value,
            attempt_count=0,
            lease_token=0,
            created_at=current_time,
            updated_at=current_time,
        )
        .on_conflict_do_nothing(index_elements=["job_id"])
    )
    await db.execute(insert_stmt)
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.job_id == job_id)
            .with_for_update()
        )
    ).scalar_one()
    if str(command.publisher_bot_identity) != publisher:
        raise TelegramPublisherDispatchError("telegram_publisher_dispatch_command_owner_mismatch")
    return command


def render_telegram_publisher_dispatch(command: TelegramPublisherDispatchCommand) -> str:
    created_at = _utc(getattr(command, "created_at", None))
    return render_telegram_publisher_b2b_envelope(
        TelegramPublisherB2BEnvelope(
            message_type=TelegramPublisherB2BMessageType.DISPATCH,
            command_id=str(command.command_id),
            sequence=_positive_int(
                command.dispatch_sequence,
                reason="telegram_publisher_dispatch_sequence_invalid",
            ),
            enqueued_at=created_at,
        )
    )


async def touch_telegram_publisher_lane_heartbeat(
    db: AsyncSession,
    *,
    publisher_bot_identity: str,
    worker_id: str,
    lease_seconds: float,
    now: datetime,
) -> TelegramPublisherLaneHeartbeat:
    """Refresh the durable liveness lease for one publisher lane."""
    publisher = _publisher_identity(publisher_bot_identity)
    current_time = _utc(now)
    if not math.isfinite(float(lease_seconds)) or float(lease_seconds) <= 0:
        raise TelegramPublisherDispatchError("telegram_publisher_heartbeat_lease_invalid")
    owner = str(worker_id or "").strip()
    if not owner:
        raise TelegramPublisherDispatchError("telegram_publisher_heartbeat_worker_invalid")
    lease_until = current_time + timedelta(seconds=float(lease_seconds))
    insert_stmt = (
        postgresql_insert(TelegramPublisherLaneHeartbeat)
        .values(
            publisher_bot_identity=publisher,
            worker_id=owner[:128],
            lease_until=lease_until,
            updated_at=current_time,
        )
        .on_conflict_do_update(
            index_elements=["publisher_bot_identity"],
            set_={
                "worker_id": owner[:128],
                "lease_until": lease_until,
                "updated_at": current_time,
            },
        )
        .returning(TelegramPublisherLaneHeartbeat)
    )
    return (await db.execute(insert_stmt)).scalar_one()


async def telegram_publisher_lane_heartbeat_is_fresh(
    db: AsyncSession,
    *,
    publisher_bot_identity: str,
    now: datetime,
) -> bool:
    publisher = _publisher_identity(publisher_bot_identity)
    current_time = _utc(now)
    lease_until = (
        await db.execute(
            select(TelegramPublisherLaneHeartbeat.lease_until).where(
                TelegramPublisherLaneHeartbeat.publisher_bot_identity == publisher
            )
        )
    ).scalar_one_or_none()
    return lease_until is not None and _utc(lease_until) > current_time


async def acknowledge_telegram_publisher_dispatch_locally(
    db: AsyncSession,
    *,
    current_server: str,
    command_id: str,
    now: datetime,
) -> bool:
    """Mark one claimed command acknowledged without a Telegram hop."""
    _require_foreign(current_server)
    current_time = _utc(now)
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.command_id == str(command_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        return False
    publisher = _publisher_identity(command.publisher_bot_identity)
    if command.state == TelegramPublisherDispatchState.ACKNOWLEDGED.value:
        return True
    if command.state not in {
        TelegramPublisherDispatchState.PENDING.value,
        TelegramPublisherDispatchState.SENT.value,
        TelegramPublisherDispatchState.RETRY_DUE.value,
    }:
        return False
    if not await telegram_publisher_lane_heartbeat_is_fresh(
        db,
        publisher_bot_identity=publisher,
        now=current_time,
    ):
        return False
    command.state = TelegramPublisherDispatchState.ACKNOWLEDGED.value
    command.acknowledged_at = current_time
    command.receipt_sequence = int(command.dispatch_sequence)
    command.receipt_received_at = current_time
    command.lease_until = None
    command.next_retry_at = None
    command.updated_at = current_time
    await db.flush()
    from core.telegram_delivery_queue_wakeup import (
        emit_delivery_queue_wakeup,
    )

    await emit_delivery_queue_wakeup(db, bot_identity=publisher)
    return True


async def claim_next_telegram_publisher_dispatch_command(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: float,
    now: datetime,
) -> TelegramPublisherDispatchLease | None:
    _require_foreign(current_server)
    current_time = _utc(now)
    if not math.isfinite(float(lease_seconds)) or float(lease_seconds) <= 0:
        raise TelegramPublisherDispatchError("telegram_publisher_dispatch_lease_invalid")
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(
                TelegramPublisherDispatchCommand.state.in_(
                    (
                        TelegramPublisherDispatchState.PENDING.value,
                        TelegramPublisherDispatchState.RETRY_DUE.value,
                        TelegramPublisherDispatchState.SENT.value,
                    )
                ),
                or_(
                    TelegramPublisherDispatchCommand.next_retry_at.is_(None),
                    TelegramPublisherDispatchCommand.next_retry_at <= current_time,
                ),
                or_(
                    TelegramPublisherDispatchCommand.lease_until.is_(None),
                    TelegramPublisherDispatchCommand.lease_until <= current_time,
                ),
            )
            .order_by(TelegramPublisherDispatchCommand.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    command.lease_token = int(command.lease_token or 0) + 1
    command.lease_until = current_time + timedelta(seconds=float(lease_seconds))
    command.attempt_count = int(command.attempt_count or 0) + 1
    command.next_retry_at = None
    command.updated_at = current_time
    await db.flush()
    return TelegramPublisherDispatchLease(command=command, lease_token=int(command.lease_token))


def _gateway_retry_after_seconds(result: telegram_gateway.TelegramGatewayResult, fallback: float) -> float:
    payload = result.response_json if isinstance(result.response_json, Mapping) else {}
    parameters = payload.get("parameters") if isinstance(payload, Mapping) else None
    value = parameters.get("retry_after") if isinstance(parameters, Mapping) else None
    try:
        retry_after = float(value)
    except (TypeError, ValueError, OverflowError):
        retry_after = fallback
    return max(0.1, retry_after)


async def record_telegram_publisher_dispatch_result(
    db: AsyncSession,
    *,
    current_server: str,
    command_id: str,
    lease_token: int,
    result: telegram_gateway.TelegramGatewayResult,
    retry_after_seconds: float,
    acknowledgement_timeout_seconds: float,
    now: datetime,
) -> bool:
    _require_foreign(current_server)
    current_time = _utc(now)
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.command_id == str(command_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None or int(command.lease_token or 0) != int(lease_token):
        return False
    if command.state == TelegramPublisherDispatchState.ACKNOWLEDGED.value:
        return True
    command.lease_until = None
    if bool(result.ok):
        command.state = TelegramPublisherDispatchState.SENT.value
        command.sent_at = command.sent_at or current_time
        command.next_retry_at = current_time + timedelta(
            seconds=max(0.1, float(acknowledgement_timeout_seconds))
        )
        command.last_error_class = None
        command.last_error_message = None
    else:
        metrics_registry.counter(
            "telegram_publisher_b2b_dispatch_retries_total",
            "Telegram publisher B2B dispatch retries.",
            lane=str(command.publisher_bot_identity),
        )
        command.state = TelegramPublisherDispatchState.RETRY_DUE.value
        command.next_retry_at = current_time + timedelta(
            seconds=_gateway_retry_after_seconds(result, retry_after_seconds)
        )
        command.last_error_class = str(result.error or "telegram_b2b_send_failed")[:120]
        command.last_error_message = None
    command.updated_at = current_time
    await db.flush()
    return True


async def accept_telegram_publisher_dispatch(
    db: AsyncSession,
    *,
    current_server: str,
    publisher_bot_identity: str,
    expected_primary_bot_id: int,
    sender_bot_id: int,
    text: str,
    now: datetime,
) -> TelegramPublisherInboundDispatchResult:
    _require_foreign(current_server)
    publisher = _publisher_identity(publisher_bot_identity)
    if _positive_int(sender_bot_id, reason="telegram_b2b_sender_invalid") != _positive_int(
        expected_primary_bot_id,
        reason="telegram_b2b_primary_sender_invalid",
    ):
        raise TelegramPublisherDispatchError("telegram_b2b_sender_not_allowlisted")
    try:
        envelope = parse_telegram_publisher_b2b_envelope(text)
    except TelegramMultiPublisherContractError as exc:
        raise TelegramPublisherDispatchError("telegram_b2b_malformed_envelope") from exc
    if envelope.message_type != TelegramPublisherB2BMessageType.DISPATCH:
        raise TelegramPublisherDispatchError("telegram_b2b_dispatch_type_required")
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.command_id == envelope.command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        raise TelegramPublisherDispatchError("telegram_b2b_command_not_found")
    if str(command.publisher_bot_identity) != publisher:
        raise TelegramPublisherDispatchError("telegram_b2b_command_not_assigned")
    if int(command.dispatch_sequence) != envelope.sequence:
        raise TelegramPublisherDispatchError("telegram_b2b_command_stale")
    duplicate = command.state == TelegramPublisherDispatchState.ACKNOWLEDGED.value
    if command.state in {
        TelegramPublisherDispatchState.FAILED.value,
        TelegramPublisherDispatchState.SUPERSEDED.value,
    }:
        raise TelegramPublisherDispatchError("telegram_b2b_command_stale")
    current_time = _utc(now)
    if not duplicate:
        command.state = TelegramPublisherDispatchState.ACKNOWLEDGED.value
        command.acknowledged_at = current_time
        command.receipt_sequence = envelope.sequence
        command.receipt_received_at = current_time
        command.lease_until = None
        command.next_retry_at = None
        command.updated_at = current_time
        await db.flush()
        from core.telegram_delivery_queue_wakeup import (
            emit_delivery_queue_wakeup,
        )

        await emit_delivery_queue_wakeup(db, bot_identity=publisher)
    acknowledgement_text = render_telegram_publisher_b2b_envelope(
        TelegramPublisherB2BEnvelope(
            message_type=TelegramPublisherB2BMessageType.ACK,
            command_id=str(command.command_id),
            sequence=int(command.dispatch_sequence),
            enqueued_at=_utc(command.created_at),
            ack_sent_at=current_time,
        )
    )
    return TelegramPublisherInboundDispatchResult(
        publisher_bot_identity=publisher,
        command_id=str(command.command_id),
        duplicate=duplicate,
        acknowledgement_text=acknowledgement_text,
    )


async def accept_telegram_publisher_acknowledgement(
    db: AsyncSession,
    *,
    current_server: str,
    sender_bot_id: int,
    publisher_bot_ids: Mapping[str, int],
    text: str,
    now: datetime,
) -> bool:
    _require_foreign(current_server)
    try:
        envelope = parse_telegram_publisher_b2b_envelope(text)
    except TelegramMultiPublisherContractError as exc:
        raise TelegramPublisherDispatchError("telegram_b2b_malformed_envelope") from exc
    if envelope.message_type != TelegramPublisherB2BMessageType.ACK:
        raise TelegramPublisherDispatchError("telegram_b2b_ack_type_required")
    command = (
        await db.execute(
            select(TelegramPublisherDispatchCommand)
            .where(TelegramPublisherDispatchCommand.command_id == envelope.command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command is None:
        raise TelegramPublisherDispatchError("telegram_b2b_command_not_found")
    publisher = _publisher_identity(command.publisher_bot_identity)
    expected_sender = _positive_int(
        publisher_bot_ids.get(publisher),
        reason="telegram_b2b_publisher_sender_invalid",
    )
    if _positive_int(sender_bot_id, reason="telegram_b2b_sender_invalid") != expected_sender:
        raise TelegramPublisherDispatchError("telegram_b2b_ack_sender_mismatch")
    if int(command.dispatch_sequence) != envelope.sequence:
        raise TelegramPublisherDispatchError("telegram_b2b_command_stale")
    if command.state == TelegramPublisherDispatchState.ACKNOWLEDGED.value:
        return True
    if command.state in {
        TelegramPublisherDispatchState.FAILED.value,
        TelegramPublisherDispatchState.SUPERSEDED.value,
    }:
        raise TelegramPublisherDispatchError("telegram_b2b_command_stale")
    current_time = _utc(now)
    command.state = TelegramPublisherDispatchState.ACKNOWLEDGED.value
    command.acknowledged_at = current_time
    command.receipt_sequence = envelope.sequence
    command.receipt_received_at = current_time
    command.lease_until = None
    command.next_retry_at = None
    command.updated_at = current_time
    await db.flush()
    from core.telegram_delivery_queue_wakeup import (
        emit_delivery_queue_wakeup,
    )

    await emit_delivery_queue_wakeup(db, bot_identity=publisher)
    metrics_registry.observe(
        "telegram_publisher_b2b_ack_lag_ms",
        "Lag from durable B2B command creation to publisher acknowledgement.",
        max(0.0, (current_time - _utc(command.created_at)).total_seconds() * 1000),
        lane=publisher,
    )
    return True


TelegramPublisherGatewayCall = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]


async def dispatch_claimed_telegram_publisher_command(
    lease: TelegramPublisherDispatchLease,
    *,
    publisher_bot_ids: Mapping[str, int],
    gateway_call: TelegramPublisherGatewayCall,
    timeout_seconds: float,
) -> telegram_gateway.TelegramGatewayResult:
    command = lease.command
    publisher = _publisher_identity(command.publisher_bot_identity)
    destination_bot_id = _positive_int(
        publisher_bot_ids.get(publisher),
        reason="telegram_b2b_publisher_destination_invalid",
    )
    return await gateway_call(
        "sendMessage",
        {"chat_id": destination_bot_id, "text": render_telegram_publisher_dispatch(command)},
        timeout=float(timeout_seconds),
        idempotency_key=f"telegram-b2b:{command.command_id}:{lease.lease_token}",
    )


async def run_telegram_publisher_dispatch_cycle(
    *,
    session_factory: Callable[[], Any],
    current_server: str,
    publisher_bot_ids: Mapping[str, int],
    gateway_call: TelegramPublisherGatewayCall,
    limit: int,
    lease_seconds: float,
    retry_after_seconds: float,
    acknowledgement_timeout_seconds: float,
    request_timeout_seconds: float,
    now_factory: Callable[[], datetime],
    local_ack_enabled: bool = False,
) -> TelegramPublisherDispatchCycleReport:
    """Send a bounded batch while keeping every external call outside a DB transaction."""
    _require_foreign(current_server)
    claimed_count = 0
    sent_count = 0
    retry_due_count = 0
    for _ in range(max(1, int(limit))):
        async with session_factory() as db:
            lease = await claim_next_telegram_publisher_dispatch_command(
                db,
                current_server=current_server,
                lease_seconds=lease_seconds,
                now=now_factory(),
            )
            if lease is None:
                rollback = getattr(db, "rollback", None)
                if callable(rollback):
                    await rollback()
                break
            await db.commit()
        claimed_count += 1
        if local_ack_enabled:
            async with session_factory() as db:
                locally_acked = await acknowledge_telegram_publisher_dispatch_locally(
                    db,
                    current_server=current_server,
                    command_id=str(lease.command.command_id),
                    now=now_factory(),
                )
                if locally_acked:
                    await db.commit()
                    sent_count += 1
                    continue
                await db.rollback()
        result = await dispatch_claimed_telegram_publisher_command(
            lease,
            publisher_bot_ids=publisher_bot_ids,
            gateway_call=gateway_call,
            timeout_seconds=request_timeout_seconds,
        )
        async with session_factory() as db:
            recorded = await record_telegram_publisher_dispatch_result(
                db,
                current_server=current_server,
                command_id=str(lease.command.command_id),
                lease_token=lease.lease_token,
                result=result,
                retry_after_seconds=retry_after_seconds,
                acknowledgement_timeout_seconds=acknowledgement_timeout_seconds,
                now=now_factory(),
            )
            if recorded:
                await db.commit()
            else:
                await db.rollback()
        if result.ok:
            sent_count += 1
        else:
            retry_due_count += 1
    return TelegramPublisherDispatchCycleReport(
        claimed_count=claimed_count,
        sent_count=sent_count,
        retry_due_count=retry_due_count,
    )
