"""Persistent foreign worker for Telegram offer-channel publication repair."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from core.background_job_authority import JOB_OFFER_TELEGRAM_PUBLICATION, assert_background_job_authority
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.cross_server_recovery_service import active_publication_is_gated
from core.services.offer_publication_reconciliation_service import reconcile_offer_publications
from core.services.telegram_offer_channel_service import apply_offer_channel_state_with_result
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_channel_state_applied_signatures: dict[int, tuple[object, ...]] = {}
_CHANNEL_STATE_APPLIED_MAX_KEYS = 5000


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


def _offer_channel_state_signature(offer: Offer) -> tuple[object, ...]:
    return (
        getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)),
        _coerce_int(getattr(offer, "version_id", None)),
        _coerce_int(getattr(offer, "quantity", None)),
        _coerce_int(getattr(offer, "remaining_quantity", None)),
        _coerce_int(getattr(offer, "channel_message_id", None)),
    )


def _offer_channel_state_recently_applied(offer: Offer, signature: tuple[object, ...]) -> bool:
    offer_id = _coerce_int(getattr(offer, "id", None))
    if offer_id is None:
        return False
    return _channel_state_applied_signatures.get(offer_id) == signature


def _remember_offer_channel_state_applied(offer: Offer, signature: tuple[object, ...]) -> None:
    offer_id = _coerce_int(getattr(offer, "id", None))
    if offer_id is None:
        return
    _channel_state_applied_signatures[offer_id] = signature
    if len(_channel_state_applied_signatures) <= _CHANNEL_STATE_APPLIED_MAX_KEYS:
        return
    overflow_count = len(_channel_state_applied_signatures) - _CHANNEL_STATE_APPLIED_MAX_KEYS
    for stale_offer_id in list(_channel_state_applied_signatures)[:overflow_count]:
        _channel_state_applied_signatures.pop(stale_offer_id, None)


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
    )


async def _load_channel_state_reconciliation_offers(db, *, limit: int) -> list[Offer]:
    stmt = (
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(
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
        .order_by(Offer.updated_at.desc().nullslast(), Offer.id.desc())
        .limit(max(int(limit or 1), 1))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


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
    cooldown_seconds = 0.0
    response_counts: dict[str, int] = {}

    async with AsyncSessionLocal() as db:
        offers = await _load_channel_state_reconciliation_offers(db, limit=_worker_batch_limit(limit))
        for index, offer in enumerate(offers):
            signature = _offer_channel_state_signature(offer)
            if _offer_channel_state_recently_applied(offer, signature):
                skipped_recent += 1
                continue

            processed += 1
            result = await apply_offer_channel_state_with_result(
                offer,
                reason="offer_channel_state_reconcile",
                timeout=5,
            )
            response_counts[result.response_class] = response_counts.get(result.response_class, 0) + 1
            if result.ok:
                applied += 1
                _remember_offer_channel_state_applied(offer, signature)
            elif result.response_class == "429":
                failed += 1
                rate_limited += 1
                retryable_failed += 1
                cooldown_seconds = max(cooldown_seconds, _rate_limit_cooldown_seconds(result.retry_after_seconds))
                break
            elif result.response_class == "400":
                failed += 1
                bad_request += 1
                if _is_terminal_offer(offer):
                    non_retryable_remembered += 1
                    _remember_offer_channel_state_applied(offer, signature)
                else:
                    retryable_failed += 1
            else:
                failed += 1
                if result.response_class != "skipped":
                    retryable_failed += 1

            if index < len(offers) - 1:
                spacing_seconds = _channel_edit_spacing_seconds()
                if spacing_seconds > 0:
                    await asyncio.sleep(spacing_seconds)

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
