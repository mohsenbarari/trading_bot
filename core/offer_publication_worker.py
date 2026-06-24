"""Persistent foreign worker for Telegram offer-channel publication repair."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from core.background_job_authority import JOB_OFFER_TELEGRAM_PUBLICATION, assert_background_job_authority
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.cross_server_recovery_service import active_publication_is_gated
from core.services.offer_publication_reconciliation_service import reconcile_offer_publications

logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


@dataclass(frozen=True, slots=True)
class OfferPublicationCycleReport:
    processed: int
    repaired: int
    failed: int
    gated: int
    status: str


def _worker_interval_seconds() -> float:
    return max(0.2, float(getattr(settings, "offer_publication_worker_interval_seconds", 1.0)))


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "offer_publication_worker_batch_limit", 25)))


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
                if report.processed or report.repaired or report.failed or report.gated:
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
