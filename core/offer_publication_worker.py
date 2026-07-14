"""Persistent foreign worker for Telegram offer-channel publication repair."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, or_, select, text as sa_text
from sqlalchemy.orm import aliased, selectinload

from core.background_job_authority import JOB_OFFER_TELEGRAM_PUBLICATION, assert_background_job_authority
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.cross_server_recovery_service import active_publication_is_gated
from core.services.offer_publication_reconciliation_service import reconcile_offer_publications
from core.services.offer_publication_state_service import apply_publication_state_update
from core.services.telegram_offer_channel_service import apply_offer_channel_state_with_result
from core.services.telegram_offer_publication_service import (
    get_or_create_telegram_publication_state,
    mark_telegram_publication_success,
)
from core.utils import utc_now
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)

logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_CHANNEL_STATE_RETRY_METADATA_KEY = "channel_state_reconciliation_attempt_count"
_CHANNEL_STATE_RETRY_VERSION_METADATA_KEY = "channel_state_reconciliation_attempt_offer_version_id"
_CHANNEL_STATE_RETRY_AT_METADATA_KEY = "channel_state_reconciliation_next_retry_at"
_CHANNEL_STATE_LOCK_PREFIX = "offer-channel-state-reconciliation"


@dataclass(frozen=True, slots=True)
class OfferPublicationCycleReport:
    processed: int
    repaired: int
    failed: int
    gated: int
    status: str
    rate_limited: int = 0
    cooldown_seconds: float = 0.0
    response_counts: tuple[tuple[str, int], ...] = ()
    skipped_locked: int = 0
    backlog_total: int = 0
    backlog_due: int = 0
    backlog_oldest_age_seconds: float | None = None
    backlog_oldest_due_age_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class OfferChannelStateCycleReport:
    processed: int
    applied: int
    failed: int
    skipped_recent: int
    rate_limited: int = 0
    bad_request: int = 0
    retryable_failed: int = 0
    non_retryable_remembered: int = 0
    cooldown_seconds: float = 0.0
    response_counts: tuple[tuple[str, int], ...] = ()
    skipped_locked: int = 0
    backlog_total: int = 0
    backlog_due: int = 0
    backlog_oldest_age_seconds: float | None = None
    backlog_oldest_due_age_seconds: float | None = None


@dataclass(slots=True)
class OfferChannelStateCandidate:
    offer: Offer
    state: OfferPublicationState | None


@dataclass(frozen=True, slots=True)
class OfferChannelStateBacklog:
    total: int = 0
    due: int = 0
    oldest_age_seconds: float | None = None
    oldest_due_age_seconds: float | None = None


def _worker_interval_seconds() -> float:
    return max(0.2, float(getattr(settings, "offer_publication_worker_interval_seconds", 1.0)))


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "offer_publication_worker_batch_limit", 25)))


def _bounded_setting_seconds(setting_name: str, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(getattr(settings, setting_name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _channel_edit_spacing_seconds() -> float:
    return _bounded_setting_seconds(
        "offer_publication_worker_channel_edit_spacing_seconds",
        default=0.35,
        minimum=0.0,
        maximum=5.0,
    )


def _channel_send_spacing_seconds() -> float:
    return _bounded_setting_seconds(
        "offer_publication_worker_channel_send_spacing_seconds",
        default=0.35,
        minimum=0.0,
        maximum=5.0,
    )


def _rate_limit_cooldown_seconds(retry_after_seconds: int | None) -> float:
    configured_default = _bounded_setting_seconds(
        "offer_publication_worker_rate_limit_cooldown_seconds",
        default=10.0,
        minimum=1.0,
        maximum=120.0,
    )
    configured_max = _bounded_setting_seconds(
        "offer_publication_worker_max_rate_limit_cooldown_seconds",
        default=120.0,
        minimum=configured_default,
        maximum=300.0,
    )
    if retry_after_seconds is None:
        return configured_default
    return max(1.0, min(configured_max, float(retry_after_seconds)))


def _coerce_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _channel_state_attempt_count(state: Any | None) -> int:
    metadata = getattr(state, "state_metadata", None)
    if not isinstance(metadata, Mapping):
        return 0
    return max(0, _coerce_int(metadata.get(_CHANNEL_STATE_RETRY_METADATA_KEY)) or 0)


def _set_channel_state_attempt_count(
    state: Any,
    count: int,
    *,
    offer_version_id: int | None = None,
    next_retry_at: datetime | None = None,
) -> None:
    metadata = getattr(state, "state_metadata", None)
    updated_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    if count > 0:
        updated_metadata[_CHANNEL_STATE_RETRY_METADATA_KEY] = int(count)
        if offer_version_id is not None:
            updated_metadata[_CHANNEL_STATE_RETRY_VERSION_METADATA_KEY] = int(offer_version_id)
        if next_retry_at is not None:
            updated_metadata[_CHANNEL_STATE_RETRY_AT_METADATA_KEY] = next_retry_at.timestamp()
    else:
        updated_metadata.pop(_CHANNEL_STATE_RETRY_METADATA_KEY, None)
        updated_metadata.pop(_CHANNEL_STATE_RETRY_VERSION_METADATA_KEY, None)
        updated_metadata.pop(_CHANNEL_STATE_RETRY_AT_METADATA_KEY, None)
    state.state_metadata = updated_metadata or None


def _channel_state_retry_delay_seconds(
    attempt_count: int,
    *,
    retry_after_seconds: int | None = None,
) -> float:
    base = _bounded_setting_seconds(
        "offer_publication_worker_retry_base_seconds",
        default=5.0,
        minimum=1.0,
        maximum=60.0,
    )
    maximum = _bounded_setting_seconds(
        "offer_publication_worker_retry_max_seconds",
        default=300.0,
        minimum=base,
        maximum=3600.0,
    )
    exponent = max(0, min(int(attempt_count or 1) - 1, 10))
    delay = min(maximum, base * (2**exponent))
    if retry_after_seconds is not None:
        delay = max(delay, min(maximum, max(1.0, float(retry_after_seconds))))
    return delay


def _channel_state_base_condition():
    return and_(
        Offer.channel_message_id.isnot(None),
        or_(Offer.archived.is_(False), Offer.archived.is_(None)),
        or_(
            Offer.status.in_([OfferStatus.COMPLETED, OfferStatus.CANCELLED, OfferStatus.EXPIRED]),
            and_(
                Offer.status == OfferStatus.ACTIVE,
                Offer.remaining_quantity.isnot(None),
                Offer.remaining_quantity < Offer.quantity,
            ),
        ),
    )


def _channel_state_next_retry_expression(state: Any):
    raw_retry_at = state.state_metadata[_CHANNEL_STATE_RETRY_AT_METADATA_KEY]
    retry_epoch = case(
        (func.json_typeof(raw_retry_at) == "number", raw_retry_at.as_float()),
        else_=None,
    )
    return func.to_timestamp(retry_epoch)


def _channel_state_next_retry_at(state: Any | None) -> datetime | None:
    metadata = getattr(state, "state_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    raw_value = metadata.get(_CHANNEL_STATE_RETRY_AT_METADATA_KEY)
    if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
        return None
    try:
        value = datetime.fromtimestamp(float(raw_value), tz=timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None
    return value


def _channel_state_drift_condition(state: Any):
    next_retry_at = _channel_state_next_retry_expression(state)
    return or_(
        state.id.is_(None),
        state.offer_version_id.is_(None),
        state.offer_version_id != Offer.version_id,
        state.telegram_message_id.is_(None),
        state.telegram_message_id != Offer.channel_message_id,
        next_retry_at.isnot(None),
    )


def _channel_state_due_condition(state: Any, *, now: datetime):
    next_retry_at = _channel_state_next_retry_expression(state)
    return or_(
        state.id.is_(None),
        next_retry_at.is_(None),
        next_retry_at <= now,
        and_(
            state.last_attempt_at.isnot(None),
            Offer.updated_at.isnot(None),
            Offer.updated_at > state.last_attempt_at,
        ),
    )


def _channel_state_age_anchor(state: Any):
    next_retry_at = _channel_state_next_retry_expression(state)
    return func.coalesce(
        next_retry_at,
        state.last_attempt_at,
        Offer.updated_at,
        Offer.created_at,
    )


def _age_seconds(now: datetime, value: Any) -> float | None:
    if not isinstance(value, datetime):
        return None
    normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    normalized_value = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return max(0.0, (normalized_now - normalized_value).total_seconds())


def _is_postgresql_session(db: Any) -> bool:
    get_bind = getattr(db, "get_bind", None)
    if get_bind is None:
        return False
    try:
        bind = get_bind()
        return str(getattr(getattr(bind, "dialect", None), "name", "")) == "postgresql"
    except Exception:
        return False


async def _try_acquire_channel_state_lock(db: Any, offer: Offer) -> bool:
    if not _is_postgresql_session(db):
        return True
    lock_key = f"{_CHANNEL_STATE_LOCK_PREFIX}:{getattr(offer, 'offer_public_id', '')}"
    result = await db.execute(
        sa_text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    return bool(result.scalar())


def _offer_status_value(offer: Offer) -> str:
    return str(getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)) or "").lower()


def _is_terminal_offer(offer: Offer) -> bool:
    return _offer_status_value(offer) in {
        OfferStatus.COMPLETED.value,
        OfferStatus.CANCELLED.value,
        OfferStatus.EXPIRED.value,
    }


async def run_offer_telegram_publication_cycle(*, limit: int | None = None) -> OfferPublicationCycleReport:
    assert_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION)

    from api.routers.offers import send_offer_to_channel_with_result

    allow_active_publication = not await active_publication_is_gated()
    async with AsyncSessionLocal() as db:
        report = await reconcile_offer_publications(
            db,
            server_mode="foreign",
            dry_run=False,
            limit=_worker_batch_limit(limit),
            send_offer_to_channel=send_offer_to_channel_with_result,
            allow_active_publication=allow_active_publication,
            telegram_send_spacing_seconds=_channel_send_spacing_seconds(),
            collect_observability=True,
        )

    rate_limited = int(report.get("telegram_rate_limited") or 0)
    return OfferPublicationCycleReport(
        processed=int(report.get("processed") or 0),
        repaired=int(report.get("repaired") or 0),
        failed=int(report.get("failed") or 0),
        gated=int(report.get("gated") or 0),
        status=str(report.get("status") or "unknown"),
        rate_limited=rate_limited,
        cooldown_seconds=(
            _rate_limit_cooldown_seconds(_coerce_int(report.get("telegram_retry_after_seconds")))
            if rate_limited
            else 0.0
        ),
        response_counts=tuple(sorted(dict(report.get("telegram_response_counts") or {}).items())),
        skipped_locked=int(report.get("skipped_locked") or 0),
        backlog_total=int(report.get("backlog_total") or 0),
        backlog_due=int(report.get("backlog_due") or 0),
        backlog_oldest_age_seconds=report.get("backlog_oldest_age_seconds"),
        backlog_oldest_due_age_seconds=report.get("backlog_oldest_due_age_seconds"),
    )


async def _load_channel_state_reconciliation_candidates(
    db: Any,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[OfferChannelStateCandidate]:
    current_time = now or utc_now()
    publication_state = aliased(OfferPublicationState)
    stmt = (
        select(Offer, publication_state)
        .outerjoin(
            publication_state,
            and_(
                publication_state.offer_public_id == Offer.offer_public_id,
                publication_state.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
            ),
        )
        .options(selectinload(Offer.commodity))
        .where(
            _channel_state_base_condition(),
            _channel_state_drift_condition(publication_state),
            _channel_state_due_condition(publication_state, now=current_time),
        )
        .order_by(_channel_state_age_anchor(publication_state).asc().nullsfirst(), Offer.id.asc())
        .limit(max(int(limit or 1), 1))
    )
    result = await db.execute(stmt)
    return [OfferChannelStateCandidate(offer=row[0], state=row[1]) for row in result.all()]


async def _channel_state_backlog(
    db: Any,
    *,
    now: datetime | None = None,
) -> OfferChannelStateBacklog:
    current_time = now or utc_now()
    publication_state = aliased(OfferPublicationState)
    due = _channel_state_due_condition(publication_state, now=current_time)
    age_anchor = func.coalesce(Offer.updated_at, Offer.created_at)
    stmt = (
        select(
            func.count(Offer.id),
            func.count(case((due, 1))),
            func.min(age_anchor),
            func.min(case((due, age_anchor), else_=None)),
        )
        .outerjoin(
            publication_state,
            and_(
                publication_state.offer_public_id == Offer.offer_public_id,
                publication_state.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
            ),
        )
        .where(
            _channel_state_base_condition(),
            _channel_state_drift_condition(publication_state),
        )
    )
    result = await db.execute(stmt)
    row = result.one()
    return OfferChannelStateBacklog(
        total=max(0, _coerce_int(row[0]) or 0),
        due=max(0, _coerce_int(row[1]) or 0),
        oldest_age_seconds=_age_seconds(current_time, row[2]),
        oldest_due_age_seconds=_age_seconds(current_time, row[3]),
    )


async def _ensure_channel_state_candidate_state(
    db: Any,
    candidate: OfferChannelStateCandidate,
) -> OfferPublicationState:
    state = candidate.state
    if state is None or _is_postgresql_session(db):
        state = await get_or_create_telegram_publication_state(db, candidate.offer)
        candidate.state = state
        refresh = getattr(db, "refresh", None)
        if refresh is not None and getattr(state, "id", None) is not None:
            await refresh(
                state,
                attribute_names=[
                    "status",
                    "offer_version_id",
                    "telegram_message_id",
                    "state_metadata",
                    "last_attempt_at",
                    "next_retry_at",
                ],
            )
            await refresh(
                candidate.offer,
                attribute_names=[
                    "status",
                    "version_id",
                    "quantity",
                    "remaining_quantity",
                    "channel_message_id",
                    "archived",
                    "updated_at",
                ],
            )
    return state


def _channel_state_candidate_still_due(
    candidate: OfferChannelStateCandidate,
    *,
    now: datetime,
) -> bool:
    offer = candidate.offer
    state = candidate.state
    if state is None:
        return True
    status = _offer_status_value(offer)
    remaining = _coerce_int(getattr(offer, "remaining_quantity", None))
    quantity = _coerce_int(getattr(offer, "quantity", None))
    eligible = _is_terminal_offer(offer) or (
        status == OfferStatus.ACTIVE.value
        and remaining is not None
        and quantity is not None
        and remaining < quantity
    )
    if not eligible or not _coerce_int(getattr(offer, "channel_message_id", None)):
        return False
    if bool(getattr(offer, "archived", False)):
        return False

    retry_at = _channel_state_next_retry_at(state)
    if retry_at is not None:
        normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        last_attempt = getattr(state, "last_attempt_at", None)
        updated_at = getattr(offer, "updated_at", None)
        changed_after_attempt = False
        if isinstance(last_attempt, datetime) and isinstance(updated_at, datetime):
            normalized_attempt = (
                last_attempt if last_attempt.tzinfo is not None else last_attempt.replace(tzinfo=timezone.utc)
            )
            normalized_updated = (
                updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=timezone.utc)
            )
            changed_after_attempt = normalized_updated > normalized_attempt
        if retry_at > normalized_now and not changed_after_attempt:
            return False

    return (
        _coerce_int(getattr(state, "offer_version_id", None))
        != _coerce_int(getattr(offer, "version_id", None))
        or _coerce_int(getattr(state, "telegram_message_id", None))
        != _coerce_int(getattr(offer, "channel_message_id", None))
        or retry_at is not None
    )


def _prepare_channel_state_candidate_state(
    state: OfferPublicationState,
    offer: Offer,
) -> None:
    message_id = _coerce_int(getattr(offer, "channel_message_id", None))
    if message_id and _coerce_int(getattr(state, "telegram_message_id", None)) != message_id:
        mark_telegram_publication_success(
            state,
            offer,
            message_id=message_id,
            chat_id=_coerce_int(settings.channel_id),
        )


def _mark_channel_state_applied(
    state: OfferPublicationState,
    offer: Offer,
    *,
    now: datetime,
    error_code: str | None = None,
) -> None:
    requested_status = (
        OfferPublicationStatus.DISABLED if _is_terminal_offer(offer) else OfferPublicationStatus.SENT
    )
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=requested_status,
        now=now,
        error_code=error_code,
        telegram_message_id=_coerce_int(getattr(offer, "channel_message_id", None)),
    )
    _set_channel_state_attempt_count(state, 0)


def _schedule_channel_state_retry(
    state: OfferPublicationState,
    offer: Offer,
    result: Any,
    *,
    now: datetime,
) -> float:
    metadata = getattr(state, "state_metadata", None)
    previous_version = (
        _coerce_int(metadata.get(_CHANNEL_STATE_RETRY_VERSION_METADATA_KEY))
        if isinstance(metadata, Mapping)
        else None
    )
    offer_version = _coerce_int(getattr(offer, "version_id", None))
    attempt_count = (_channel_state_attempt_count(state) if previous_version == offer_version else 0) + 1
    delay = _channel_state_retry_delay_seconds(
        attempt_count,
        retry_after_seconds=_coerce_int(getattr(result, "retry_after_seconds", None)),
    )
    retry_at = now + timedelta(seconds=delay)
    _set_channel_state_attempt_count(
        state,
        attempt_count,
        offer_version_id=offer_version,
        next_retry_at=retry_at,
    )
    state.last_attempt_at = now
    state.error_code = str(getattr(result, "reason", None) or "channel_state_repair_failed")[:96]
    error = getattr(result, "error", None)
    state.error_message = str(error)[:240] if error else None
    return delay


async def run_offer_channel_state_cycle(*, limit: int | None = None) -> OfferChannelStateCycleReport:
    """Repair Telegram channel presentation for offers already published to the channel."""
    assert_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION)
    processed = 0
    applied = 0
    failed = 0
    skipped_recent = 0
    rate_limited = 0
    bad_request = 0
    retryable_failed = 0
    non_retryable_remembered = 0
    skipped_locked = 0
    cooldown_seconds = 0.0
    response_counts: dict[str, int] = {}
    backlog = OfferChannelStateBacklog()

    async with AsyncSessionLocal() as db:
        candidates = await _load_channel_state_reconciliation_candidates(
            db,
            limit=_worker_batch_limit(limit),
        )
        for index, candidate in enumerate(candidates):
            offer = candidate.offer
            if not await _try_acquire_channel_state_lock(db, offer):
                skipped_locked += 1
                await db.commit()
                continue

            state = await _ensure_channel_state_candidate_state(db, candidate)
            if not _channel_state_candidate_still_due(candidate, now=utc_now()):
                skipped_recent += 1
                await db.commit()
                continue
            _prepare_channel_state_candidate_state(state, offer)
            processed += 1
            result = await apply_offer_channel_state_with_result(
                offer,
                publication_state=state,
                reason="offer_channel_state_reconcile",
                timeout=5,
            )
            response_counts[result.response_class] = response_counts.get(result.response_class, 0) + 1
            if result.ok:
                applied += 1
                _mark_channel_state_applied(state, offer, now=utc_now())
            elif result.response_class == "429":
                failed += 1
                rate_limited += 1
                retryable_failed += 1
                _schedule_channel_state_retry(state, offer, result, now=utc_now())
                cooldown_seconds = max(cooldown_seconds, _rate_limit_cooldown_seconds(result.retry_after_seconds))
            elif result.response_class == "400":
                failed += 1
                bad_request += 1
                if _is_terminal_offer(offer):
                    non_retryable_remembered += 1
                    _mark_channel_state_applied(
                        state,
                        offer,
                        now=utc_now(),
                        error_code="telegram_terminal_bad_request",
                    )
                else:
                    retryable_failed += 1
                    _schedule_channel_state_retry(state, offer, result, now=utc_now())
            else:
                failed += 1
                retryable_failed += 1
                _schedule_channel_state_retry(state, offer, result, now=utc_now())

            await db.commit()
            if result.response_class == "429":
                break

            if index < len(candidates) - 1:
                spacing_seconds = _channel_edit_spacing_seconds()
                if spacing_seconds > 0:
                    await asyncio.sleep(spacing_seconds)

        backlog = await _channel_state_backlog(db)

    return OfferChannelStateCycleReport(
        processed=processed,
        applied=applied,
        failed=failed,
        skipped_recent=skipped_recent,
        rate_limited=rate_limited,
        bad_request=bad_request,
        retryable_failed=retryable_failed,
        non_retryable_remembered=non_retryable_remembered,
        cooldown_seconds=cooldown_seconds,
        response_counts=tuple(sorted(response_counts.items())),
        skipped_locked=skipped_locked,
        backlog_total=backlog.total,
        backlog_due=backlog.due,
        backlog_oldest_age_seconds=backlog.oldest_age_seconds,
        backlog_oldest_due_age_seconds=backlog.oldest_due_age_seconds,
    )


async def offer_telegram_publication_loop() -> None:
    assert_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION)
    logger.info(
        "Offer Telegram publication worker started",
        extra={
            "event": "offer_publication_worker.started",
            "job_name": JOB_OFFER_TELEGRAM_PUBLICATION,
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": _worker_batch_limit(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        sleep_seconds = _worker_interval_seconds()
        with job_context(JOB_OFFER_TELEGRAM_PUBLICATION, iteration=iteration) as run_id:
            try:
                report = await run_offer_telegram_publication_cycle()
                channel_state_report = await run_offer_channel_state_cycle()
                sleep_seconds = max(sleep_seconds, report.cooldown_seconds, channel_state_report.cooldown_seconds)
                if (
                    report.processed
                    or report.repaired
                    or report.failed
                    or report.gated
                    or channel_state_report.processed
                    or channel_state_report.applied
                    or channel_state_report.failed
                ):
                    logger.info(
                        "Offer Telegram publication worker cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_OFFER_TELEGRAM_PUBLICATION,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed": report.processed,
                            "repaired": report.repaired,
                            "failed": report.failed,
                            "gated": report.gated,
                            "status": report.status,
                            "publication_rate_limited": report.rate_limited,
                            "publication_cooldown_seconds": report.cooldown_seconds,
                            "publication_response_counts": dict(report.response_counts),
                            "publication_skipped_locked": report.skipped_locked,
                            "publication_backlog_total": report.backlog_total,
                            "publication_backlog_due": report.backlog_due,
                            "publication_backlog_oldest_age_seconds": report.backlog_oldest_age_seconds,
                            "publication_backlog_oldest_due_age_seconds": (
                                report.backlog_oldest_due_age_seconds
                            ),
                            "channel_state_processed": channel_state_report.processed,
                            "channel_state_applied": channel_state_report.applied,
                            "channel_state_failed": channel_state_report.failed,
                            "channel_state_skipped_recent": channel_state_report.skipped_recent,
                            "channel_state_rate_limited": channel_state_report.rate_limited,
                            "channel_state_bad_request": channel_state_report.bad_request,
                            "channel_state_retryable_failed": channel_state_report.retryable_failed,
                            "channel_state_non_retryable_remembered": (
                                channel_state_report.non_retryable_remembered
                            ),
                            "channel_state_cooldown_seconds": channel_state_report.cooldown_seconds,
                            "channel_state_response_counts": dict(channel_state_report.response_counts),
                            "channel_state_skipped_locked": channel_state_report.skipped_locked,
                            "channel_state_backlog_total": channel_state_report.backlog_total,
                            "channel_state_backlog_due": channel_state_report.backlog_due,
                            "channel_state_backlog_oldest_age_seconds": (
                                channel_state_report.backlog_oldest_age_seconds
                            ),
                            "channel_state_backlog_oldest_due_age_seconds": (
                                channel_state_report.backlog_oldest_due_age_seconds
                            ),
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in offer Telegram publication worker loop: %s",
                    exc,
                    job_name=JOB_OFFER_TELEGRAM_PUBLICATION,
                    run_id=run_id,
                )

        await asyncio.sleep(sleep_seconds)
