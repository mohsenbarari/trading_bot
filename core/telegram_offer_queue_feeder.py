"""Subordinate Offer publication/edit feeders for the shared Telegram queue."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from core.background_job_authority import (
    JOB_TELEGRAM_DELIVERY_QUEUE,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.cross_server_recovery_service import active_publication_is_gated
from core.services.telegram_offer_queue_service import (
    TelegramOfferQueueError,
    enqueue_current_offer_delivery,
    load_offer_edit_queue_candidates,
    load_offer_edit_fresh_success_counts,
    load_offer_publication_queue_candidates,
)
from core.services.telegram_delivery_queue_service import telegram_delivery_database_now
from core.telegram_delivery_queue_contract import EDIT_CATCH_UP_FRESH_COUNT
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.trading_settings import get_trading_settings_async


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


@dataclass(frozen=True, slots=True)
class TelegramOfferQueueFeederReport:
    publication_handoffs: int = 0
    edit_handoffs: int = 0
    deduplicated: int = 0
    skipped: int = 0
    invalid: int = 0
    publication_gated: bool = False


def _assert_queue_runtime_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1 or not runtime.queue_worker_enabled:
        raise RuntimeError("telegram_offer_queue_feeder_requires_queue_owner")


def _batch_limit() -> int:
    return max(
        1,
        int(getattr(settings, "telegram_offer_queue_feeder_batch_limit", 25)),
    )


def _interval_seconds() -> float:
    return max(
        0.1,
        float(getattr(settings, "telegram_offer_queue_feeder_interval_seconds", 0.5)),
    )


async def _handoff_candidates(
    db,
    candidates,
    *,
    expected_channel_id: int,
    offer_expiry_minutes: int,
    now,
) -> tuple[int, int, int, int]:
    handed_off = 0
    deduplicated = 0
    skipped = 0
    invalid = 0
    for candidate in candidates:
        try:
            # Keep all candidate row locks until the batch commit, while one
            # malformed candidate rolls back only its own handoff work.
            async with db.begin_nested():
                result = await enqueue_current_offer_delivery(
                    db,
                    current_server=current_server(),
                    offer=candidate.offer,
                    state=candidate.state,
                    expected_channel_id=expected_channel_id,
                    offer_expiry_minutes=offer_expiry_minutes,
                    now=now,
                )
            if result.queue_result is None:
                skipped += 1
            elif result.queue_result.created:
                handed_off += 1
            else:
                deduplicated += 1
        except TelegramOfferQueueError as exc:
            invalid += 1
            logger.error(
                "Offer queue handoff rejected unsafe candidate",
                extra={
                    "event": "telegram_offer_queue_feeder.invalid_candidate",
                    "offer_public_id": str(
                        getattr(candidate.offer, "offer_public_id", "") or ""
                    )[:40],
                    "reason": str(exc)[:120],
                },
            )
    await db.commit()
    return handed_off, deduplicated, skipped, invalid


async def run_telegram_offer_queue_handoff_cycle() -> TelegramOfferQueueFeederReport:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    try:
        channel_id = int(settings.channel_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("telegram_offer_queue_channel_invalid") from exc
    if channel_id == 0:
        raise RuntimeError("telegram_offer_queue_channel_invalid")

    trading_settings = await get_trading_settings_async()
    expiry_minutes = int(getattr(trading_settings, "offer_expiry_minutes", 0) or 0)
    if expiry_minutes <= 0:
        raise RuntimeError("telegram_offer_queue_expiry_invalid")

    publication_gated = await active_publication_is_gated()
    publication_counts = (0, 0, 0, 0)
    edit_counts = (0, 0, 0, 0)
    async with AsyncSessionLocal() as db:
        if not publication_gated:
            publication_now = await telegram_delivery_database_now(db)
            publication_candidates = await load_offer_publication_queue_candidates(
                db,
                limit=_batch_limit(),
            )
            publication_counts = await _handoff_candidates(
                db,
                publication_candidates,
                expected_channel_id=channel_id,
                offer_expiry_minutes=expiry_minutes,
                now=publication_now,
            )

        edit_success_counts = await load_offer_edit_fresh_success_counts(db)
        catch_up_due_ranks = frozenset(
            rank
            for rank, count in edit_success_counts.items()
            if count >= EDIT_CATCH_UP_FRESH_COUNT
        )
        edit_now = await telegram_delivery_database_now(db)
        edit_candidates = await load_offer_edit_queue_candidates(
            db,
            limit=_batch_limit(),
            catch_up_due_ranks=catch_up_due_ranks,
            now=edit_now,
        )
        edit_counts = await _handoff_candidates(
            db,
            edit_candidates,
            expected_channel_id=channel_id,
            offer_expiry_minutes=expiry_minutes,
            now=edit_now,
        )

    return TelegramOfferQueueFeederReport(
        publication_handoffs=publication_counts[0],
        edit_handoffs=edit_counts[0],
        deduplicated=publication_counts[1] + edit_counts[1],
        skipped=publication_counts[2] + edit_counts[2],
        invalid=publication_counts[3] + edit_counts[3],
        publication_gated=publication_gated,
    )


async def telegram_offer_queue_handoff_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    iteration = 0
    while True:
        iteration += 1
        started = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_offer_queue_handoff_cycle()
                if any(
                    (
                        report.publication_handoffs,
                        report.edit_handoffs,
                        report.deduplicated,
                        report.skipped,
                        report.invalid,
                    )
                ):
                    logger.info(
                        "Telegram Offer queue feeder cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "run_id": run_id,
                            "iteration": iteration,
                            "publication_handoffs": report.publication_handoffs,
                            "edit_handoffs": report.edit_handoffs,
                            "deduplicated": report.deduplicated,
                            "skipped": report.skipped,
                            "invalid": report.invalid,
                            "publication_gated": report.publication_gated,
                            "duration_ms": duration_ms_since(started),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram Offer queue feeder: %s",
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(started),
                )
        await asyncio.sleep(_interval_seconds())
