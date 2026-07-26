"""Feature-flagged adapter invoked by the existing shared offer parser."""

from __future__ import annotations

import asyncio
from functools import lru_cache
import logging
import random
import time
from datetime import datetime, timezone
from typing import Coroutine

from core.config import settings
from core.market_intelligence.contracts import ShadowObservation
from core.market_intelligence.observability import (
    record_shadow_observation,
    record_shadow_runtime_event,
)
from core.market_intelligence.service import CoinIntelligenceShadowService
from core.market_intelligence.snapshot import AtomicJsonSnapshotProvider


logger = logging.getLogger(__name__)
_BACKGROUND_TASKS: set[asyncio.Task] = set()


@lru_cache(maxsize=1)
def _configured_service() -> CoinIntelligenceShadowService | None:
    snapshot_path = str(
        settings.coin_intelligence_snapshot_path or ""
    ).strip()
    if not settings.coin_intelligence_shadow_enabled or not snapshot_path:
        return None
    bundle_path = str(settings.coin_intelligence_bundle_path or "").strip()
    return CoinIntelligenceShadowService(
        snapshot_provider=AtomicJsonSnapshotProvider(snapshot_path),
        bundle_path=bundle_path or None,
    )


def _task_finished(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        # Every worker should already contain its own boundary.  This final
        # guard prevents an accidental task exception from becoming unhandled.
        logger.exception(
            "Coin-intelligence Shadow task escaped its isolation boundary",
            extra={"event": "coin_intelligence.shadow_task_unhandled"},
        )


def _schedule(coro: Coroutine, *, component: str) -> bool:
    if len(_BACKGROUND_TASKS) >= int(
        settings.coin_intelligence_shadow_max_inflight
    ):
        coro.close()
        record_shadow_runtime_event(
            component=component,
            status="queue_full",
        )
        return False
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        coro.close()
        record_shadow_runtime_event(
            component=component,
            status="no_event_loop",
        )
        return False
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_task_finished)
    record_shadow_runtime_event(component=component, status="scheduled")
    return True


def _sampled_in() -> bool:
    return random.random() < float(
        settings.coin_intelligence_shadow_sample_rate
    )


