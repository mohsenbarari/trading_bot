#!/usr/bin/env python3
"""Run the approved real-channel Telegram publisher staging matrix.

Unlike the B2B transport matrix, this program creates real offers through the
bot handler and WebApp router.  Queue-v1's normal feeder, five publisher
workers, Telegram provider calls, expiry worker, and lifecycle edit feeder do
the work.  The runner only observes durable records and writes a redacted,
per-offer timeline to the staging audit volume.

It is deliberately staging-only and fail-closed.  A crash never deletes test
offers or queue evidence: operators can inspect the audit artifact and the
normal queue resumes its durable work after the process exits.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, override_current_server
from core.telegram_delivery_queue_contract import TelegramDeliveryAction, TelegramDeliveryState
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES
from core.trading_settings import get_trading_settings_async
from models.offer import Offer, OfferStatus
from models.offer_publication_state import OfferPublicationState, OfferPublicationSurface
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_publisher_dispatch_command import TelegramPublisherDispatchCommand
from scripts.run_telegram_publisher_b2b_harness import (
    B2BHarnessError,
    _assert_quiet_outbox,
    _new_run_id,
    _require_live_configuration,
)


MATRIX_TOTAL_OFFERS = 1_000
MATRIX_BOT_OFFERS = 600
MATRIX_WEBAPP_OFFERS = 400
MATRIX_USER_INTERACTIONS = 10
MATRIX_INGRESS_INTERVAL_SECONDS = 0.5
MATRIX_OFFER_EXPIRY_MINUTES = 25
MATRIX_DESTINATION_MIN_INTERVAL_SECONDS = 1.05
MATRIX_PROGRESS_POLL_SECONDS = 5.0
MATRIX_PROGRESS_STALL_SECONDS = 180.0
MATRIX_AUDIT_DIRECTORY = Path("/app/audit_trail")

_INITIAL_ACTION = TelegramDeliveryAction.OFFER_PUBLISH.value
_EXPIRY_ACTION = TelegramDeliveryAction.EXPIRED_OFFER_EDIT.value
_SENT_STATE = TelegramDeliveryState.SENT.value
_FINAL_JOB_STATES = frozenset(
    {
        TelegramDeliveryState.SENT.value,
        TelegramDeliveryState.SENT_NOOP.value,
        TelegramDeliveryState.SUPERSEDED.value,
        TelegramDeliveryState.EXPIRED_INTERACTION.value,
        TelegramDeliveryState.PERMANENT_UNDELIVERABLE.value,
        TelegramDeliveryState.TERMINAL_FAILED.value,
        TelegramDeliveryState.QUARANTINED.value,
    }
)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LiveMatrixError(B2BHarnessError):
    """The real staging matrix cannot safely continue."""


@dataclass(frozen=True, slots=True)
class MatrixWorkload:
    origins: tuple[str, ...]
    interaction_origins: tuple[str, ...]
    interaction_offsets_seconds: tuple[float, ...]


@dataclass(slots=True)
class OfferTimeline:
    index: int
    origin: str
    scheduled_at: str
    registration_started_at: str | None = None
    accepted_at: str | None = None
    offer_id: int | None = None
    offer_public_id: str | None = None
    offer_created_at: str | None = None
    offer_home_server: str | None = None
    webapp_visible_at: str | None = None
    webapp_visibility_error: str | None = None
    central_queue_entered_at: str | None = None
    central_queue_sequence: int | None = None
    publisher_lane: str | None = None
    b2b_command_created_at: str | None = None
    b2b_command_sent_at: str | None = None
    worker_acknowledged_at: str | None = None
    post_provider_started_at: str | None = None
    channel_posted_at: str | None = None
    channel_post_state: str | None = None
    expiry_at: str | None = None
    expiry_edit_queue_entered_at: str | None = None
    expiry_edit_provider_started_at: str | None = None
    expiry_edit_posted_at: str | None = None
    expiry_edit_state: str | None = None


@dataclass(slots=True)
class InteractionTimeline:
    index: int
    origin: str
    scheduled_at: str
    started_at: str | None = None
    completed_at: str | None = None
    status: str | None = None


@dataclass(slots=True)
class MatrixRun:
    run_id: str
    started_at: str
    expected_expiry_minutes: int
    timelines: list[OfferTimeline] = field(default_factory=list)
    interactions: list[InteractionTimeline] = field(default_factory=list)
    phase: str = "preflight"
    failure_reason: str | None = None


def build_live_matrix_workload(
    *,
    total_offers: int,
    bot_offers: int,
    webapp_offers: int,
    interaction_count: int,
    ingress_interval_seconds: float,
) -> MatrixWorkload:
    """Build the exact 6:4 source mix without hiding ingress-rate drift."""
    if total_offers != MATRIX_TOTAL_OFFERS:
        raise LiveMatrixError("live_matrix_total_offers_must_equal_1000")
    if bot_offers != MATRIX_BOT_OFFERS or webapp_offers != MATRIX_WEBAPP_OFFERS:
        raise LiveMatrixError("live_matrix_source_mix_must_be_600_bot_400_webapp")
    if interaction_count != MATRIX_USER_INTERACTIONS:
        raise LiveMatrixError("live_matrix_interactions_must_equal_10")
    if not math.isclose(
        float(ingress_interval_seconds),
        MATRIX_INGRESS_INTERVAL_SECONDS,
        abs_tol=0.000_001,
    ):
        raise LiveMatrixError("live_matrix_ingress_must_be_two_per_second")
    # Ten offers per cycle preserves the requested source ratio at every point
    # in the run, rather than only in the final aggregate.
    origins = tuple(("bot",) * 6 + ("webapp",) * 4) * (total_offers // 10)
    if len(origins) != total_offers:
        raise LiveMatrixError("live_matrix_origin_cycle_invalid")
    duration = total_offers * float(ingress_interval_seconds)
    return MatrixWorkload(
        origins=origins,
        interaction_origins=tuple(("bot",) * 6 + ("webapp",) * 4),
        interaction_offsets_seconds=tuple(
            duration * (index + 1) / (interaction_count + 1)
            for index in range(interaction_count)
        ),
    )


def _audit_path(run_id: str) -> Path:
    normalized = str(run_id or "").strip()
    if not normalized.startswith("telegram-live-matrix-"):
        raise LiveMatrixError("live_matrix_run_id_invalid")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in normalized):
        raise LiveMatrixError("live_matrix_run_id_invalid")
    return MATRIX_AUDIT_DIRECTORY / f"{normalized}.json"


def _report_payload(run: MatrixRun) -> dict[str, Any]:
    timelines = [asdict(item) for item in run.timelines]
    interactions = [asdict(item) for item in run.interactions]
    initial_posted = sum(item["channel_post_state"] == _SENT_STATE for item in timelines)
    expiry_edited = sum(item["expiry_edit_state"] == _SENT_STATE for item in timelines)
    expired = sum(item["expiry_at"] is not None for item in timelines)
    queue_entered = sum(item["central_queue_entered_at"] is not None for item in timelines)
    acknowledged = sum(item["worker_acknowledged_at"] is not None for item in timelines)
    lanes = Counter(item["publisher_lane"] for item in timelines if item["publisher_lane"])
    queue_wait_seconds = [
        max(
            0.0,
            (datetime.fromisoformat(item["channel_posted_at"]) - datetime.fromisoformat(item["central_queue_entered_at"])).total_seconds(),
        )
        for item in timelines
        if item["channel_posted_at"] and item["central_queue_entered_at"]
    ]
    passed = (
        run.failure_reason is None
        and len(timelines) == MATRIX_TOTAL_OFFERS
        and queue_entered == MATRIX_TOTAL_OFFERS
        and acknowledged == MATRIX_TOTAL_OFFERS
        and initial_posted == MATRIX_TOTAL_OFFERS
        and expired == MATRIX_TOTAL_OFFERS
        and expiry_edited == MATRIX_TOTAL_OFFERS
        and set(lanes) == set(TELEGRAM_PUBLISHER_IDENTITIES)
        and all(count > 0 for count in lanes.values())
        and len(interactions) == MATRIX_USER_INTERACTIONS
        and all(item["status"] == "success" for item in interactions)
    )
    return {
        "schema_version": 1,
        "run_id": run.run_id,
        "started_at": run.started_at,
        "phase": run.phase,
        "failure_reason": run.failure_reason,
        "configuration": {
            "offer_expiry_minutes": run.expected_expiry_minutes,
            "ingress_interval_seconds": MATRIX_INGRESS_INTERVAL_SECONDS,
            "source_mix": {"bot": MATRIX_BOT_OFFERS, "webapp": MATRIX_WEBAPP_OFFERS},
            "publisher_lanes": list(TELEGRAM_PUBLISHER_IDENTITIES),
            "channel_destination_min_interval_seconds": MATRIX_DESTINATION_MIN_INTERVAL_SECONDS,
        },
        "summary": {
            "offers_created": len(timelines),
            "central_queue_entered": queue_entered,
            "worker_acknowledged": acknowledged,
            "channel_posts_sent": initial_posted,
            "offers_expired": expired,
            "expiry_edits_sent": expiry_edited,
            "publisher_lane_counts": dict(sorted(lanes.items())),
            "webapp_visible": sum(item["webapp_visible_at"] is not None for item in timelines),
            "interaction_successes": sum(item["status"] == "success" for item in interactions),
            "queue_to_channel_seconds": {
                "min": min(queue_wait_seconds, default=None),
                "max": max(queue_wait_seconds, default=None),
            },
        },
        "offer_timelines": timelines,
        "user_interactions": interactions,
        "passed": passed,
    }


def _write_audit(run: MatrixRun) -> Path:
    path = _audit_path(run.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_report_payload(run), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


async def _assert_live_preflight() -> tuple[int, tuple[str, ...]]:
    if str(getattr(settings, "environment", "")).strip().lower() != "staging":
        raise LiveMatrixError("live_matrix_requires_staging_environment")
    if current_server() != SERVER_FOREIGN:
        raise LiveMatrixError("live_matrix_requires_foreign_execution_server")
    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1 or not runtime.queue_worker_enabled:
        raise LiveMatrixError("live_matrix_requires_active_queue_v1")
    if bool(getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)):
        raise LiveMatrixError("live_matrix_requires_editor_lane_disabled")
    destination_interval = float(
        getattr(settings, "telegram_delivery_queue_destination_min_interval_seconds", 0.0)
    )
    if not math.isclose(destination_interval, MATRIX_DESTINATION_MIN_INTERVAL_SECONDS, abs_tol=0.000_001):
        raise LiveMatrixError("live_matrix_channel_limiter_must_be_57_per_minute")
    trading_settings = await get_trading_settings_async()
    if int(getattr(trading_settings, "offer_expiry_minutes", 0) or 0) != MATRIX_OFFER_EXPIRY_MINUTES:
        raise LiveMatrixError("live_matrix_offer_expiry_must_be_25_minutes")
    channel_id, lanes = _require_live_configuration()
    if len(lanes) != len(TELEGRAM_PUBLISHER_IDENTITIES):
        raise LiveMatrixError("live_matrix_requires_all_five_publishers")
    async with AsyncSessionLocal() as db:
        active_offers = int(
            await db.scalar(select(func.count(Offer.id)).where(Offer.status == OfferStatus.ACTIVE))
            or 0
        )
        active_jobs = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.state.notin_(tuple(_FINAL_JOB_STATES))
                )
            )
            or 0
        )
    if active_offers:
        raise LiveMatrixError("live_matrix_active_offers_must_be_empty")
    if active_jobs:
        raise LiveMatrixError("live_matrix_active_delivery_jobs_must_be_empty")
    await _assert_quiet_outbox()
    return channel_id, lanes


async def _load_offer_metadata(offer_id: int) -> tuple[str, str, str]:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise LiveMatrixError("live_matrix_created_offer_missing")
        return (
            str(offer.offer_public_id),
            _iso(offer.created_at) or "",
            str(offer.home_server),
        )


async def _observe_webapp_visibility(timeline: OfferTimeline) -> None:
    """Observe the public WebApp offer projection without mutating it."""
    if not timeline.offer_public_id:
        timeline.webapp_visibility_error = "offer_public_id_missing"
        return
    try:
        from api.routers import offers as offers_router

        with override_current_server(SERVER_IRAN):
            async with AsyncSessionLocal() as db:
                response = await offers_router.get_public_offer_by_public_id(
                    timeline.offer_public_id,
                    db=db,
                )
        if str(getattr(response, "offer_public_id", "")) != timeline.offer_public_id:
            raise LiveMatrixError("webapp_public_projection_identity_mismatch")
        timeline.webapp_visible_at = _iso(_utcnow())
    except Exception as exc:  # record no internal/provider text in the artifact
        timeline.webapp_visibility_error = type(exc).__name__


async def _create_offer(
    *,
    worker: Any,
    harness: Any,
    user: Any,
    commodity_id: int,
    commodity_name: str,
    run_id: str,
    timeline: OfferTimeline,
) -> None:
    timeline.registration_started_at = _iso(_utcnow())
    if timeline.origin == "bot":
        offer_id = await worker.create_bot_offer_with_dispatcher(
            harness=harness,
            owner=user,
            commodity_name=commodity_name,
            prefix=f"{run_id}-{timeline.index:04d}",
            quantity=5,
            price=100000,
            offer_type="sell",
        )
    elif timeline.origin == "webapp":
        with override_current_server(SERVER_IRAN):
            offer_id = await worker.create_offer_for_user(
                user_id=user.user_id,
                commodity_id=commodity_id,
                prefix=f"{run_id}-{timeline.index:04d}",
                index=timeline.index,
                source_surface="webapp",
            )
    else:
        raise LiveMatrixError("live_matrix_origin_invalid")
    timeline.accepted_at = _iso(_utcnow())
    timeline.offer_id = int(offer_id)
    (
        timeline.offer_public_id,
        timeline.offer_created_at,
        timeline.offer_home_server,
    ) = await _load_offer_metadata(int(offer_id))


async def _run_user_interactions(
    *,
    worker: Any,
    harness: Any,
    users: Sequence[Any],
    workload: MatrixWorkload,
    started_monotonic: float,
    started_at: datetime,
    run: MatrixRun,
) -> None:
    for index, (origin, offset) in enumerate(
        zip(workload.interaction_origins, workload.interaction_offsets_seconds), start=1
    ):
        scheduled = started_at.timestamp() + float(offset)
        timeline = InteractionTimeline(
            index=index,
            origin=origin,
            scheduled_at=_iso(datetime.fromtimestamp(scheduled, tz=timezone.utc)) or "",
        )
        run.interactions.append(timeline)
        delay = started_monotonic + float(offset) - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        timeline.started_at = _iso(_utcnow())
        user = users[(index - 1) % len(users)]
        try:
            if origin == "bot":
                result = await worker.execute_bot_market_view_with_dispatcher(
                    harness=harness,
                    user=user,
                )
                if result != "success":
                    raise LiveMatrixError("live_matrix_bot_interaction_failed")
            else:
                with override_current_server(SERVER_IRAN):
                    await worker.list_active_offers_for_user(user_id=user.user_id)
            timeline.status = "success"
        except Exception as exc:
            timeline.status = type(exc).__name__
        finally:
            timeline.completed_at = _iso(_utcnow())


async def _hydrate_timelines(timelines: Iterable[OfferTimeline]) -> None:
    rows = [item for item in timelines if item.offer_id and item.offer_public_id]
    if not rows:
        return
    by_offer_id = {int(item.offer_id): item for item in rows if item.offer_id}
    by_public_id = {str(item.offer_public_id): item for item in rows if item.offer_public_id}
    async with AsyncSessionLocal() as db:
        offers = list(
            (
                await db.execute(select(Offer).where(Offer.id.in_(tuple(by_offer_id))))
            ).scalars()
        )
        states = list(
            (
                await db.execute(
                    select(OfferPublicationState).where(
                        OfferPublicationState.offer_public_id.in_(tuple(by_public_id)),
                        OfferPublicationState.surface == OfferPublicationSurface.TELEGRAM_CHANNEL,
                    )
                )
            ).scalars()
        )
        jobs = list(
            (
                await db.execute(
                    select(TelegramDeliveryJobRecord).where(
                        TelegramDeliveryJobRecord.source_natural_id.in_(tuple(by_public_id)),
                        TelegramDeliveryJobRecord.action_kind.in_((_INITIAL_ACTION, _EXPIRY_ACTION)),
                    )
                )
            ).scalars()
        )
        commands = list(
            (
                await db.execute(
                    select(TelegramPublisherDispatchCommand, TelegramDeliveryJobRecord)
                    .join(
                        TelegramDeliveryJobRecord,
                        TelegramPublisherDispatchCommand.job_id == TelegramDeliveryJobRecord.id,
                    )
                    .where(TelegramDeliveryJobRecord.id.in_(tuple(job.id for job in jobs) or (-1,)))
                )
            ).all()
        )

    for offer in offers:
        timeline = by_offer_id.get(int(offer.id))
        if timeline is not None:
            timeline.offer_created_at = _iso(offer.created_at)
            timeline.offer_home_server = str(offer.home_server)
            timeline.expiry_at = _iso(offer.expired_at)
    for state in states:
        timeline = by_public_id.get(str(state.offer_public_id))
        if timeline is not None and not timeline.publisher_lane:
            timeline.publisher_lane = str(state.publisher_bot_identity or "") or None
    for job in sorted(jobs, key=lambda item: (item.created_at or _utcnow(), int(item.id))):
        timeline = by_public_id.get(str(job.source_natural_id))
        if timeline is None:
            continue
        action = _value(job.action_kind)
        if action == _INITIAL_ACTION:
            timeline.central_queue_entered_at = _iso(job.created_at)
            timeline.central_queue_sequence = int(job.enqueued_seq)
            timeline.publisher_lane = str(job.bot_identity)
            timeline.post_provider_started_at = _iso(job.dispatch_started_at)
            timeline.channel_posted_at = _iso(job.sent_at)
            timeline.channel_post_state = _value(job.state)
        elif action == _EXPIRY_ACTION:
            timeline.expiry_edit_queue_entered_at = _iso(job.created_at)
            timeline.expiry_edit_provider_started_at = _iso(job.dispatch_started_at)
            timeline.expiry_edit_posted_at = _iso(job.sent_at)
            timeline.expiry_edit_state = _value(job.state)
    for command, job in commands:
        if _value(job.action_kind) != _INITIAL_ACTION:
            continue
        timeline = by_public_id.get(str(job.source_natural_id))
        if timeline is not None:
            timeline.b2b_command_created_at = _iso(command.created_at)
            timeline.b2b_command_sent_at = _iso(command.sent_at)
            timeline.worker_acknowledged_at = _iso(command.acknowledged_at)


def _progress_snapshot(run: MatrixRun) -> tuple[int, int, int, int]:
    rows = run.timelines
    return (
        sum(item.central_queue_entered_at is not None for item in rows),
        sum(item.channel_post_state == _SENT_STATE for item in rows),
        sum(item.expiry_at is not None for item in rows),
        sum(item.expiry_edit_state == _SENT_STATE for item in rows),
    )


async def _wait_for_terminal_lifecycle(run: MatrixRun) -> None:
    """Wait without a wall-clock cap; fail only when an expected active phase stalls."""
    last_progress = _progress_snapshot(run)
    last_progress_at = time.monotonic()
    while True:
        await _hydrate_timelines(run.timelines)
        _write_audit(run)
        progress = _progress_snapshot(run)
        if progress != last_progress:
            last_progress = progress
            last_progress_at = time.monotonic()
        queue_count, posted_count, expired_count, edited_count = progress
        if (
            queue_count == MATRIX_TOTAL_OFFERS
            and posted_count == MATRIX_TOTAL_OFFERS
            and expired_count == MATRIX_TOTAL_OFFERS
            and edited_count == MATRIX_TOTAL_OFFERS
        ):
            return

        first_created = min(
            (item.offer_created_at for item in run.timelines if item.offer_created_at),
            default=None,
        )
        expiry_window_open = False
        if first_created:
            expiry_window_open = (
                _utcnow() - datetime.fromisoformat(first_created)
            ).total_seconds() >= MATRIX_OFFER_EXPIRY_MINUTES * 60
        delivery_is_due = posted_count < MATRIX_TOTAL_OFFERS or (
            expiry_window_open and edited_count < MATRIX_TOTAL_OFFERS
        )
        if delivery_is_due and time.monotonic() - last_progress_at >= MATRIX_PROGRESS_STALL_SECONDS:
            raise LiveMatrixError("live_matrix_delivery_progress_stalled")
        await asyncio.sleep(MATRIX_PROGRESS_POLL_SECONDS)


async def run_live_matrix(args: argparse.Namespace) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise LiveMatrixError("live_matrix_live_confirmation_required")
    run_id = args.run_id or _new_run_id().replace("b2b-light-", "telegram-live-matrix-", 1)
    if not run_id.startswith("telegram-live-matrix-"):
        raise LiveMatrixError("live_matrix_run_id_invalid")
    workload = build_live_matrix_workload(
        total_offers=args.total_offers,
        bot_offers=args.bot_offers,
        webapp_offers=args.webapp_offers,
        interaction_count=args.user_interactions,
        ingress_interval_seconds=args.ingress_interval_seconds,
    )
    run = MatrixRun(
        run_id=run_id,
        started_at=_iso(_utcnow()) or "",
        expected_expiry_minutes=MATRIX_OFFER_EXPIRY_MINUTES,
    )
    report_path = _audit_path(run_id)
    main_harness = None
    interaction_harness = None
    interaction_task: asyncio.Task[None] | None = None
    visibility_tasks: list[asyncio.Task[None]] = []
    try:
        _channel_id, _lanes = await _assert_live_preflight()
        run.phase = "preflight_passed"
        _write_audit(run)
        if args.preflight_only:
            payload = _report_payload(run)
            return {"passed": bool(payload["passed"] is False and run.failure_reason is None), "report_path": str(report_path), "run_id": run_id, "preflight_only": True}

        from scripts import trading_core_probe_worker as worker

        users = await worker.create_load_fixture_users(run_id, user_count=MATRIX_TOTAL_OFFERS)
        commodity_id, commodity_name = await worker.resolve_commodity()
        main_harness = worker.AiogramDispatcherHarness()
        interaction_harness = worker.AiogramDispatcherHarness()
        started_monotonic = time.monotonic()
        started_at = _utcnow()
        run.phase = "ingress"
        async with worker.patched_trading_boundaries():
            interaction_task = asyncio.create_task(
                _run_user_interactions(
                    worker=worker,
                    harness=interaction_harness,
                    users=users,
                    workload=workload,
                    started_monotonic=started_monotonic,
                    started_at=started_at,
                    run=run,
                ),
                name="telegram-live-matrix-user-interactions",
            )
            for index, origin in enumerate(workload.origins, start=1):
                scheduled_at = datetime.fromtimestamp(
                    started_at.timestamp() + (index - 1) * MATRIX_INGRESS_INTERVAL_SECONDS,
                    tz=timezone.utc,
                )
                timeline = OfferTimeline(
                    index=index,
                    origin=origin,
                    scheduled_at=_iso(scheduled_at) or "",
                )
                run.timelines.append(timeline)
                await _create_offer(
                    worker=worker,
                    harness=main_harness,
                    user=users[index - 1],
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    run_id=run_id,
                    timeline=timeline,
                )
                visibility_tasks.append(
                    asyncio.create_task(
                        _observe_webapp_visibility(timeline),
                        name=f"telegram-live-matrix-webapp-observe-{index}",
                    )
                )
                due_at = started_monotonic + index * MATRIX_INGRESS_INTERVAL_SECONDS
                delay = due_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                if index % 25 == 0:
                    await _hydrate_timelines(run.timelines)
                    _write_audit(run)
            await asyncio.gather(*visibility_tasks)
            visibility_tasks = []
            if interaction_task is not None:
                await interaction_task
            run.phase = "awaiting_terminal_lifecycle"
            await _wait_for_terminal_lifecycle(run)
        run.phase = "complete"
    except Exception as exc:
        run.phase = "failed"
        run.failure_reason = str(exc)[:160]
    finally:
        if interaction_task is not None and not interaction_task.done():
            interaction_task.cancel()
            await asyncio.gather(interaction_task, return_exceptions=True)
        if visibility_tasks:
            await asyncio.gather(*visibility_tasks, return_exceptions=True)
        if main_harness is not None:
            await main_harness.close()
        if interaction_harness is not None:
            await interaction_harness.close()
        await _hydrate_timelines(run.timelines)
        _write_audit(run)
    payload = _report_payload(run)
    return {
        "passed": bool(payload["passed"]),
        "report_path": str(report_path),
        "run_id": run_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the real Telegram publisher staging matrix.")
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--total-offers", type=int, default=MATRIX_TOTAL_OFFERS)
    parser.add_argument("--bot-offers", type=int, default=MATRIX_BOT_OFFERS)
    parser.add_argument("--webapp-offers", type=int, default=MATRIX_WEBAPP_OFFERS)
    parser.add_argument("--user-interactions", type=int, default=MATRIX_USER_INTERACTIONS)
    parser.add_argument("--ingress-interval-seconds", type=float, default=MATRIX_INGRESS_INTERVAL_SECONDS)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = asyncio.run(run_live_matrix(args))
    except Exception as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] or args.preflight_only else 1


if __name__ == "__main__":
    sys.exit(main())
