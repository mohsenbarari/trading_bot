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
from core.services.telegram_offer_channel_service import apply_offer_channel_state
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


@dataclass(frozen=True, slots=True)
class OfferChannelStateCycleReport:
    processed: int
    applied: int
    failed: int
    skipped_recent: int


def _worker_interval_seconds() -> float:
    return max(0.2, float(getattr(settings, "offer_publication_worker_interval_seconds", 1.0)))


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "offer_publication_worker_batch_limit", 25)))


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


async def run_offer_telegram_publication_cycle(*, limit: int | None = None) -> OfferPublicationCycleReport:
    assert_background_job_authority(JOB_OFFER_TELEGRAM_PUBLICATION)

    from api.routers.offers import send_offer_to_channel

    allow_active_publication = not await active_publication_is_gated()
    async with AsyncSessionLocal() as db:
        report = await reconcile_offer_publications(
            db,
            server_mode="foreign",
            dry_run=False,
            limit=_worker_batch_limit(limit),
            send_offer_to_channel=send_offer_to_channel,
            allow_active_publication=allow_active_publication,
        )

    return OfferPublicationCycleReport(
        processed=int(report.get("processed") or 0),
        repaired=int(report.get("repaired") or 0),
        failed=int(report.get("failed") or 0),
        gated=int(report.get("gated") or 0),
        status=str(report.get("status") or "unknown"),
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

    async with AsyncSessionLocal() as db:
        offers = await _load_channel_state_reconciliation_offers(db, limit=_worker_batch_limit(limit))
        for offer in offers:
            signature = _offer_channel_state_signature(offer)
            if _offer_channel_state_recently_applied(offer, signature):
                skipped_recent += 1
                continue

            processed += 1
            result = await apply_offer_channel_state(offer, reason="offer_channel_state_reconcile", timeout=5)
            if result:
                applied += 1
                _remember_offer_channel_state_applied(offer, signature)
            else:
                failed += 1

    return OfferChannelStateCycleReport(
        processed=processed,
        applied=applied,
        failed=failed,
        skipped_recent=skipped_recent,
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
        with job_context(JOB_OFFER_TELEGRAM_PUBLICATION, iteration=iteration) as run_id:
            try:
                report = await run_offer_telegram_publication_cycle()
                channel_state_report = await run_offer_channel_state_cycle()
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
                            "channel_state_processed": channel_state_report.processed,
                            "channel_state_applied": channel_state_report.applied,
                            "channel_state_failed": channel_state_report.failed,
                            "channel_state_skipped_recent": channel_state_report.skipped_recent,
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

        await asyncio.sleep(_worker_interval_seconds())
