#!/usr/bin/env python3
"""Run the approved real-channel Telegram publisher staging matrix.

Unlike the B2B transport matrix, this program creates real offers through the
authoritative bot-confirmation domain path and the WebApp router.  Queue-v1's
normal feeder, five publisher workers, Telegram provider calls, expiry worker,
and lifecycle edit feeder do the work.  The runner only observes durable
records and writes a redacted, per-offer timeline to the staging audit volume.

It is deliberately staging-only and fail-closed.  A crash never deletes test
offers or queue evidence: operators can inspect the audit artifact and the
normal queue resumes its durable work after the process exits.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import and_, func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, override_current_server
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
)
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
from models.user import User
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
# A direct router invocation has no ASGI response boundary: calling
# ``BackgroundTasks`` inline would otherwise make the lifecycle driver wait on
# best-effort work that a real WebApp client receives *after* its successful
# response.  Keep that observation bounded; the matrix later verifies the
# durable terminal outbox and WebApp projection for every offer.
MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS = 15.0
MATRIX_AUDIT_DIRECTORY = Path("/app/audit_trail")

MATRIX_DIRECT_WHOLESALE_TRADES = 100
MATRIX_DIRECT_RETAIL_TRADES = 100
MATRIX_OVERTIME_APPROVED_TRADES = 30
MATRIX_OVERTIME_OWNER_REJECTIONS = 30
MATRIX_OVERTIME_DECISION_TIMEOUTS = 240
MATRIX_MANUAL_EXPIRIES = 100
MATRIX_NATURAL_EXPIRIES = 400
MATRIX_OVERTIME_MINUTES = 5
MATRIX_OVERTIME_RECEIPT_SAFETY_SECONDS = 1.0

_INITIAL_ACTION = TelegramDeliveryAction.OFFER_PUBLISH.value
_EXPIRY_ACTION = TelegramDeliveryAction.EXPIRED_OFFER_EDIT.value
_TRADED_ACTION = TelegramDeliveryAction.TRADED_OFFER_EDIT.value
_TERMINAL_ACTIONS = frozenset({_TRADED_ACTION, _EXPIRY_ACTION})
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

_EXPECTED_TERMINAL_STATUS_BY_SCENARIO = {
    "direct_wholesale_trade": OfferStatus.COMPLETED.value,
    "direct_retail_lot_trade": OfferStatus.COMPLETED.value,
    "overtime_approved_trade": OfferStatus.COMPLETED.value,
    "overtime_owner_rejected": OfferStatus.EXPIRED.value,
    "overtime_decision_timeout": OfferStatus.EXPIRED.value,
    "manual_expiry": OfferStatus.EXPIRED.value,
    "natural_expiry": OfferStatus.EXPIRED.value,
}
_OVERTIME_SCENARIOS = frozenset(
    {
        "overtime_approved_trade",
        "overtime_owner_rejected",
        "overtime_decision_timeout",
    }
)
_DIRECT_TRADE_SCENARIOS = frozenset(
    {"direct_wholesale_trade", "direct_retail_lot_trade"}
)


def _is_ignorable_historical_private_job(job: Any) -> bool:
    """Narrow staging-only exception for unclaimable legacy probe artifacts."""
    return (
        _value(getattr(job, "state", None))
        == TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value
        and _value(getattr(job, "action_kind", None))
        == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value
        and _value(getattr(job, "destination_class", None))
        == TelegramDestinationClass.PRIVATE.value
        and str(getattr(job, "destination_key", "")).startswith("private:user:")
    )


def _retail_lot_sizes(lot_min_size: Any) -> tuple[int, int, int]:
    """Build three valid, equal lots from the active market minimum."""
    try:
        minimum = int(lot_min_size)
    except (TypeError, ValueError, OverflowError):
        minimum = 1
    size = max(1, minimum)
    return (size, size, size)


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
    scenarios: tuple[str, ...]
    interaction_origins: tuple[str, ...]
    interaction_offsets_seconds: tuple[float, ...]


@dataclass(slots=True)
class OfferTimeline:
    index: int
    origin: str
    scenario: str
    expected_terminal_status: str
    scheduled_at: str
    registration_started_at: str | None = None
    accepted_at: str | None = None
    offer_id: int | None = None
    offer_public_id: str | None = None
    offer_created_at: str | None = None
    offer_home_server: str | None = None
    overtime_minutes_snapshot: int | None = None
    normal_deadline_at: str | None = None
    final_deadline_at: str | None = None
    webapp_visible_at: str | None = None
    webapp_status_at_visibility: str | None = None
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
    offer_status: str | None = None
    terminal_at: str | None = None
    terminal_edit_queue_entered_at: str | None = None
    terminal_edit_provider_started_at: str | None = None
    terminal_edit_posted_at: str | None = None
    terminal_edit_state: str | None = None
    webapp_terminal_visible_at: str | None = None
    webapp_terminal_status: str | None = None
    webapp_terminal_error: str | None = None


@dataclass(slots=True)
class LifecycleActionTimeline:
    offer_index: int
    action: str
    origin: str
    scheduled_at: str
    started_at: str | None = None
    completed_at: str | None = None
    status: str | None = None
    failure_class: str | None = None


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
    lifecycle_actions: list[LifecycleActionTimeline] = field(default_factory=list)
    ignored_historical_private_job_count: int = 0
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
    scenario_counts = (
        ("direct_wholesale_trade", MATRIX_DIRECT_WHOLESALE_TRADES),
        ("direct_retail_lot_trade", MATRIX_DIRECT_RETAIL_TRADES),
        ("overtime_approved_trade", MATRIX_OVERTIME_APPROVED_TRADES),
        ("overtime_owner_rejected", MATRIX_OVERTIME_OWNER_REJECTIONS),
        ("overtime_decision_timeout", MATRIX_OVERTIME_DECISION_TIMEOUTS),
        ("manual_expiry", MATRIX_MANUAL_EXPIRIES),
        ("natural_expiry", MATRIX_NATURAL_EXPIRIES),
    )
    if sum(count for _name, count in scenario_counts) != total_offers:
        raise LiveMatrixError("live_matrix_lifecycle_total_invalid")
    origin_cycle = ("bot",) * 6 + ("webapp",) * 4
    origins: list[str] = []
    scenarios: list[str] = []
    for scenario, count in scenario_counts:
        if count % len(origin_cycle):
            raise LiveMatrixError("live_matrix_lifecycle_source_ratio_invalid")
        origins.extend(origin_cycle * (count // len(origin_cycle)))
        scenarios.extend((scenario,) * count)
    if len(origins) != total_offers or len(scenarios) != total_offers:
        raise LiveMatrixError("live_matrix_origin_cycle_invalid")
    duration = total_offers * float(ingress_interval_seconds)
    return MatrixWorkload(
        origins=tuple(origins),
        scenarios=tuple(scenarios),
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
    lifecycle_actions = [asdict(item) for item in run.lifecycle_actions]
    initial_posted = sum(item["channel_post_state"] == _SENT_STATE for item in timelines)
    expiry_edited = sum(item["expiry_edit_state"] == _SENT_STATE for item in timelines)
    terminal_edited = sum(item["terminal_edit_state"] == _SENT_STATE for item in timelines)
    expired = sum(item["expiry_at"] is not None for item in timelines)
    queue_entered = sum(item["central_queue_entered_at"] is not None for item in timelines)
    acknowledged = sum(item["worker_acknowledged_at"] is not None for item in timelines)
    lanes = Counter(item["publisher_lane"] for item in timelines if item["publisher_lane"])
    scenarios = Counter(item["scenario"] for item in timelines)
    terminal_statuses = Counter(item["offer_status"] for item in timelines if item["offer_status"])
    lifecycle_statuses = Counter(
        (item["action"], item["status"])
        for item in lifecycle_actions
        if item["status"]
    )
    queue_wait_seconds = [
        max(
            0.0,
            (datetime.fromisoformat(item["channel_posted_at"]) - datetime.fromisoformat(item["central_queue_entered_at"])).total_seconds(),
        )
        for item in timelines
        if item["channel_posted_at"] and item["central_queue_entered_at"]
    ]
    terminal_after_initial_publication = all(
        _timeline_terminal_follows_initial_publication(item)
        for item in run.timelines
    )
    passed = (
        run.failure_reason is None
        and len(timelines) == MATRIX_TOTAL_OFFERS
        and queue_entered == MATRIX_TOTAL_OFFERS
        and acknowledged == MATRIX_TOTAL_OFFERS
        and initial_posted == MATRIX_TOTAL_OFFERS
        and terminal_edited == MATRIX_TOTAL_OFFERS
        and terminal_after_initial_publication
        and all(
            item["offer_status"] == item["expected_terminal_status"]
            for item in timelines
        )
        and all(item["webapp_terminal_status"] == item["expected_terminal_status"] for item in timelines)
        and set(lanes) == set(TELEGRAM_PUBLISHER_IDENTITIES)
        and all(count > 0 for count in lanes.values())
        and len(interactions) == MATRIX_USER_INTERACTIONS
        and all(item["status"] == "success" for item in interactions)
        and all(item["status"] == "success" for item in lifecycle_actions)
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
            "lifecycle_mix": {
                "direct_wholesale_trades": MATRIX_DIRECT_WHOLESALE_TRADES,
                "direct_retail_lot_trades": MATRIX_DIRECT_RETAIL_TRADES,
                "overtime_approved_trades": MATRIX_OVERTIME_APPROVED_TRADES,
                "overtime_owner_rejections": MATRIX_OVERTIME_OWNER_REJECTIONS,
                "overtime_decision_timeouts": MATRIX_OVERTIME_DECISION_TIMEOUTS,
                "manual_expiries": MATRIX_MANUAL_EXPIRIES,
                "natural_expiries": MATRIX_NATURAL_EXPIRIES,
                "overtime_minutes": MATRIX_OVERTIME_MINUTES,
            },
            "publisher_lanes": list(TELEGRAM_PUBLISHER_IDENTITIES),
            "channel_destination_min_interval_seconds": MATRIX_DESTINATION_MIN_INTERVAL_SECONDS,
            "ignored_historical_private_job_count": run.ignored_historical_private_job_count,
        },
        "summary": {
            "offers_created": len(timelines),
            "central_queue_entered": queue_entered,
            "worker_acknowledged": acknowledged,
            "channel_posts_sent": initial_posted,
            "offers_expired": expired,
            "expiry_edits_sent": expiry_edited,
            "terminal_channel_edits_sent": terminal_edited,
            "terminal_events_after_initial_publication": terminal_after_initial_publication,
            "scenarios": dict(sorted(scenarios.items())),
            "terminal_offer_statuses": dict(sorted(terminal_statuses.items())),
            "lifecycle_action_statuses": {
                f"{action}:{status}": count
                for (action, status), count in sorted(lifecycle_statuses.items())
            },
            "publisher_lane_counts": dict(sorted(lanes.items())),
            "webapp_visible": sum(item["webapp_visible_at"] is not None for item in timelines),
            "webapp_terminal_visible": sum(
                item["webapp_terminal_visible_at"] is not None for item in timelines
            ),
            "interaction_successes": sum(item["status"] == "success" for item in interactions),
            "queue_to_channel_seconds": {
                "min": min(queue_wait_seconds, default=None),
                "max": max(queue_wait_seconds, default=None),
            },
        },
        "offer_timelines": timelines,
        "user_interactions": interactions,
        "lifecycle_actions": lifecycle_actions,
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


async def _assert_live_preflight() -> tuple[int, tuple[str, ...], int]:
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
        legacy_private_probe_job = and_(
            TelegramDeliveryJobRecord.state
            == TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
            TelegramDeliveryJobRecord.action_kind
            == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
            TelegramDeliveryJobRecord.destination_class
            == TelegramDestinationClass.PRIVATE,
            TelegramDeliveryJobRecord.destination_key.like("private:user:%"),
        )
        active_jobs = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.state.notin_(tuple(_FINAL_JOB_STATES)),
                    ~legacy_private_probe_job,
                )
            )
            or 0
        )
        ignored_historical_private_job_count = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    legacy_private_probe_job
                )
            )
            or 0
        )
    if active_offers:
        raise LiveMatrixError("live_matrix_active_offers_must_be_empty")
    if active_jobs:
        raise LiveMatrixError("live_matrix_active_delivery_jobs_must_be_empty")
    await _assert_quiet_outbox()
    return channel_id, lanes, ignored_historical_private_job_count


async def _load_offer_metadata(offer_id: int) -> tuple[str, str, str, int]:
    async with AsyncSessionLocal() as db:
        offer = await db.get(Offer, offer_id)
        if offer is None:
            raise LiveMatrixError("live_matrix_created_offer_missing")
        return (
            str(offer.offer_public_id),
            _iso(offer.created_at) or "",
            str(offer.home_server),
            int(getattr(offer, "overtime_minutes_snapshot", 0) or 0),
        )


async def _observe_webapp_visibility(
    timeline: OfferTimeline,
    *,
    terminal: bool = False,
) -> None:
    """Observe the public WebApp offer projection without mutating it."""
    if not timeline.offer_public_id:
        if terminal:
            timeline.webapp_terminal_error = "offer_public_id_missing"
        else:
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
        observed_at = _iso(_utcnow())
        observed_status = _value(getattr(response, "status", None))
        if terminal:
            timeline.webapp_terminal_visible_at = observed_at
            timeline.webapp_terminal_status = observed_status
        else:
            timeline.webapp_visible_at = observed_at
            timeline.webapp_status_at_visibility = observed_status
    except Exception as exc:  # record no internal/provider text in the artifact
        if terminal:
            timeline.webapp_terminal_error = type(exc).__name__
        else:
            timeline.webapp_visibility_error = type(exc).__name__


async def _create_offer(
    *,
    worker: Any,
    user: Any,
    commodity_id: int,
    commodity_name: str,
    run_id: str,
    timeline: OfferTimeline,
) -> None:
    timeline.registration_started_at = _iso(_utcnow())
    trading_settings = await get_trading_settings_async()
    is_retail = timeline.scenario == "direct_retail_lot_trade"
    retail_lots = _retail_lot_sizes(
        getattr(trading_settings, "lot_min_size", None)
    )
    quantity = sum(retail_lots) if is_retail else 5
    if timeline.origin == "bot":
        # The in-container Aiogram recording harness is intentionally unable
        # to emulate the production root router ordering.  Exercise the same
        # atomic persistence half of the real confirmation handler instead:
        # its parser was already validated by the bot interaction probe below,
        # and this transaction is the durable offer/publication obligation.
        from bot.utils.offer_parser import parse_offer_text
        from core.offer_source import OfferSourceSurface
        from core.services.offer_creation_service import (
            OfferCreationCommand,
            OfferCreationQuotaPolicy,
            create_authoritative_offer_with_outcome,
        )
        from core.services.telegram_offer_publication_service import (
            get_or_create_telegram_publication_state,
            initial_telegram_publication_publisher_identity,
        )
        from models.offer import OfferStatus, OfferType

        text_value, _marker = worker.build_bot_offer_text(
            owner_user_id=user.user_id,
            commodity_name=commodity_name,
            prefix=f"{run_id}-{timeline.index:04d}",
            quantity=quantity,
            price=100000,
            offer_type="sell",
            is_wholesale=not is_retail,
            lot_sizes=retail_lots if is_retail else None,
        )
        parsed, parse_error = await parse_offer_text(text_value)
        if parsed is None or parse_error is not None:
            raise LiveMatrixError("live_matrix_bot_offer_parse_failed")
        async with AsyncSessionLocal() as db:
            outcome = await create_authoritative_offer_with_outcome(
                db,
                OfferCreationCommand(
                    source_surface=OfferSourceSurface.TELEGRAM_BOT,
                    owner_user_id=user.user_id,
                    actor_user_id=user.user_id,
                    offer_type=(
                        OfferType.BUY if parsed.trade_type == "buy" else OfferType.SELL
                    ),
                    settlement_type=parsed.settlement_type,
                    commodity_id=parsed.commodity_id,
                    quantity=parsed.quantity,
                    price=parsed.price,
                    is_wholesale=parsed.is_wholesale,
                    lot_sizes=parsed.lot_sizes,
                    original_lot_sizes=parsed.lot_sizes,
                    notes=parsed.notes,
                    status=OfferStatus.ACTIVE,
                ),
                commit=False,
                refresh=False,
                validate_market=True,
                enforce_market_admission=True,
                quota_policy=OfferCreationQuotaPolicy(
                    max_active_offers=int(trading_settings.max_active_offers),
                ),
            )
            await db.flush()
            await get_or_create_telegram_publication_state(
                db,
                outcome.offer,
                publisher_bot_identity=initial_telegram_publication_publisher_identity(
                    multi_publisher_enabled=bool(
                        getattr(settings, "telegram_multi_publisher_enabled", False)
                    ),
                    b2b_dispatch_enabled=bool(
                        getattr(settings, "telegram_b2b_dispatch_enabled", False)
                    ),
                ),
            )
            await db.commit()
            offer_id = int(outcome.offer.id)
    elif timeline.origin == "webapp":
        with override_current_server(SERVER_IRAN):
            offer_id = await worker.create_offer_for_user(
                user_id=user.user_id,
                commodity_id=commodity_id,
                prefix=f"{run_id}-{timeline.index:04d}",
                index=timeline.index,
                source_surface="webapp",
                quantity=quantity,
                is_wholesale=timeline.scenario != "direct_retail_lot_trade",
                lot_sizes=retail_lots
                if timeline.scenario == "direct_retail_lot_trade"
                else None,
            )
    else:
        raise LiveMatrixError("live_matrix_origin_invalid")
    timeline.accepted_at = _iso(_utcnow())
    timeline.offer_id = int(offer_id)
    (
        timeline.offer_public_id,
        timeline.offer_created_at,
        timeline.offer_home_server,
        timeline.overtime_minutes_snapshot,
    ) = await _load_offer_metadata(int(offer_id))
    expected_overtime = MATRIX_OVERTIME_MINUTES if timeline.scenario in _OVERTIME_SCENARIOS else 0
    if timeline.overtime_minutes_snapshot != expected_overtime:
        raise LiveMatrixError("live_matrix_overtime_snapshot_mismatch")


async def _run_user_interactions(
    *,
    worker: Any,
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
                _commodity_id, commodity_name = await worker.resolve_commodity()
                text_value, _marker = worker.build_bot_offer_text(
                    owner_user_id=user.user_id,
                    commodity_name=commodity_name,
                    prefix=f"{run.run_id}-interaction-{index}",
                    quantity=5,
                    price=100000,
                    offer_type="sell",
                )
                probe = await worker.run_bot_text_handler_probe(
                    user_id=user.user_id,
                    text_value=text_value,
                )
                if not bool(probe.get("state_set")):
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
                        TelegramDeliveryJobRecord.action_kind.in_(
                            (_INITIAL_ACTION, *_TERMINAL_ACTIONS)
                        ),
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
            from core.offer_lifecycle import compute_lifecycle_deadlines

            timeline.offer_created_at = _iso(offer.created_at)
            timeline.offer_home_server = str(offer.home_server)
            timeline.expiry_at = _iso(offer.expired_at)
            timeline.offer_status = _value(offer.status)
            timeline.overtime_minutes_snapshot = int(
                getattr(offer, "overtime_minutes_snapshot", 0) or 0
            )
            normal_deadline, final_deadline = compute_lifecycle_deadlines(
                offer.created_at,
                normal_lifetime_minutes=MATRIX_OFFER_EXPIRY_MINUTES,
                overtime_minutes_snapshot=timeline.overtime_minutes_snapshot,
            )
            timeline.normal_deadline_at = _iso(normal_deadline)
            timeline.final_deadline_at = _iso(final_deadline)
            if timeline.offer_status == OfferStatus.EXPIRED.value:
                timeline.terminal_at = _iso(offer.expired_at) or _iso(offer.updated_at)
            elif timeline.offer_status == OfferStatus.COMPLETED.value:
                timeline.terminal_at = _iso(offer.updated_at)
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
            timeline.terminal_edit_queue_entered_at = _iso(job.created_at)
            timeline.terminal_edit_provider_started_at = _iso(job.dispatch_started_at)
            timeline.terminal_edit_posted_at = _iso(job.sent_at)
            timeline.terminal_edit_state = _value(job.state)
        elif action == _TRADED_ACTION:
            timeline.terminal_edit_queue_entered_at = _iso(job.created_at)
            timeline.terminal_edit_provider_started_at = _iso(job.dispatch_started_at)
            timeline.terminal_edit_posted_at = _iso(job.sent_at)
            timeline.terminal_edit_state = _value(job.state)
    for command, job in commands:
        if _value(job.action_kind) != _INITIAL_ACTION:
            continue
        timeline = by_public_id.get(str(job.source_natural_id))
        if timeline is not None:
            timeline.b2b_command_created_at = _iso(command.created_at)
            timeline.b2b_command_sent_at = _iso(command.sent_at)
            timeline.worker_acknowledged_at = _iso(command.acknowledged_at)


def _append_lifecycle_action(
    run: MatrixRun,
    *,
    timeline: OfferTimeline,
    action: str,
    origin: str,
    scheduled_at: datetime,
) -> LifecycleActionTimeline:
    entry = LifecycleActionTimeline(
        offer_index=timeline.index,
        action=action,
        origin=origin,
        scheduled_at=_iso(scheduled_at) or "",
    )
    run.lifecycle_actions.append(entry)
    return entry


async def _complete_lifecycle_action(
    entry: LifecycleActionTimeline,
    operation: Any,
) -> None:
    entry.started_at = _iso(_utcnow())
    try:
        outcome = await operation()
        entry.status = "success" if outcome in (None, "success") else str(outcome)
    except Exception as exc:
        entry.status = type(exc).__name__
        entry.failure_class = type(exc).__name__
    finally:
        entry.completed_at = _iso(_utcnow())
    if entry.status != "success" and entry.failure_class is None:
        entry.failure_class = f"operation_returned_{entry.status or 'unknown'}"
    if entry.status != "success":
        raise LiveMatrixError(f"live_matrix_lifecycle_action_failed:{entry.action}")


async def _configure_overtime_preferences(
    *,
    users: Sequence[Any],
    workload: MatrixWorkload,
) -> None:
    """Use the Iran-authoritative preference service before offer registration."""
    overtime_owner_indexes = [
        index
        for index, scenario in enumerate(workload.scenarios)
        if scenario in _OVERTIME_SCENARIOS
    ]
    if len(overtime_owner_indexes) != (
        MATRIX_OVERTIME_APPROVED_TRADES
        + MATRIX_OVERTIME_OWNER_REJECTIONS
        + MATRIX_OVERTIME_DECISION_TIMEOUTS
    ):
        raise LiveMatrixError("live_matrix_overtime_owner_count_invalid")
    from core.services.offer_overtime_preference_service import persist_overtime_preference

    with override_current_server(SERVER_IRAN):
        async with AsyncSessionLocal() as db:
            for index in overtime_owner_indexes:
                owner = await db.get(User, int(users[index].user_id))
                if owner is None:
                    raise LiveMatrixError("live_matrix_overtime_owner_missing")
                await persist_overtime_preference(db, owner, MATRIX_OVERTIME_MINUTES)
            await db.commit()


async def _wait_until(target: datetime) -> None:
    delay = (target - _utcnow()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


def _taker_for_timeline(users: Sequence[Any], timeline: OfferTimeline) -> Any:
    taker = users[(timeline.index - 1 + MATRIX_TOTAL_OFFERS // 2) % len(users)]
    if int(taker.user_id) == int(users[timeline.index - 1].user_id):
        raise LiveMatrixError("live_matrix_taker_must_differ_from_owner")
    return taker


async def _run_direct_trade(
    *,
    worker: Any,
    harness: Any,
    users: Sequence[Any],
    run: MatrixRun,
    timeline: OfferTimeline,
) -> None:
    taker = _taker_for_timeline(users, timeline)
    trade_count = 1 if timeline.scenario == "direct_wholesale_trade" else 3
    action_name = (
        "direct_wholesale_trade"
        if timeline.scenario == "direct_wholesale_trade"
        else "retail_lot_trade"
    )
    for lot_index in range(1, trade_count + 1):
        entry = _append_lifecycle_action(
            run,
            timeline=timeline,
            action=action_name,
            origin=timeline.origin,
            scheduled_at=_utcnow(),
        )

        async def execute() -> str:
            error_details: list[str] = []
            offer = await worker.load_offer_snapshot(int(timeline.offer_id))
            if timeline.scenario == "direct_retail_lot_trade":
                lot_sizes = list(getattr(offer, "lot_sizes", None) or ())
                if not lot_sizes:
                    raise LiveMatrixError("live_matrix_retail_lot_missing")
                amount = int(lot_sizes[0])
            else:
                amount = 5
            if timeline.origin == "bot":
                publisher_identity = str(timeline.publisher_lane or "").strip()
                if publisher_identity not in TELEGRAM_PUBLISHER_IDENTITIES:
                    raise LiveMatrixError("live_matrix_publisher_callback_identity_missing")
                outcome = await worker.execute_bot_trade_with_dispatcher(
                    harness=harness,
                    spec=worker.MixedLoadAttemptSpec(
                        index=timeline.index * 10 + lot_index,
                        surface="telegram",
                        user_id=int(taker.user_id),
                        telegram_id=int(taker.telegram_id),
                    ),
                    offer=offer,
                    amount=amount,
                    prefix=f"{run.run_id}-direct-{timeline.index:04d}-{lot_index}",
                    error_details=error_details,
                    callback_bot_identity=publisher_identity,
                )
            else:
                with override_current_server(timeline.offer_home_server or SERVER_IRAN):
                    outcome = await worker.execute_webapp_trade_for_user(
                        user_id=int(taker.user_id),
                        offer_id=int(timeline.offer_id),
                        offer_public_id=timeline.offer_public_id,
                        quantity=amount,
                        idempotency_key=(
                            f"{run.run_id}-direct-{timeline.index:04d}-{lot_index}"
                        ),
                        error_details=error_details,
                    )
            if outcome != "success" and error_details:
                entry.failure_class = error_details[0].partition(":")[0].strip() or None
            return outcome

        await _complete_lifecycle_action(entry, execute)


async def _run_manual_expiry(
    *,
    worker: Any,
    harness: Any,
    users: Sequence[Any],
    run: MatrixRun,
    timeline: OfferTimeline,
) -> None:
    owner = users[timeline.index - 1]
    entry = _append_lifecycle_action(
        run,
        timeline=timeline,
        action="manual_expiry",
        origin=timeline.origin,
        scheduled_at=_utcnow(),
    )

    async def execute() -> str | None:
        error_details: list[str] = []
        if timeline.origin == "bot":
            outcome = await worker.expire_bot_offer_with_dispatcher(
                harness=harness,
                owner=owner,
                offer_id=int(timeline.offer_id),
                prefix=run.run_id,
                index=timeline.index,
                error_details=error_details,
            )
        else:
            with override_current_server(timeline.offer_home_server or SERVER_IRAN):
                await worker.expire_offer_for_user(
                    user_id=int(owner.user_id),
                    offer_id=int(timeline.offer_id),
                )
            outcome = "success"
        if outcome != "success" and error_details:
            entry.failure_class = error_details[0].partition(":")[0].strip() or None
        return outcome

    await _complete_lifecycle_action(entry, execute)


async def _load_overtime_request_public_id(
    *,
    offer_id: int,
    idempotency_key: str,
) -> str:
    from models.offer_request import OfferRequest, OfferRequestWorkflow

    async with AsyncSessionLocal() as db:
        request = (
            await db.execute(
                select(OfferRequest).where(
                    OfferRequest.local_offer_id == int(offer_id),
                    OfferRequest.idempotency_key == idempotency_key,
                    OfferRequest.workflow_kind == OfferRequestWorkflow.OVERTIME,
                )
            )
        ).scalar_one_or_none()
    if request is None or not getattr(request, "request_public_id", None):
        raise LiveMatrixError("live_matrix_overtime_request_missing")
    return str(request.request_public_id)


async def _decide_overtime_request_via_webapp(
    *,
    worker: Any,
    owner: Any,
    request_public_id: str,
    approve: bool,
    home_server: str,
) -> None:
    from fastapi import BackgroundTasks
    from api.routers import trades as trades_router

    with override_current_server(home_server):
        async with AsyncSessionLocal() as db:
            owner_user = await worker.load_user(db, int(owner.user_id))
            context = worker.owner_context(owner_user)
            if approve:
                background_tasks = BackgroundTasks()
                response = await trades_router.approve_overtime_request(
                    request_public_id,
                    background_tasks=background_tasks,
                    db=db,
                    context=context,
                )
                if getattr(response, "status_code", 201) >= 400:
                    raise LiveMatrixError("live_matrix_overtime_approve_rejected")
                await _run_post_response_background_tasks(background_tasks)
            else:
                response = await trades_router.reject_overtime_request(
                    request_public_id,
                    db=db,
                    context=context,
                )
                if getattr(response, "status_code", 200) >= 400:
                    raise LiveMatrixError("live_matrix_overtime_reject_failed")


async def _run_post_response_background_tasks(background_tasks: Any) -> bool:
    """Bound harness-only post-response work without weakening final checks.

    FastAPI runs these tasks after the HTTP response, whereas this matrix calls
    a router directly.  An individual best-effort task must not hold a committed
    overtime approval hostage.  The terminal phase still requires the durable
    channel edit and WebApp projection, so a missing side effect remains a test
    failure rather than being silently accepted here.
    """
    try:
        await asyncio.wait_for(
            background_tasks(),
            timeout=MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS,
        )
    except TimeoutError:
        return False
    return True


async def _run_overtime_lifecycle(
    *,
    worker: Any,
    users: Sequence[Any],
    run: MatrixRun,
    timeline: OfferTimeline,
) -> None:
    if not timeline.normal_deadline_at:
        raise LiveMatrixError("live_matrix_overtime_normal_deadline_missing")
    scheduled = datetime.fromisoformat(timeline.normal_deadline_at) + timedelta(
        seconds=MATRIX_OVERTIME_RECEIPT_SAFETY_SECONDS
    )
    request_entry = _append_lifecycle_action(
        run,
        timeline=timeline,
        action="overtime_request",
        # A WebApp request keeps the owner-decision state local and observable;
        # the published offer's ingress surface remains in ``timeline.origin``.
        origin="webapp",
        scheduled_at=scheduled,
    )
    await _wait_until(scheduled)
    await _assert_timeline_initial_publication(timeline)
    requester = _taker_for_timeline(users, timeline)
    idempotency_key = f"{run.run_id}-overtime-{timeline.index:04d}"

    async def request_overtime() -> str:
        with override_current_server(timeline.offer_home_server or SERVER_IRAN):
            return await worker.execute_webapp_trade_for_user(
                user_id=int(requester.user_id),
                offer_id=int(timeline.offer_id),
                offer_public_id=timeline.offer_public_id,
                quantity=5,
                idempotency_key=idempotency_key,
            )

    await _complete_lifecycle_action(request_entry, request_overtime)
    if timeline.scenario == "overtime_decision_timeout":
        return

    request_public_id = await _load_overtime_request_public_id(
        offer_id=int(timeline.offer_id),
        idempotency_key=idempotency_key,
    )
    decision = "approve" if timeline.scenario == "overtime_approved_trade" else "reject"
    decision_entry = _append_lifecycle_action(
        run,
        timeline=timeline,
        action=f"overtime_owner_{decision}",
        origin="webapp",
        scheduled_at=_utcnow(),
    )
    owner = users[timeline.index - 1]
    await _complete_lifecycle_action(
        decision_entry,
        lambda: _decide_overtime_request_via_webapp(
            worker=worker,
            owner=owner,
            request_public_id=request_public_id,
            approve=decision == "approve",
            home_server=timeline.offer_home_server or SERVER_IRAN,
        ),
    )


async def _monitor_lifecycle_progress(
    *,
    run: MatrixRun,
    stop: asyncio.Event,
) -> None:
    """Checkpoint lifecycle actions without competing with queue workers.

    The durable queue records already hold every timestamp needed for the
    final per-offer audit.  Hydrating one thousand offers, jobs, publication
    states, and B2B commands every few seconds adds avoidable read pressure to
    the same staging database being measured.  Keep the in-memory action
    checkpoint current here and perform a full hydration at a phase boundary.
    """
    while not stop.is_set():
        _write_audit(run)
        try:
            await asyncio.wait_for(stop.wait(), timeout=MATRIX_PROGRESS_POLL_SECONDS)
        except TimeoutError:
            continue


async def _run_lifecycle_actions(
    *,
    worker: Any,
    users: Sequence[Any],
    run: MatrixRun,
) -> None:
    """Drive authoritative paths after workers receive the full matrix.

    Channel publication is deliberately slower than central ingress.  Waiting
    for the thousandth channel post before acting on the first one would make
    an early offer consume most of its real 25-minute lifetime off-screen.  A
    direct/manual mutation therefore waits for its own post; final audit still
    proves every terminal event followed that offer's initial channel post.
    """
    harness = worker.AiogramDispatcherHarness()
    overtime_tasks: list[asyncio.Task[None]] = []
    monitor_stop = asyncio.Event()
    monitor_task = asyncio.create_task(
        _monitor_lifecycle_progress(run=run, stop=monitor_stop),
        name="telegram-live-matrix-lifecycle-monitor",
    )
    try:
        manual_timelines: list[OfferTimeline] = []
        for timeline in run.timelines:
            if timeline.scenario in _OVERTIME_SCENARIOS:
                overtime_tasks.append(
                    asyncio.create_task(
                        _run_overtime_lifecycle(
                            worker=worker,
                            users=users,
                            run=run,
                            timeline=timeline,
                        ),
                        name=f"telegram-live-matrix-overtime-{timeline.index}",
                    )
                )
            elif timeline.scenario in _DIRECT_TRADE_SCENARIOS:
                await _run_direct_trade(
                    worker=worker,
                    harness=harness,
                    users=users,
                    run=run,
                    timeline=timeline,
                )
            elif timeline.scenario == "manual_expiry":
                manual_timelines.append(timeline)
            elif timeline.scenario != "natural_expiry":
                raise LiveMatrixError("live_matrix_scenario_invalid")
        if manual_timelines:
            await _wait_for_initial_publication(run, timelines=manual_timelines)
            for timeline in manual_timelines:
                await _run_manual_expiry(
                    worker=worker,
                    harness=harness,
                    users=users,
                    run=run,
                    timeline=timeline,
                )
        await asyncio.gather(*overtime_tasks)
    finally:
        if any(not task.done() for task in overtime_tasks):
            for task in overtime_tasks:
                task.cancel()
            await asyncio.gather(*overtime_tasks, return_exceptions=True)
        monitor_stop.set()
        await monitor_task
        await harness.close()


def _initial_publication_complete(
    *,
    posted_count: int,
    expired_count: int,
    expected_count: int = MATRIX_TOTAL_OFFERS,
) -> bool:
    """Validate a publication gate before the selected lifecycle cohort."""
    if int(expired_count) > 0:
        raise LiveMatrixError("live_matrix_offer_expired_before_initial_publication")
    return int(posted_count) == int(expected_count)


async def _initial_publication_progress(
    timelines: Iterable[OfferTimeline],
) -> tuple[int, int]:
    """Return lightweight durable counts while the initial channel backlog drains."""
    public_ids = tuple(
        str(item.offer_public_id)
        for item in timelines
        if item.offer_public_id
    )
    offer_ids = tuple(int(item.offer_id) for item in timelines if item.offer_id)
    if not public_ids or not offer_ids:
        return 0, 0
    async with AsyncSessionLocal() as db:
        posted_count = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.source_natural_id.in_(public_ids),
                    TelegramDeliveryJobRecord.action_kind == _INITIAL_ACTION,
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.SENT,
                )
            )
            or 0
        )
        expired_count = int(
            await db.scalar(
                select(func.count(Offer.id)).where(
                    Offer.id.in_(offer_ids),
                    Offer.status == OfferStatus.EXPIRED,
                )
            )
            or 0
        )
    return posted_count, expired_count


async def _worker_acknowledgement_progress(
    timelines: Iterable[OfferTimeline],
) -> int:
    """Count worker receipts without hydrating the thousand-offer audit."""
    public_ids = tuple(
        str(item.offer_public_id)
        for item in timelines
        if item.offer_public_id
    )
    if not public_ids:
        return 0
    async with AsyncSessionLocal() as db:
        return int(
            await db.scalar(
                select(func.count(TelegramPublisherDispatchCommand.id))
                .join(
                    TelegramDeliveryJobRecord,
                    TelegramPublisherDispatchCommand.job_id
                    == TelegramDeliveryJobRecord.id,
                )
                .where(
                    TelegramDeliveryJobRecord.source_natural_id.in_(public_ids),
                    TelegramDeliveryJobRecord.action_kind == _INITIAL_ACTION,
                    TelegramPublisherDispatchCommand.acknowledged_at.is_not(None),
                )
            )
            or 0
        )


async def _wait_for_worker_acknowledgement(run: MatrixRun) -> None:
    """Require all 1,000 central dispatches to reach publisher workers first."""
    expected_count = len(run.timelines)
    if expected_count != MATRIX_TOTAL_OFFERS:
        raise LiveMatrixError("live_matrix_worker_ack_offer_count_invalid")
    last_acknowledged = -1
    last_progress_at = time.monotonic()
    while True:
        acknowledged_count = await _worker_acknowledgement_progress(run.timelines)
        if acknowledged_count == expected_count:
            # Overtime tasks start from each offer's durable normal deadline.
            # This one phase-boundary hydration records those deadlines without
            # reintroducing a global channel-publication barrier.
            await _hydrate_timelines(run.timelines)
            _write_audit(run)
            return
        if acknowledged_count > last_acknowledged:
            last_acknowledged = acknowledged_count
            last_progress_at = time.monotonic()
        elif time.monotonic() - last_progress_at >= MATRIX_PROGRESS_STALL_SECONDS:
            raise LiveMatrixError("live_matrix_worker_acknowledgement_stalled")
        await asyncio.sleep(MATRIX_PROGRESS_POLL_SECONDS)


async def _terminal_progress_snapshot(run: MatrixRun) -> tuple[int, int, int, int]:
    """Read only the aggregate queue/domain facts needed by the progress loop."""
    public_ids = tuple(
        str(item.offer_public_id)
        for item in run.timelines
        if item.offer_public_id
    )
    completed_ids = tuple(
        int(item.offer_id)
        for item in run.timelines
        if item.offer_id and item.expected_terminal_status == OfferStatus.COMPLETED.value
    )
    expired_ids = tuple(
        int(item.offer_id)
        for item in run.timelines
        if item.offer_id and item.expected_terminal_status == OfferStatus.EXPIRED.value
    )
    if not public_ids:
        return 0, 0, 0, 0
    async with AsyncSessionLocal() as db:
        queue_count = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.source_natural_id.in_(public_ids),
                    TelegramDeliveryJobRecord.action_kind == _INITIAL_ACTION,
                )
            )
            or 0
        )
        posted_count = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.source_natural_id.in_(public_ids),
                    TelegramDeliveryJobRecord.action_kind == _INITIAL_ACTION,
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.SENT,
                )
            )
            or 0
        )
        completed_count = int(
            await db.scalar(
                select(func.count(Offer.id)).where(
                    Offer.id.in_(completed_ids or (-1,)),
                    Offer.status == OfferStatus.COMPLETED,
                )
            )
            or 0
        )
        expired_count = int(
            await db.scalar(
                select(func.count(Offer.id)).where(
                    Offer.id.in_(expired_ids or (-1,)),
                    Offer.status == OfferStatus.EXPIRED,
                )
            )
            or 0
        )
        edited_count = int(
            await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.source_natural_id.in_(public_ids),
                    TelegramDeliveryJobRecord.action_kind.in_(tuple(_TERMINAL_ACTIONS)),
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.SENT,
                )
            )
            or 0
        )
    return queue_count, posted_count, completed_count + expired_count, edited_count


async def _wait_for_initial_publication(
    run: MatrixRun,
    *,
    timelines: Iterable[OfferTimeline] | None = None,
) -> None:
    """Wait for a lifecycle cohort's own initial posts, never a global barrier."""
    selected_timelines = list(timelines if timelines is not None else run.timelines)
    expected_count = len(selected_timelines)
    if not expected_count:
        return
    last_posted = -1
    last_progress_at = time.monotonic()
    while True:
        posted_count, expired_count = await _initial_publication_progress(selected_timelines)
        if _initial_publication_complete(
            posted_count=posted_count,
            expired_count=expired_count,
            expected_count=expected_count,
        ):
            await _hydrate_timelines(selected_timelines)
            _write_audit(run)
            return
        if posted_count > last_posted:
            last_posted = posted_count
            last_progress_at = time.monotonic()
        elif time.monotonic() - last_progress_at >= MATRIX_PROGRESS_STALL_SECONDS:
            raise LiveMatrixError("live_matrix_initial_publication_stalled")
        await asyncio.sleep(MATRIX_PROGRESS_POLL_SECONDS)


