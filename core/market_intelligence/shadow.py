"""Feature-flagged adapter invoked by the existing shared offer parser."""

from __future__ import annotations

import asyncio
from dataclasses import replace
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
_GEMMA_TASKS: set[asyncio.Task] = set()


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
    _finish_scoped_task(task, _BACKGROUND_TASKS)


def _finish_scoped_task(
    task: asyncio.Task,
    collection: set[asyncio.Task],
) -> None:
    collection.discard(task)
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
    return _schedule_scoped(
        coro,
        component=component,
        collection=_BACKGROUND_TASKS,
        limit=int(settings.coin_intelligence_shadow_max_inflight),
    )


def _schedule_scoped(
    coro: Coroutine,
    *,
    component: str,
    collection: set[asyncio.Task],
    limit: int,
) -> bool:
    if len(collection) >= int(limit):
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
    collection.add(task)
    task.add_done_callback(
        lambda completed: _finish_scoped_task(completed, collection)
    )
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
    # The deterministic ranker is bounded local CPU work. Running it directly
    # avoids a timed-out Python thread surviving process shutdown.
    observation = service.observe_implicit_commodity(
        price=price,
        settlement=settlement,
        current_commodity=current_commodity,
        now=requested_at,
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    if latency_ms > int(
        float(settings.coin_intelligence_shadow_timeout_seconds) * 1000
    ):
        raise asyncio.TimeoutError
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


async def _observe_gemma_parser_candidate(
    *,
    text: str,
    side: str,
    settlement: str,
    quantity: int,
    price: int,
    current_commodity: str,
) -> None:
    """Query the bounded local sidecar; raw text never reaches persistence."""

    from core.market_intelligence.gemma_parser import (
        GEMMA_PARSER_VERSION,
        infer_gemma_parser_candidate,
    )
    from core.market_intelligence.ledger import persist_parser_observation

    service = _configured_service()
    if service is None:
        return
    try:
        bundle = service._loaded_bundle()
        candidate = await asyncio.to_thread(
            infer_gemma_parser_candidate,
            text,
            endpoint=str(
                settings.coin_intelligence_shadow_gemma_endpoint
            ),
            canonical_commodities=bundle.canonical_commodity_names,
            timeout_seconds=float(
                settings.coin_intelligence_shadow_gemma_timeout_seconds
            ),
        )
        normalized = candidate.to_dict()
        expected = {
            "side": str(side).upper(),
            "settlement": str(settlement).upper(),
            "quantity": int(quantity),
            "price": int(price),
            "commodity": str(current_commodity),
        }
        disagreement = [
            name
            for name, value in expected.items()
            if normalized.get(name) != value
        ]
        observation = ShadowObservation(
            status="AMBIGUOUS" if candidate.abstain else "INFERRED",
            settlement=str(settlement).upper(),
            current_commodity=str(current_commodity),
            inferred_commodity=candidate.commodity,
            agrees_with_current=(
                candidate.commodity == current_commodity
                if candidate.commodity is not None
                else None
            ),
            requires_user_confirmation=bool(
                candidate.abstain or disagreement
            ),
            decision_reason=str(candidate.reason_code),
            bundle_version=GEMMA_PARSER_VERSION,
            snapshot_version=None,
        )
        await persist_parser_observation(
            observation,
            requested_at=datetime.now(timezone.utc),
            source_surface="offer_parser_gemma",
            component="GEMMA_PARSER_CANDIDATE",
            candidate_payload=normalized,
            disagreement_fields=disagreement,
        )
        record_shadow_runtime_event(
            component="gemma_parser",
            status="completed",
        )
    except (TimeoutError, asyncio.TimeoutError):
        record_shadow_runtime_event(
            component="gemma_parser",
            status="timeout",
        )
    except Exception:
        # No exception message is logged because a third-party runtime may
        # include the prompt in it.
        record_shadow_runtime_event(
            component="gemma_parser",
            status="runtime_error",
        )


def schedule_gemma_parser_shadow(
    *,
    text: str,
    side: str,
    settlement: str,
    quantity: int,
    price: int,
    current_commodity: str,
) -> bool:
    if not (
        settings.coin_intelligence_shadow_enabled
        and settings.coin_intelligence_shadow_persist_enabled
        and settings.coin_intelligence_shadow_gemma_parser_enabled
        and settings.coin_intelligence_shadow_gemma_endpoint
        and _configured_service() is not None
    ):
        return False
    if not _sampled_in():
        return False
    return _schedule_scoped(
        _observe_gemma_parser_candidate(
            text=text,
            side=side,
            settlement=settlement,
            quantity=quantity,
            price=price,
            current_commodity=current_commodity,
        ),
        component="gemma_parser",
        collection=_GEMMA_TASKS,
        limit=1,
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
    from sqlalchemy import case, func, or_, select

    from core.db import AsyncSessionLocal
    from core.market_intelligence.basis_v2 import evaluate_basis_v2
    from core.market_intelligence.feature_store_v2 import (
        load_feature_context_v2,
    )
    from core.market_intelligence.features_v2 import (
        build_feature_snapshot_v2,
    )
    from core.market_intelligence.hybrid_v2 import evaluate_hybrid_v2
    from core.market_intelligence.ledger import (
        persist_rate_prediction,
        shadow_subject_fingerprint,
    )
    from core.market_intelligence.low_date_v2 import evaluate_low_date_v2
    from core.market_intelligence.quality import evaluate_offer_quality
    from models.commodity import Commodity
    from models.offer import Offer, OfferStatus, OfferType

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
        book = (
            await session.execute(
                select(
                    func.min(
                        case(
                            (Offer.offer_type == OfferType.BUY, Offer.price),
                            else_=None,
                        )
                    ),
                    func.max(
                        case(
                            (Offer.offer_type == OfferType.SELL, Offer.price),
                            else_=None,
                        )
                    ),
                ).where(
                    Offer.id != int(local_id),
                    Offer.commodity_id == int(offer.commodity_id),
                    Offer.settlement_type == offer.settlement_type,
                    or_(
                        Offer.status == OfferStatus.ACTIVE,
                        func.coalesce(
                            Offer.expired_at,
                            Offer.updated_at,
                        )
                        >= requested_at,
                    ),
                    Offer.exclude_from_competitive_price.is_(False),
                    Offer.created_at < requested_at,
                )
            )
        ).one()
    offer_public_id = str(offer.offer_public_id or "").strip()
    if not offer_public_id:
        return
    service = _configured_service()
    if service is None:
        return
    started = time.monotonic()
    settlement = str(
        getattr(
            offer.settlement_type,
            "value",
            offer.settlement_type,
        )
    )
    prediction = service.estimate_canonical_rate(
        commodity=str(commodity_name),
        settlement=settlement,
        trade_form="PHYSICAL",
        now=requested_at,
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    if latency_ms > int(
        float(settings.coin_intelligence_shadow_timeout_seconds) * 1000
    ):
        raise asyncio.TimeoutError

    if settings.coin_intelligence_shadow_feature_v2_enabled:
        context = await load_feature_context_v2(
            commodity_id=int(offer.commodity_id),
            settlement=settlement,
            trade_form="PHYSICAL",
            cutoff_utc=requested_at,
        )
        evidence_v2 = build_feature_snapshot_v2(
            prediction.evidence,
            as_of_utc=requested_at,
            same_market_history=context["same_market_history"],
            settlement_basis=context["settlement_basis"],
            previous_regime_label=context["previous_regime_label"],
        )
        prediction = replace(
            prediction,
            feature_schema_version=str(
                evidence_v2["schema_version"]
            ),
            evidence=evidence_v2,
        )

    quality_decision = None
    if settings.coin_intelligence_shadow_quality_gate_enabled:
        quality_decision = evaluate_offer_quality(
            side=str(
                getattr(offer.offer_type, "value", offer.offer_type)
            ).upper(),
            price_project=int(offer.price),
            lowest_active_buy=book[0],
            highest_active_sell=book[1],
            regime_v2=(
                prediction.evidence.get("market_regime_v2") or {}
            ),
            structural_reference_project=prediction.center_project_price,
        )
        evidence = dict(prediction.evidence)
        evidence["quality"] = {
            "policy_version": quality_decision.context.get(
                "policy_version"
            ),
            "decision": quality_decision.decision,
            "reason_codes": list(quality_decision.reason_codes),
            "realtime_weight": quality_decision.realtime_weight,
            "training_weight": quality_decision.training_weight,
            "review_required": quality_decision.review_required,
            "context": dict(quality_decision.context),
        }
        prediction = replace(prediction, evidence=evidence)

    candidates = []
    if settings.coin_intelligence_shadow_numeric_v2_enabled:
        candidates.append(
            evaluate_hybrid_v2(prediction, as_of_utc=requested_at)
        )
    if settings.coin_intelligence_shadow_low_date_v2_enabled:
        candidates.append(
            evaluate_low_date_v2(prediction, as_of_utc=requested_at)
        )
    if settings.coin_intelligence_shadow_basis_v2_enabled:
        candidates.append(
            evaluate_basis_v2(prediction, as_of_utc=requested_at)
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
        quality_decision=quality_decision,
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


async def _enqueue_project_market_event(event) -> None:
    component = (
        "project_offer" if str(event.kind).upper() == "OFFER"
        else "project_trade"
    )
    try:
        from core.market_intelligence.job_queue import enqueue_project_job

        await enqueue_project_job(
            kind=str(event.kind),
            local_id=int(event.local_id),
            requested_at_utc=_as_utc(
                getattr(event, "observed_after_commit_at_utc", None)
            ),
        )
        record_shadow_runtime_event(component=component, status="enqueued")
    except Exception:
        record_shadow_runtime_event(
            component=component,
            status="enqueue_error",
        )
        logger.exception(
            "Project market event Shadow enqueue failed",
            extra={
                "event": "coin_intelligence.project_event_enqueue_failed",
                "event_kind": str(getattr(event, "kind", "UNKNOWN"))[:24],
            },
        )


async def process_durable_project_job(job) -> None:
    """Worker handler. Raising leaves retry/lease policy to the queue."""

    kind = str(job.job_kind).upper()
    if kind == "PROJECT_OFFER":
        await _process_project_offer(
            int(job.local_id),
            requested_at=_as_utc(job.requested_at_utc),
        )
        return
    if kind == "PROJECT_TRADE":
        created = await _process_project_trade(int(job.local_id))
        if not created:
            raise RuntimeError("STRICTLY_PRIOR_PREDICTION_NOT_AVAILABLE")
        return
    raise ValueError("UNSUPPORTED_SHADOW_JOB_KIND")


def schedule_project_market_event(event) -> bool:
    """Enqueue a committed project event without retaining raw user data."""

    if not (
        settings.coin_intelligence_shadow_enabled
        and settings.coin_intelligence_shadow_project_events_enabled
        and settings.coin_intelligence_shadow_persist_enabled
        and settings.coin_intelligence_shadow_durable_worker_enabled
        and _configured_service() is not None
    ):
        return False
    component = (
        "project_offer"
        if str(getattr(event, "kind", "")).upper() == "OFFER"
        else "project_trade"
    )
    return _schedule(
        _enqueue_project_market_event(event),
        component=component,
    )
