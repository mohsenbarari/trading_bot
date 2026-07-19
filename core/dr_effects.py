"""Writer-epoch-bound durable external-effect execution."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Awaitable, Callable, Iterator, TypeVar
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import (
    AsyncSessionLocal,
    DrProjectionSessionLocal,
    verify_three_site_database_role_bindings,
)
from core.dr_event_protocol import canonical_json_bytes
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import snapshot_is_local_active, writer_state_snapshot
from core.writer_fencing import (
    current_writer_fence_context,
    projection_fence_scope,
    writer_fence_scope,
)
from core.writer_lease_clock import boottime_seconds
from models.dr_event import DrEffectOutbox, DrEvent
from models.webapp_writer_state import WebappWriterState


class DrEffectError(RuntimeError):
    """Raised when an external effect is not bound to a current writer term."""


@dataclass(frozen=True)
class EffectExecutionCapability:
    effect_id: str
    physical_site: str
    writer_epoch: int
    effect_type: str
    provider: str


@dataclass(frozen=True)
class ProviderEffectResult:
    outcome: str  # succeeded | not_sent | ambiguous
    receipt: str | None = None
    error_code: str | None = None


_effect_capability: ContextVar[EffectExecutionCapability | None] = ContextVar(
    "dr_effect_execution_capability", default=None
)
EffectHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[ProviderEffectResult]]
T = TypeVar("T")


def current_effect_capability() -> EffectExecutionCapability | None:
    return _effect_capability.get()


@contextmanager
def _effect_scope(capability: EffectExecutionCapability) -> Iterator[None]:
    token: Token = _effect_capability.set(capability)
    try:
        yield
    finally:
        _effect_capability.reset(token)


def assert_epoch_bound_effect_execution(*, provider: str, effect_type: str | None = None) -> None:
    """Reject direct WebApp provider calls in strict three-site mode."""

    if not (
        bool(getattr(settings, "three_site_dr_enabled", False))
        and bool(getattr(settings, "dr_event_protocol_strict", False))
    ):
        return
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_authority:
        return
    capability = current_effect_capability()
    if (
        capability is None
        or capability.provider != provider
        or capability.physical_site != identity.physical_site
        or (effect_type is not None and capability.effect_type != effect_type)
    ):
        raise DrEffectError("direct WebApp external effect lacks an epoch-bound execution capability")


async def execute_claimed_inline_effect(
    *,
    effect_type: str,
    provider: str,
    idempotency_key: str,
    handler: Callable[[], Awaitable[T]],
) -> T:
    """Execute an already-durably-claimed legacy effect under the Writer term.

    OTP and Invitation-SMS already persist a crash-aware claim in their own
    ledgers.  This bridge keeps those mature ledgers while adding the same
    epoch/lease recheck and transition lock used by the generic DR outbox.
    It must never be used for an effect that has no durable claim.
    """

    if not (
        bool(getattr(settings, "three_site_dr_enabled", False))
        and bool(getattr(settings, "dr_event_protocol_strict", False))
    ):
        return await handler()
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_authority:
        return await handler()
    outer_fence = current_writer_fence_context()
    if outer_fence is None or outer_fence.physical_site != identity.physical_site:
        raise DrEffectError("claimed inline effect lacks the request/worker writer capability")
    require_witness = bool(settings.writer_witness_required)
    async with AsyncSessionLocal() as session:
        state = await session.scalar(
            select(WebappWriterState)
            .where(WebappWriterState.authority == "webapp")
            .with_for_update(read=True)
        )
        if state is None:
            raise DrEffectError("writer state is missing at claimed inline effect execution")
        snapshot = writer_state_snapshot(state)
        active, reasons = snapshot_is_local_active(
            identity, snapshot, require_witness_lease=require_witness
        )
        if not active:
            raise DrEffectError("claimed inline effect writer check failed: " + ",".join(reasons))
        if (
            snapshot.writer_epoch != outer_fence.writer_epoch
            or snapshot.transition_id != outer_fence.transition_id
        ):
            raise DrEffectError("claimed inline effect writer term changed before provider call")
        if require_witness:
            remaining = float(snapshot.witness_local_boottime_deadline or 0) - boottime_seconds()
            if remaining < max(1, int(settings.dr_effect_min_lease_remaining_seconds)):
                raise DrEffectError("claimed inline effect lease has insufficient monotonic lifetime")
        capability = EffectExecutionCapability(
            effect_id="legacy:" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            physical_site=identity.physical_site,
            writer_epoch=snapshot.writer_epoch,
            effect_type=effect_type,
            provider=provider,
        )
        with writer_fence_scope(
            identity,
            snapshot,
            source="claimed_inline_effect",
            require_witness_lease=require_witness,
        ):
            with _effect_scope(capability):
                result = await handler()
        await session.commit()
        return result


async def enqueue_epoch_bound_effect(
    session: AsyncSession,
    *,
    event_id: str,
    effect_type: str,
    provider: str,
    destination_key: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> DrEffectOutbox:
    identity = resolve_runtime_identity(settings)
    fence = current_writer_fence_context()
    if fence is None or fence.physical_site != identity.physical_site:
        raise DrEffectError("effect intent lacks the current writer capability")
    event = await session.get(DrEvent, event_id)
    if event is None:
        raise DrEffectError("effect intent references a missing immutable event")
    if (
        event.origin_physical_site != identity.physical_site
        or event.writer_epoch != fence.writer_epoch
    ):
        raise DrEffectError("effect event does not belong to the current local writer epoch")
    canonical_payload = canonical_json_bytes(payload)
    payload_hash = hashlib.sha256(canonical_payload).hexdigest()
    destination_hash = hashlib.sha256(destination_key.encode("utf-8")).hexdigest()
    existing = await session.scalar(
        select(DrEffectOutbox).where(DrEffectOutbox.idempotency_key == str(idempotency_key))
    )
    if existing is not None:
        if (
            existing.event_id != event.event_id
            or existing.effect_type != str(effect_type)
            or existing.provider != str(provider)
            or existing.destination_key_hash != destination_hash
            or existing.payload_hash != payload_hash
        ):
            raise DrEffectError("effect idempotency key was reused with different immutable intent")
        return existing
    row = DrEffectOutbox(
        effect_id=str(uuid4()),
        event_id=event.event_id,
        origin_physical_site=identity.physical_site,
        executor_site=identity.physical_site,
        writer_epoch=fence.writer_epoch,
        effect_type=str(effect_type),
        provider=str(provider),
        destination_key_hash=destination_hash,
        idempotency_key=str(idempotency_key),
        payload=payload,
        payload_hash=payload_hash,
        status="pending",
        attempt_count=0,
    )
    session.add(row)
    return row


async def enqueue_effect_for_aggregate(
    *,
    aggregate_type: str,
    aggregate_db_id: str | int,
    effect_type: str,
    provider: str,
    destination_key: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> str:
    """Find the committed causation event and append a durable effect intent."""

    fence = current_writer_fence_context()
    if fence is None:
        raise DrEffectError("post-commit effect enqueue lost its writer capability")
    async with AsyncSessionLocal() as session:
        event = await session.scalar(
            select(DrEvent)
            .where(
                DrEvent.aggregate_type == aggregate_type,
                DrEvent.aggregate_db_id == str(aggregate_db_id),
                DrEvent.origin_physical_site == fence.physical_site,
                DrEvent.writer_epoch == fence.writer_epoch,
            )
            .order_by(DrEvent.producer_sequence.desc())
            .limit(1)
        )
        if event is None:
            raise DrEffectError("no immutable causation event exists for external effect")
        row = await enqueue_epoch_bound_effect(
            session,
            event_id=event.event_id,
            effect_type=effect_type,
            provider=provider,
            destination_key=destination_key,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        await session.commit()
        return row.effect_id


async def claim_next_effect() -> str | None:
    identity = resolve_runtime_identity(settings)
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as read_session:
        state = await read_session.scalar(
            select(WebappWriterState).where(WebappWriterState.authority == "webapp")
        )
        if state is None:
            raise DrEffectError("writer state is missing")
        snapshot = writer_state_snapshot(state)
    require_witness = bool(settings.writer_witness_required)
    active, reasons = snapshot_is_local_active(
        identity, snapshot, require_witness_lease=require_witness
    )
    if not active:
        # A fenced standby is a normal steady state. It must keep the worker
        # process warm without claiming provider effects until it owns a valid
        # Writer term.
        return None
    with projection_fence_scope(source="dr_effect_claim"):
        async with DrProjectionSessionLocal() as session:
            # A claimed effect may have crossed a crash boundary after the
            # provider call. Never blindly return it to pending.
            expired = (
                await session.execute(
                    select(DrEffectOutbox)
                    .where(
                        DrEffectOutbox.executor_site == identity.physical_site,
                        DrEffectOutbox.status == "inflight",
                        DrEffectOutbox.claim_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for row in expired:
                row.status = "ambiguous"
                row.last_error_code = "claim_expired_unknown_provider_state"
                row.claimed_by = None
                row.claim_expires_at = None

            stale = (
                await session.execute(
                    select(DrEffectOutbox)
                    .where(
                        DrEffectOutbox.executor_site == identity.physical_site,
                        DrEffectOutbox.status.in_(("pending", "failed")),
                        DrEffectOutbox.writer_epoch != snapshot.writer_epoch,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for row in stale:
                row.status = "cancelled_stale_epoch"
                row.last_error_code = "writer_epoch_changed"

            effect = await session.scalar(
                select(DrEffectOutbox)
                .where(
                    DrEffectOutbox.executor_site == identity.physical_site,
                    DrEffectOutbox.writer_epoch == snapshot.writer_epoch,
                    DrEffectOutbox.status.in_(("pending", "failed")),
                    or_(DrEffectOutbox.next_attempt_at.is_(None), DrEffectOutbox.next_attempt_at <= now),
                )
                .order_by(DrEffectOutbox.created_at, DrEffectOutbox.effect_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if effect is None:
                await session.commit()
                return None
            effect.status = "inflight"
            effect.attempt_count = int(effect.attempt_count or 0) + 1
            effect.claimed_by = str(uuid4())
            effect.claim_expires_at = now + timedelta(seconds=max(5, int(settings.dr_effect_claim_seconds)))
            await session.commit()
            return effect.effect_id


async def execute_claimed_effect(effect_id: str, handlers: dict[tuple[str, str], EffectHandler]) -> str:
    identity = resolve_runtime_identity(settings)
    require_witness = bool(settings.writer_witness_required)
    # Provider execution and its final state transition use the application
    # role under an actual Writer capability. The projection role is reserved
    # for transport/apply bookkeeping and cannot impersonate a Writer at the
    # database trigger boundary.
    async with AsyncSessionLocal() as session:
        # FOR SHARE prevents a Writer transition from racing between the final
        # authority check and provider call. Other effect transactions may read
        # the row, while transition_writer_state (FOR UPDATE) must wait.
        state = await session.scalar(
            select(WebappWriterState)
            .where(WebappWriterState.authority == "webapp")
            .with_for_update(read=True)
        )
        if state is None:
            raise DrEffectError("writer state is missing at effect execution")
        snapshot = writer_state_snapshot(state)
        active, reasons = snapshot_is_local_active(
            identity, snapshot, require_witness_lease=require_witness
        )
        if not active:
            raise DrEffectError("effect writer check failed: " + ",".join(reasons))
        if require_witness:
            remaining = float(snapshot.witness_local_boottime_deadline or 0) - boottime_seconds()
            if remaining < max(1, int(settings.dr_effect_min_lease_remaining_seconds)):
                raise DrEffectError("effect lease has insufficient monotonic lifetime")
        with writer_fence_scope(
            identity,
            snapshot,
            source="dr_effect_execute",
            require_witness_lease=require_witness,
        ):
            effect = await session.get(DrEffectOutbox, effect_id, with_for_update=True)
            if effect is None or effect.status != "inflight":
                raise DrEffectError("effect is missing or not durably claimed")
            if (
                effect.executor_site != identity.physical_site
                or effect.origin_physical_site != identity.physical_site
                or effect.writer_epoch != snapshot.writer_epoch
            ):
                effect.status = "cancelled_stale_epoch"
                effect.last_error_code = "writer_term_mismatch_before_provider"
                await session.commit()
                return "cancelled_stale_epoch"
            if hashlib.sha256(canonical_json_bytes(effect.payload)).hexdigest() != effect.payload_hash:
                effect.status = "ambiguous"
                effect.last_error_code = "effect_payload_hash_mismatch"
                await session.commit()
                return "ambiguous"
            handler = handlers.get((effect.provider, effect.effect_type))
            if handler is None:
                effect.status = "failed"
                effect.last_error_code = "provider_handler_missing"
                effect.next_attempt_at = None
                await session.commit()
                return "failed"
            capability = EffectExecutionCapability(
                effect_id=effect.effect_id,
                physical_site=identity.physical_site,
                writer_epoch=snapshot.writer_epoch,
                effect_type=effect.effect_type,
                provider=effect.provider,
            )
            try:
                with _effect_scope(capability):
                    result = await handler(session, dict(effect.payload))
            except Exception as exc:
                # An exception after the call begins cannot prove non-delivery.
                result = ProviderEffectResult(
                    outcome="ambiguous", error_code=f"provider_exception_{type(exc).__name__}"
                )
            if result.outcome == "succeeded":
                effect.status = "succeeded"
                effect.provider_receipt_hash = (
                    hashlib.sha256(result.receipt.encode("utf-8")).hexdigest()
                    if result.receipt
                    else None
                )
                effect.last_error_code = None
                effect.next_attempt_at = None
            elif result.outcome == "not_sent":
                effect.status = "failed"
                effect.last_error_code = str(result.error_code or "provider_not_sent")[:64]
                effect.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(300, 2 ** min(8, int(effect.attempt_count or 1)))
                )
            else:
                effect.status = "ambiguous"
                effect.last_error_code = str(result.error_code or "provider_state_ambiguous")[:64]
                effect.next_attempt_at = None
            effect.claimed_by = None
            effect.claim_expires_at = None
            await session.commit()
            return effect.status


async def dr_effect_loop(handlers: dict[tuple[str, str], EffectHandler]) -> None:
    if not settings.dr_effect_worker_enabled:
        raise DrEffectError("DR effect worker is disabled")
    await verify_three_site_database_role_bindings()
    while True:
        effect_id = await claim_next_effect()
        if effect_id is None:
            await asyncio.sleep(max(0.05, float(settings.dr_effect_poll_seconds)))
            continue
        await execute_claimed_effect(effect_id, handlers)