async def _assert_timeline_initial_publication(timeline: OfferTimeline) -> None:
    """Fail closed if a delayed lifecycle task reaches an unpublished offer."""
    posted_count, expired_count = await _initial_publication_progress((timeline,))
    if not _initial_publication_complete(
        posted_count=posted_count,
        expired_count=expired_count,
        expected_count=1,
    ):
        raise LiveMatrixError("live_matrix_lifecycle_before_initial_publication")


def _timeline_terminal_follows_initial_publication(timeline: OfferTimeline) -> bool:
    """Keep the final audit strict even though lifecycle starts per-offer."""
    if not timeline.channel_posted_at or not timeline.terminal_at:
        return False
    try:
        return datetime.fromisoformat(timeline.channel_posted_at) <= datetime.fromisoformat(
            timeline.terminal_at
        )
    except (TypeError, ValueError):
        return False


async def _wait_for_terminal_lifecycle(run: MatrixRun) -> None:
    """Wait without a wall-clock cap; fail only when an expected active phase stalls."""
    last_progress = await _terminal_progress_snapshot(run)
    last_progress_at = time.monotonic()
    while True:
        progress = await _terminal_progress_snapshot(run)
        if progress != last_progress:
            last_progress = progress
            last_progress_at = time.monotonic()
        queue_count, posted_count, terminal_count, edited_count = progress
        if (
            queue_count == MATRIX_TOTAL_OFFERS
            and posted_count == MATRIX_TOTAL_OFFERS
            and terminal_count == MATRIX_TOTAL_OFFERS
            and edited_count == MATRIX_TOTAL_OFFERS
        ):
            await _hydrate_timelines(run.timelines)
            if not all(
                _timeline_terminal_follows_initial_publication(item)
                for item in run.timelines
            ):
                raise LiveMatrixError("live_matrix_terminal_before_initial_publication")
            await asyncio.gather(
                *[
                    _observe_webapp_visibility(timeline, terminal=True)
                    for timeline in run.timelines
                    if timeline.webapp_terminal_visible_at is None
                ]
            )
            if all(
                item.webapp_terminal_status == item.expected_terminal_status
                for item in run.timelines
            ):
                _write_audit(run)
                return
            raise LiveMatrixError("live_matrix_webapp_terminal_projection_mismatch")
        now = _utcnow()
        overdue_terminal = any(
            item.offer_status != item.expected_terminal_status
            and item.final_deadline_at is not None
            and datetime.fromisoformat(item.final_deadline_at) <= now
            for item in run.timelines
        )
        due_lifecycle_action = any(
            item.status is None
            and datetime.fromisoformat(item.scheduled_at) <= now
            for item in run.lifecycle_actions
        )
        if (
            (overdue_terminal or due_lifecycle_action)
            and time.monotonic() - last_progress_at >= MATRIX_PROGRESS_STALL_SECONDS
        ):
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
    interaction_task: asyncio.Task[None] | None = None
    visibility_tasks: list[asyncio.Task[None]] = []
    try:
        (
            _channel_id,
            _lanes,
            run.ignored_historical_private_job_count,
        ) = await _assert_live_preflight()
        run.phase = "preflight_passed"
        _write_audit(run)
        if args.preflight_only:
            payload = _report_payload(run)
            return {"passed": bool(payload["passed"] is False and run.failure_reason is None), "report_path": str(report_path), "run_id": run_id, "preflight_only": True}

        from scripts import trading_core_probe_worker as worker

        users = await worker.create_load_fixture_users(run_id, user_count=MATRIX_TOTAL_OFFERS)
        await _configure_overtime_preferences(users=users, workload=workload)
        commodity_id, commodity_name = await worker.resolve_commodity()
        started_monotonic = time.monotonic()
        started_at = _utcnow()
        run.phase = "ingress"
        async with worker.patched_trading_boundaries(
            emulate_callback_answers=True
        ):
            interaction_task = asyncio.create_task(
                _run_user_interactions(
                    worker=worker,
                    users=users,
                    workload=workload,
                    started_monotonic=started_monotonic,
                    started_at=started_at,
                    run=run,
                ),
                name="telegram-live-matrix-user-interactions",
            )
            for index, (origin, scenario) in enumerate(
                zip(workload.origins, workload.scenarios, strict=True), start=1
            ):
                scheduled_at = datetime.fromtimestamp(
                    started_at.timestamp() + (index - 1) * MATRIX_INGRESS_INTERVAL_SECONDS,
                    tz=timezone.utc,
                )
                timeline = OfferTimeline(
                    index=index,
                    origin=origin,
                    scenario=scenario,
                    expected_terminal_status=_EXPECTED_TERMINAL_STATUS_BY_SCENARIO[scenario],
                    scheduled_at=_iso(scheduled_at) or "",
                )
                run.timelines.append(timeline)
                await _create_offer(
                    worker=worker,
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
                    _write_audit(run)
            await asyncio.gather(*visibility_tasks)
            visibility_tasks = []
            if interaction_task is not None:
                await interaction_task
            run.phase = "awaiting_worker_acknowledgement"
            await _wait_for_worker_acknowledgement(run)
            run.phase = "awaiting_direct_initial_publication"
            await _wait_for_initial_publication(
                run,
                timelines=(
                    item
                    for item in run.timelines
                    if item.scenario in _DIRECT_TRADE_SCENARIOS
                ),
            )
            run.phase = "driving_lifecycle"
            await _run_lifecycle_actions(worker=worker, users=users, run=run)
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