async def _infer_implicit_commodity(
    *,
    price: int,
    settlement: str,
    current_commodity: str,
    requested_at: datetime,
) -> tuple[ShadowObservation, int]:
    service = _configured_service()
    if service is None:
        raise RuntimeError("shadow service is not configured")
    started = time.monotonic()
    observation = await asyncio.wait_for(
        asyncio.to_thread(
            service.observe_implicit_commodity,
            price=price,
            settlement=settlement,
            current_commodity=current_commodity,
            now=requested_at,
        ),
        timeout=float(settings.coin_intelligence_shadow_timeout_seconds),
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    return observation, latency_ms


async def observe_implicit_commodity_shadow(
    *,
    price: int,
    settlement: str,
    current_commodity: str,
) -> None:
    """Observe safely; every failure is isolated from offer parsing."""

    requested_at = datetime.now(timezone.utc)
    try:
        if _configured_service() is None:
            return
        observation, latency_ms = await _infer_implicit_commodity(
            price=price,
            settlement=settlement,
            current_commodity=current_commodity,
            requested_at=requested_at,
        )
        record_shadow_observation(observation)
        if settings.coin_intelligence_shadow_persist_enabled:
            try:
                from core.market_intelligence.ledger import (
                    persist_parser_observation,
                )

                await persist_parser_observation(
                    observation,
                    requested_at=requested_at,
                    latency_ms=latency_ms,
                )
            except Exception:
                record_shadow_runtime_event(
                    component="commodity_ranker",
                    status="persistence_error",
                )
                logger.exception(
                    "Shadow parser observation persistence failed",
                    extra={
                        "event": (
                            "coin_intelligence.shadow_parser_persistence_failed"
                        )
                    },
                )
        record_shadow_runtime_event(
            component="commodity_ranker",
            status="completed",
        )
    except asyncio.TimeoutError:
        record_shadow_runtime_event(
            component="commodity_ranker",
            status="timeout",
        )
    except Exception:
        # Shadow mode is prohibited from changing parser availability or output.
        try:
            record_shadow_observation(
                ShadowObservation(
                    status="RUNTIME_ERROR",
                    settlement=str(settlement),
                    current_commodity=current_commodity,
                    inferred_commodity=None,
                    agrees_with_current=None,
                    requires_user_confirmation=True,
                    decision_reason="UNEXPECTED_SHADOW_RUNTIME_ERROR",
                    bundle_version=None,
                    snapshot_version=None,
                )
            )
            record_shadow_runtime_event(
                component="commodity_ranker",
                status="runtime_error",
            )
        except Exception:
            return


def schedule_implicit_commodity_shadow(
    *,
    price: int,
    settlement: str,
    current_commodity: str,
) -> bool:
    """Schedule observation without extending the parser response path."""

    if _configured_service() is None:
        return False
    if not _sampled_in():
        record_shadow_runtime_event(
            component="commodity_ranker",
            status="sampled_out",
        )
        return False
    return _schedule(
        observe_implicit_commodity_shadow(
            price=price,
            settlement=settlement,
            current_commodity=current_commodity,
        ),
        component="commodity_ranker",
    )


def _as_utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


async def _process_project_offer(
    local_id: int,
    *,
    requested_at: datetime,
) -> None:
    from sqlalchemy import select

    from core.db import AsyncSessionLocal
    from core.market_intelligence.ledger import (
        persist_rate_prediction,
        shadow_subject_fingerprint,
    )
    from core.market_intelligence.hybrid_v2 import evaluate_hybrid_v2
    from models.commodity import Commodity
    from models.offer import Offer

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Offer, Commodity.name)
                .join(Commodity, Commodity.id == Offer.commodity_id)
                .where(Offer.id == int(local_id))
            )
        ).one_or_none()
    if row is None:
        return
    offer, commodity_name = row
    offer_public_id = str(offer.offer_public_id or "").strip()
    if not offer_public_id:
        return
    service = _configured_service()
    if service is None:
        return
    started = time.monotonic()
    prediction = await asyncio.wait_for(
        asyncio.to_thread(
            service.estimate_canonical_rate,
            commodity=str(commodity_name),
            settlement=str(
                getattr(
                    offer.settlement_type,
                    "value",
                    offer.settlement_type,
                )
            ),
            trade_form="PHYSICAL",
            now=requested_at,
        ),
        timeout=float(settings.coin_intelligence_shadow_timeout_seconds),
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    candidates = (
        [evaluate_hybrid_v2(prediction, as_of_utc=requested_at)]
        if settings.coin_intelligence_shadow_numeric_v2_enabled
        else []
    )
    await persist_rate_prediction(
        prediction,
        commodity_id=int(offer.commodity_id),
        requested_at=requested_at,
        source_surface=f"project_{str(offer.home_server or 'unknown')}",
        subject_kind="OFFER",
        subject_fingerprint=shadow_subject_fingerprint(
            "OFFER",
            offer_public_id,
        ),
        event_idempotency_key=f"offer:{offer_public_id}",
        observed_project_price=int(offer.price),
        latency_ms=latency_ms,
        candidate_predictions=candidates,
    )


async def _process_project_trade(local_id: int) -> int:
    from sqlalchemy import select

    from core.db import AsyncSessionLocal
    from core.market_intelligence.ledger import (
        attach_confirmed_trade_outcomes,
        shadow_subject_fingerprint,
    )
    from models.offer import Offer
    from models.trade import Trade, TradeStatus

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Trade, Offer.offer_public_id)
                .join(Offer, Offer.id == Trade.offer_id)
                .where(Trade.id == int(local_id))
            )
        ).one_or_none()
    if row is None:
        return 0
    trade, offer_public_id = row
    status = str(getattr(trade.status, "value", trade.status)).lower()
    if status != TradeStatus.COMPLETED.value or not offer_public_id:
        return 0
    occurred_at = _as_utc(trade.completed_at or trade.created_at)
    return await attach_confirmed_trade_outcomes(
        offer_subject_fingerprint=shadow_subject_fingerprint(
            "OFFER",
            str(offer_public_id),
        ),
        actual_commodity_id=int(trade.commodity_id),
        settlement=str(
            getattr(
                trade.settlement_type,
                "value",
                trade.settlement_type,
            )
        ),
        trade_form="PHYSICAL",
        actual_project_price=int(trade.price),
        occurred_at_utc=occurred_at,
        label_status="UNREVIEWED",
        review_reason="PROJECT_EVENTS_SHADOW_NOT_TRAINING_ELIGIBLE",
    )


async def _observe_project_market_event(event) -> None:
    component = (
        "project_offer" if str(event.kind).upper() == "OFFER"
        else "project_trade"
    )
    try:
        if component == "project_offer":
            requested_at = _as_utc(
                getattr(event, "observed_after_commit_at_utc", None)
            )
            await _process_project_offer(
                int(event.local_id),
                requested_at=requested_at,
            )
        else:
            created = 0
            # An immediately executed trade can commit while the independent
            # offer prediction is still running.  Bounded retries preserve
            # ordering without delaying either business transaction.
            for delay in (0.0, 0.20, 0.50, 1.0):
                if delay:
                    await asyncio.sleep(delay)
                created = await _process_project_trade(int(event.local_id))
                if created:
                    break
            if not created:
                record_shadow_runtime_event(
                    component=component,
                    status="no_prior_prediction",
                )
        record_shadow_runtime_event(component=component, status="completed")
    except asyncio.TimeoutError:
        record_shadow_runtime_event(component=component, status="timeout")
    except Exception:
        record_shadow_runtime_event(
            component=component,
            status="persistence_error",
        )
        logger.exception(
            "Project market event Shadow observation failed",
            extra={
                "event": "coin_intelligence.project_event_failed",
                "event_kind": str(getattr(event, "kind", "UNKNOWN"))[:24],
            },
        )


def schedule_project_market_event(event) -> bool:
    """Schedule a committed project event without retaining raw user data."""

    if not (
        settings.coin_intelligence_shadow_enabled
        and settings.coin_intelligence_shadow_project_events_enabled
        and settings.coin_intelligence_shadow_persist_enabled
        and _configured_service() is not None
    ):
        return False
    component = (
        "project_offer"
        if str(getattr(event, "kind", "")).upper() == "OFFER"
        else "project_trade"
    )
    if not _sampled_in():
        record_shadow_runtime_event(
            component=component,
            status="sampled_out",
        )
        return False
    return _schedule(
        _observe_project_market_event(event),
        component=component,
    )
