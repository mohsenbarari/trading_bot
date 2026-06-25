#!/usr/bin/env python3
"""Run the comprehensive Bot/WebApp staging load scenario matrix.

The matrix uses synthetic users and offers only. Telegram-side requests are fed
through aiogram Dispatcher; WebApp-side requests call the same router/service
paths used by the PWA. Network/Telegram side effects are patched by
trading_core_probe_worker.patched_trading_boundaries so the run is repeatable in
staging without production peers or real Telegram traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import engine
from core.redis import close_redis
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, override_current_server
from models.offer import OfferStatus
from scripts import trading_core_probe_worker as worker


MATRIX_SCHEMA_VERSION = "bot_webapp_comprehensive_load_matrix_v1"
DEFAULT_MIN_ATTEMPTS_PER_SCENARIO = 40
SHUTDOWN_TIMEOUT_SECONDS = 5.0
SURFACES = ("telegram", "webapp")
ORIGINS = ("bot", "webapp")
OFFER_TYPES = ("buy", "sell")


@dataclass(frozen=True)
class OfferShape:
    name: str
    quantity: int
    request_amount: int
    expected_winner_count: int
    is_wholesale: bool = True
    lot_sizes: tuple[int, ...] = ()


@dataclass(frozen=True)
class MatrixScenario:
    scenario_id: str
    family: str
    offer_origin: str | None = None
    request_surface: str | None = None
    expire_surface: str | None = None
    offer_type: str = "sell"
    shape: str = "wholesale_full"
    terminal_state: str | None = None

    @property
    def name(self) -> str:
        parts = [
            self.scenario_id,
            self.family,
            self.offer_origin,
            self.request_surface,
            self.expire_surface,
            self.offer_type,
            self.shape,
            self.terminal_state,
        ]
        return "__".join(str(part) for part in parts if part)


@dataclass(frozen=True)
class AttemptOutcome:
    status: str
    latency_ms: float
    detail: str | None = None
    start_offset_seconds: float | None = None
    admission_wait_ms: float | None = None


@dataclass(frozen=True)
class OfferExecutionRef:
    id: int
    offer_public_id: str | None
    channel_message_id: int | None = None


SHAPES: dict[str, OfferShape] = {
    "wholesale_full": OfferShape(
        name="wholesale_full",
        quantity=5,
        request_amount=5,
        expected_winner_count=1,
    ),
    "retail_two_lot": OfferShape(
        name="retail_two_lot",
        quantity=20,
        request_amount=10,
        expected_winner_count=2,
        is_wholesale=False,
        lot_sizes=(10, 10),
    ),
    "retail_three_lot": OfferShape(
        name="retail_three_lot",
        quantity=30,
        request_amount=10,
        expected_winner_count=3,
        is_wholesale=False,
        lot_sizes=(10, 10, 10),
    ),
}

WRITE_HEAVY_NON_CONTENTION_FAMILIES = {
    "create_offer",
    "trade_non_concurrent",
    "manual_expire_non_concurrent",
}
READ_VIEW_FAMILIES = {
    "active_view",
    "public_detail_view",
    "market_history_view",
}
DEFAULT_READ_VIEW_MAX_CONCURRENCY = 96
EXPIRY_BUSY_RETRY_ATTEMPTS = 4


def scenario_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:96]


def build_comprehensive_scenarios() -> list[MatrixScenario]:
    scenarios: list[MatrixScenario] = []
    counter = 1

    def add(family: str, **kwargs: Any) -> None:
        nonlocal counter
        scenarios.append(MatrixScenario(scenario_id=f"CLM-{counter:03d}", family=family, **kwargs))
        counter += 1

    for offer_type in OFFER_TYPES:
        for shape_name in SHAPES:
            for surface in SURFACES:
                add("create_offer", request_surface=surface, offer_type=offer_type, shape=shape_name)

            for origin in ORIGINS:
                add("trade_concurrent", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("trade_non_concurrent", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("manual_expire_contention", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("time_expiry", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("after_completed_reject", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("after_manual_expiry_reject", offer_origin=origin, offer_type=offer_type, shape=shape_name)
                add("after_time_expiry_reject", offer_origin=origin, offer_type=offer_type, shape=shape_name)

                for surface in SURFACES:
                    add(
                        "manual_expire_non_concurrent",
                        offer_origin=origin,
                        expire_surface=surface,
                        offer_type=offer_type,
                        shape=shape_name,
                    )
                    add(
                        "active_view",
                        offer_origin=origin,
                        request_surface=surface,
                        offer_type=offer_type,
                        shape=shape_name,
                    )

                for terminal_state in ("active", "completed", "manual_expired", "time_expired"):
                    add(
                        "public_detail_view",
                        offer_origin=origin,
                        request_surface="webapp",
                        offer_type=offer_type,
                        shape=shape_name,
                        terminal_state=terminal_state,
                    )

                for terminal_state in ("completed", "manual_expired", "time_expired"):
                    add(
                        "market_history_view",
                        offer_origin=origin,
                        request_surface="webapp",
                        offer_type=offer_type,
                        shape=shape_name,
                        terminal_state=terminal_state,
                    )

    return scenarios


def filter_scenarios(
    scenarios: Iterable[MatrixScenario],
    *,
    families: set[str],
    names: set[str],
    max_scenarios: int | None,
) -> list[MatrixScenario]:
    selected = [
        scenario
        for scenario in scenarios
        if (not families or scenario.family in families)
        and (not names or scenario.scenario_id in names or scenario.name in names)
    ]
    if max_scenarios is not None:
        selected = selected[: max(0, max_scenarios)]
    return selected


def surface_for_index(index: int, telegram_ratio: float) -> str:
    slots = max(1, min(9, int(round(float(telegram_ratio) * 10))))
    return "telegram" if index % 10 < slots else "webapp"


def scenario_attempt_count(min_attempts: int) -> int:
    return max(int(min_attempts), DEFAULT_MIN_ATTEMPTS_PER_SCENARIO)


def write_admission_max_concurrency_for_scenario(
    scenario: MatrixScenario,
    configured_max_concurrency: int,
) -> int | None:
    if scenario.family not in WRITE_HEAVY_NON_CONTENTION_FAMILIES:
        return None
    max_concurrency = int(configured_max_concurrency or 0)
    return max_concurrency if max_concurrency > 0 else None


def read_view_admission_max_concurrency_for_scenario(
    scenario: MatrixScenario,
    configured_max_concurrency: int,
) -> int | None:
    if scenario.family not in READ_VIEW_FAMILIES:
        return None
    max_concurrency = int(configured_max_concurrency or 0)
    return max_concurrency if max_concurrency > 0 else None


async def reset_scenario_user_runtime_state(users: list[worker.LoadUserRef]) -> int:
    """Clear synthetic-user Redis runtime keys so scenarios stay isolated."""
    user_ids = sorted({int(user.user_id) for user in users})
    return await worker.cleanup_redis_for_user_ids(user_ids)


def summarize_outcomes(outcomes: list[AttemptOutcome], elapsed_seconds: float) -> dict[str, Any]:
    samples = [item.latency_ms for item in outcomes]
    summary = {
        "total": len(outcomes),
        "elapsed_seconds": round(max(elapsed_seconds, 0.001), 3),
        "business_request_rps": round(len(outcomes) / max(elapsed_seconds, 0.001), 3),
        "success": sum(1 for item in outcomes if item.status == "success"),
        "rejected": sum(1 for item in outcomes if item.status == "rejected"),
        "error": sum(1 for item in outcomes if item.status == "error"),
        "latency": worker.summarize_samples(samples),
        "error_details": sorted({item.detail for item in outcomes if item.detail}),
    }
    start_offsets = [
        float(item.start_offset_seconds)
        for item in outcomes
        if item.start_offset_seconds is not None
    ]
    if len(start_offsets) >= 2:
        attempt_start_elapsed = max(start_offsets) - min(start_offsets)
        safe_attempt_start_elapsed = max(float(attempt_start_elapsed), 0.001)
        summary["attempt_start_elapsed_seconds"] = round(safe_attempt_start_elapsed, 3)
        summary["attempt_start_rps"] = round(len(start_offsets) / safe_attempt_start_elapsed, 3)
    admission_waits = [
        float(item.admission_wait_ms)
        for item in outcomes
        if item.admission_wait_ms is not None
    ]
    if admission_waits:
        summary["admission_wait"] = worker.summarize_samples(admission_waits)
    return summary


async def run_scheduled_attempts(
    *,
    total: int,
    target_rps: float,
    attempt: Callable[[int], Awaitable[str]],
    max_concurrency: int | None = None,
) -> tuple[list[AttemptOutcome], float]:
    started = time.perf_counter()
    concurrency_limit = int(max_concurrency or 0)
    semaphore = asyncio.Semaphore(concurrency_limit) if concurrency_limit > 0 else None

    async def _run(index: int) -> AttemptOutcome:
        scheduled_at = started + (index / target_rps)
        delay = scheduled_at - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        attempt_started = time.perf_counter()
        start_offset_seconds = attempt_started - started
        detail = None
        admission_wait_ms = None
        try:
            if semaphore is None:
                status = await attempt(index)
            else:
                async with semaphore:
                    admission_wait_ms = round((time.perf_counter() - attempt_started) * 1000.0, 3)
                    status = await attempt(index)
        except Exception as exc:
            status = "error"
            detail = f"{type(exc).__name__}: {exc}"
        return AttemptOutcome(
            status=status,
            latency_ms=round((time.perf_counter() - attempt_started) * 1000.0, 3),
            detail=detail,
            start_offset_seconds=start_offset_seconds,
            admission_wait_ms=admission_wait_ms,
        )

    outcomes = await asyncio.gather(*[_run(index) for index in range(total)])
    return outcomes, time.perf_counter() - started


async def create_offer(
    *,
    origin: str,
    owner: worker.LoadUserRef,
    commodity_id: int,
    commodity_name: str,
    shape: OfferShape,
    offer_type: str,
    prefix: str,
    index: int,
    bot_harness: worker.AiogramDispatcherHarness | None = None,
    fast_seed_bot_offer: bool = False,
    time_limit_buffer_minutes: int | None = None,
) -> int:
    if origin == "webapp" or fast_seed_bot_offer:
        target_server = SERVER_IRAN if origin == "webapp" else SERVER_FOREIGN
        with override_current_server(target_server):
            return await worker.create_offer_for_user(
                user_id=owner.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=index,
                offer_type=offer_type,
                quantity=shape.quantity,
                price=100000 + index,
                is_wholesale=shape.is_wholesale,
                lot_sizes=shape.lot_sizes,
                channel_message_id=(900_000_000 + index) if origin == "bot" else None,
                time_limit_buffer_minutes=time_limit_buffer_minutes,
                source_surface="telegram_bot" if origin == "bot" else "webapp",
            )
    harness = bot_harness or worker.AiogramDispatcherHarness()
    close_harness = bot_harness is None
    try:
        with override_current_server(SERVER_FOREIGN):
            return await worker.create_bot_offer_with_dispatcher(
                harness=harness,
                owner=owner,
                commodity_name=commodity_name,
                prefix=prefix,
                quantity=shape.quantity,
                price=100000 + index,
                offer_type=offer_type,
                is_wholesale=shape.is_wholesale,
                lot_sizes=shape.lot_sizes,
            )
    finally:
        if close_harness:
            await harness.close()


async def create_non_contention_offers(
    *,
    origin: str,
    users: list[worker.LoadUserRef],
    commodity_id: int,
    commodity_name: str,
    shape: OfferShape,
    offer_type: str,
    prefix: str,
    attempts_per_scenario: int,
    index_offset: int,
    bot_harness: worker.AiogramDispatcherHarness,
    max_concurrency: int | None,
) -> tuple[list[OfferExecutionRef], list[worker.LoadUserRef], float]:
    """Seed independent offers before the measured non-contention attempt phase."""
    semaphore = asyncio.Semaphore(max_concurrency) if max_concurrency and max_concurrency > 0 else None
    started = time.perf_counter()

    async def _create(index: int) -> tuple[int, worker.LoadUserRef, OfferExecutionRef]:
        owner = users[index % len(users)]

        async def _run() -> OfferExecutionRef:
            offer_id = await create_offer(
                origin=origin,
                owner=owner,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                shape=shape,
                offer_type=offer_type,
                prefix=prefix,
                index=index_offset + index,
                bot_harness=bot_harness,
                fast_seed_bot_offer=True,
                time_limit_buffer_minutes=60,
            )
            offer = await worker.load_offer_snapshot(offer_id)
            return OfferExecutionRef(
                id=int(getattr(offer, "id")),
                offer_public_id=getattr(offer, "offer_public_id", None),
                channel_message_id=getattr(offer, "channel_message_id", None),
            )

        if semaphore is None:
            offer_ref = await _run()
        else:
            async with semaphore:
                offer_ref = await _run()
        return index, owner, offer_ref

    results = await asyncio.gather(*(_create(index) for index in range(attempts_per_scenario)))
    results.sort(key=lambda item: item[0])
    return (
        [offer_ref for _, _, offer_ref in results],
        [owner for _, owner, _ in results],
        time.perf_counter() - started,
    )


async def execute_trade_attempt(
    *,
    surface: str,
    harness: worker.AiogramDispatcherHarness,
    user: worker.LoadUserRef,
    offer_id: int,
    amount: int,
    prefix: str,
    index: int,
    error_details: list[str] | None = None,
    preconfirm_telegram: bool = False,
    record_rejected_details: bool = False,
    offer_ref: OfferExecutionRef | None = None,
) -> str:
    offer = offer_ref or await worker.load_offer_snapshot(offer_id)
    if surface == "telegram":
        with override_current_server(SERVER_FOREIGN):
            spec = worker.MixedLoadAttemptSpec(
                index=index,
                surface="telegram",
                user_id=user.user_id,
                telegram_id=user.telegram_id,
            )
            phase_details: dict[str, Any] = {}
            preconfirmed = False
            if preconfirm_telegram:
                preconfirm_result = await worker.preconfirm_bot_trade_callback(
                    spec=spec,
                    offer=offer,
                    amount=amount,
                )
                phase_details["telegram_preconfirm_result"] = str(preconfirm_result)
                preconfirmed = preconfirm_result is False
                if isinstance(preconfirm_result, str) and error_details is not None:
                    error_details.append(f"telegram_preconfirm_failed: {preconfirm_result}")
            status = await worker.execute_bot_trade_with_dispatcher(
                harness=harness,
                spec=spec,
                offer=offer,
                amount=amount,
                prefix=prefix,
                error_details=error_details,
                phase_details=phase_details,
                preconfirmed=preconfirmed,
            )
            if status == "rejected" and record_rejected_details and error_details is not None:
                answer_text = (
                    str(phase_details.get("second_answer_text") or "")
                    or str(phase_details.get("first_answer_text") or "")
                    or "telegram callback rejected without answer text"
                )
                error_details.append(f"telegram_callback_rejected: {answer_text}")
            return status
    with override_current_server(SERVER_IRAN):
        return await worker.execute_webapp_trade_for_user(
            user_id=user.user_id,
            offer_id=offer_id,
            offer_public_id=getattr(offer, "offer_public_id", None),
            quantity=amount,
            idempotency_key=worker.build_role_attempt_idempotency_key(
                prefix=prefix,
                role="webapp",
                offer_id=offer_id,
                attempt_index=index,
            ),
            error_details=error_details,
        )


async def expire_attempt(
    *,
    surface: str,
    harness: worker.AiogramDispatcherHarness,
    owner: worker.LoadUserRef,
    offer_id: int,
    prefix: str,
    index: int,
    error_details: list[str] | None = None,
) -> str:
    busy_detail = worker.offers_router.OFFER_EXPIRY_LOCK_BUSY_DETAIL
    for attempt in range(EXPIRY_BUSY_RETRY_ATTEMPTS):
        details_before = len(error_details or [])
        if surface == "telegram":
            with override_current_server(SERVER_FOREIGN):
                status = await worker.expire_bot_offer_with_dispatcher(
                    harness=harness,
                    owner=owner,
                    offer_id=offer_id,
                    prefix=prefix,
                    index=index + attempt,
                    error_details=error_details,
                )
            if status == "success":
                return "success"
            recent_details = (error_details or [])[details_before:]
            if attempt < EXPIRY_BUSY_RETRY_ATTEMPTS - 1 and any(busy_detail in str(item) for item in recent_details):
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            return status
        try:
            with override_current_server(SERVER_IRAN):
                await worker.expire_offer_for_user(user_id=owner.user_id, offer_id=offer_id)
            return "success"
        except Exception as exc:
            detail = str(getattr(exc, "detail", "") or exc)
            if attempt < EXPIRY_BUSY_RETRY_ATTEMPTS - 1 and busy_detail in detail:
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            if error_details is not None:
                error_details.append(f"{type(exc).__name__}: {exc}")
            return "rejected"
    return "rejected"


def owner_for_origin(users: list[worker.LoadUserRef], origin: str) -> worker.LoadUserRef:
    return users[1] if origin == "bot" else users[0]


def responder_for_index(
    users: list[worker.LoadUserRef],
    *,
    owner_user_id: int,
    index: int,
) -> worker.LoadUserRef:
    responders = [user for user in users if user.user_id != owner_user_id]
    return responders[index % len(responders)]


async def finalize_offer_for_terminal_state(
    *,
    terminal_state: str,
    origin: str,
    owner: worker.LoadUserRef,
    users: list[worker.LoadUserRef],
    commodity_id: int,
    commodity_name: str,
    shape: OfferShape,
    offer_type: str,
    prefix: str,
) -> int:
    harness = worker.AiogramDispatcherHarness()
    try:
        offer_id = await create_offer(
            origin=origin,
            owner=owner,
            commodity_id=commodity_id,
            commodity_name=commodity_name,
            shape=shape,
            offer_type=offer_type,
            prefix=prefix,
            index=7000,
            bot_harness=harness,
        )
        if terminal_state == "active":
            return offer_id
        if terminal_state == "completed":
            for index in range(shape.expected_winner_count):
                responder = responder_for_index(users, owner_user_id=owner.user_id, index=index)
                status = await execute_trade_attempt(
                    surface="webapp",
                    harness=harness,
                    user=responder,
                    offer_id=offer_id,
                    amount=shape.request_amount,
                    prefix=f"{prefix}complete-",
                    index=index,
                )
                if status != "success":
                    raise worker.TradingProbeError(f"terminal setup trade failed with status={status}")
            await assert_offer_terminal(offer_id=offer_id, expected_status=OfferStatus.COMPLETED.value)
            return offer_id
        if terminal_state == "manual_expired":
            status = await expire_attempt(
                surface="webapp",
                harness=harness,
                owner=owner,
                offer_id=offer_id,
                prefix=f"{prefix}manual-terminal-",
                index=0,
            )
            if status != "success":
                raise worker.TradingProbeError(f"terminal manual expiry setup failed with status={status}")
            await assert_offer_terminal(offer_id=offer_id, expected_status=OfferStatus.EXPIRED.value)
            return offer_id
        if terminal_state == "time_expired":
            await worker.age_offer_for_time_expiry(offer_id=offer_id, age_minutes=10)
            target_server = SERVER_FOREIGN if origin == "bot" else SERVER_IRAN
            await worker.run_offer_expiry_cycle_for_server(target_server)
            await assert_offer_terminal(offer_id=offer_id, expected_status=OfferStatus.EXPIRED.value)
            return offer_id
    finally:
        await harness.close()
    raise worker.TradingProbeError(f"unsupported terminal state: {terminal_state}")


async def assert_offer_terminal(
    *,
    offer_id: int,
    expected_status: str,
) -> None:
    offer = await worker.load_offer_snapshot(offer_id)
    status = getattr(getattr(offer, "status", None), "value", None)
    if status != expected_status:
        raise worker.TradingProbeError(f"offer {offer_id} expected status {expected_status}, got {status}")


async def run_scenario(
    *,
    scenario: MatrixScenario,
    users: list[worker.LoadUserRef],
    commodity_id: int,
    commodity_name: str,
    attempts_per_scenario: int,
    telegram_ratio: float,
    target_rps: float,
    run_prefix: str,
    write_max_concurrency: int,
    read_view_max_concurrency: int,
) -> dict[str, Any]:
    shape = SHAPES[scenario.shape]
    scenario_prefix = f"{run_prefix}{scenario.scenario_id}_"
    scenario_write_max_concurrency = write_admission_max_concurrency_for_scenario(
        scenario,
        write_max_concurrency,
    )
    scenario_read_view_max_concurrency = read_view_admission_max_concurrency_for_scenario(
        scenario,
        read_view_max_concurrency,
    )
    await worker.cleanup_prefix(scenario_prefix)
    started = time.perf_counter()
    correctness_failures: list[str] = []
    outcomes: list[AttemptOutcome] = []
    summary: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    attempt_error_details: list[str] = []

    try:
        await reset_scenario_user_runtime_state(users)
        if scenario.family == "create_offer":
            harness = worker.AiogramDispatcherHarness()

            async def _attempt(index: int) -> str:
                user = users[index % len(users)]
                await create_offer(
                    origin="bot" if scenario.request_surface == "telegram" else "webapp",
                    owner=user,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    shape=shape,
                    offer_type=scenario.offer_type,
                    prefix=scenario_prefix,
                    index=index,
                    bot_harness=harness,
                )
                return "success"

            try:
                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                    max_concurrency=scenario_write_max_concurrency,
                )
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)

        elif scenario.family == "trade_concurrent":
            owner = owner_for_origin(users, scenario.offer_origin or "webapp")
            offer_id = await create_offer(
                origin=scenario.offer_origin or "webapp",
                owner=owner,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                shape=shape,
                offer_type=scenario.offer_type,
                prefix=scenario_prefix,
                index=1000,
            )
            report = await worker.run_hot_offer_contention(
                prefix=scenario_prefix,
                offer_id=offer_id,
                owner_user_id=owner.user_id,
                users=users,
                total_requests=attempts_per_scenario,
                telegram_ratio=telegram_ratio,
                target_rps=target_rps,
                amount=shape.request_amount,
                expected_winner_count=shape.expected_winner_count,
                check=False,
            )
            summary = dict(report["summary"])
            extra = {
                "offer_id": offer_id,
                "persistence": report["persistence"],
                "telegram_preconfirm": report.get("telegram_preconfirm"),
            }
            correctness_failures.extend(str(item) for item in report.get("correctness_failures") or [])

        elif scenario.family == "trade_non_concurrent":
            harness = worker.AiogramDispatcherHarness()
            fixture_elapsed = 0.0
            try:
                offer_refs, offer_owners, fixture_elapsed = await create_non_contention_offers(
                    origin=scenario.offer_origin or "webapp",
                    users=users,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    shape=shape,
                    offer_type=scenario.offer_type,
                    prefix=scenario_prefix,
                    attempts_per_scenario=attempts_per_scenario,
                    index_offset=2000,
                    bot_harness=harness,
                    max_concurrency=scenario_write_max_concurrency,
                )

                async def _attempt(index: int) -> str:
                    surface = surface_for_index(index, telegram_ratio)
                    owner = offer_owners[index]
                    responder = responder_for_index(users, owner_user_id=owner.user_id, index=index)
                    return await execute_trade_attempt(
                        surface=surface,
                        harness=harness,
                        user=responder,
                        offer_id=offer_refs[index].id,
                        amount=shape.request_amount,
                        prefix=scenario_prefix,
                        index=index,
                        error_details=attempt_error_details,
                        preconfirm_telegram=True,
                        record_rejected_details=True,
                        offer_ref=offer_refs[index],
                    )

                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                    max_concurrency=scenario_write_max_concurrency,
                )
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)
            extra = {"fixture_creation_elapsed_seconds": round(fixture_elapsed, 3)}
            if summary["success"] != attempts_per_scenario:
                correctness_failures.append("non-concurrent trade expected every request to succeed")

        elif scenario.family == "manual_expire_non_concurrent":
            harness = worker.AiogramDispatcherHarness()
            fixture_elapsed = 0.0
            try:
                offer_refs, offer_owners, fixture_elapsed = await create_non_contention_offers(
                    origin=scenario.offer_origin or "webapp",
                    users=users,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    shape=shape,
                    offer_type=scenario.offer_type,
                    prefix=scenario_prefix,
                    attempts_per_scenario=attempts_per_scenario,
                    index_offset=3000,
                    bot_harness=harness,
                    max_concurrency=scenario_write_max_concurrency,
                )

                async def _attempt(index: int) -> str:
                    return await expire_attempt(
                        surface=scenario.expire_surface or "webapp",
                        harness=harness,
                        owner=offer_owners[index],
                        offer_id=offer_refs[index].id,
                        prefix=scenario_prefix,
                        index=index,
                        error_details=attempt_error_details,
                    )

                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                    max_concurrency=scenario_write_max_concurrency,
                )
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)
            extra = {"fixture_creation_elapsed_seconds": round(fixture_elapsed, 3)}
            if summary["success"] != attempts_per_scenario:
                correctness_failures.append("non-concurrent manual expiry expected every request to succeed")

        elif scenario.family == "manual_expire_contention":
            owner = owner_for_origin(users, scenario.offer_origin or "webapp")
            harness = worker.AiogramDispatcherHarness()
            try:
                offer_id = await create_offer(
                    origin=scenario.offer_origin or "webapp",
                    owner=owner,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    shape=shape,
                    offer_type=scenario.offer_type,
                    prefix=scenario_prefix,
                    index=4000,
                    bot_harness=harness,
                )

                async def _attempt(index: int) -> str:
                    return await expire_attempt(
                        surface=surface_for_index(index, telegram_ratio),
                        harness=harness,
                        owner=owner,
                        offer_id=offer_id,
                        prefix=scenario_prefix,
                        index=index,
                        error_details=attempt_error_details,
                    )

                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                )
                await assert_offer_terminal(offer_id=offer_id, expected_status=OfferStatus.EXPIRED.value)
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)
            if summary["success"] != 1:
                correctness_failures.append("manual expiry contention expected exactly one successful expiry")

        elif scenario.family == "time_expiry":
            origin = scenario.offer_origin or "webapp"
            harness = worker.AiogramDispatcherHarness()
            offer_ids: list[int] = []
            try:
                for index in range(attempts_per_scenario):
                    owner = users[index % len(users)]
                    offer_id = await create_offer(
                        origin=origin,
                        owner=owner,
                        commodity_id=commodity_id,
                        commodity_name=commodity_name,
                        shape=shape,
                        offer_type=scenario.offer_type,
                        prefix=scenario_prefix,
                        index=5000 + index,
                        bot_harness=harness,
                        fast_seed_bot_offer=True,
                    )
                    await worker.age_offer_for_time_expiry(offer_id=offer_id, age_minutes=10)
                    offer_ids.append(offer_id)
            finally:
                await harness.close()
            target_server = SERVER_FOREIGN if origin == "bot" else SERVER_IRAN
            expired_count = await worker.run_offer_expiry_cycle_for_server(target_server)
            elapsed = time.perf_counter() - started
            outcomes = [AttemptOutcome(status="success", latency_ms=0.0) for _ in offer_ids]
            summary = summarize_outcomes(outcomes, elapsed)
            extra = {"expired_count": expired_count, "offer_ids_checked": len(offer_ids)}
            for offer_id in offer_ids:
                try:
                    await assert_offer_terminal(offer_id=offer_id, expected_status=OfferStatus.EXPIRED.value)
                except Exception as exc:
                    correctness_failures.append(str(exc))
                    break

        elif scenario.family in {
            "after_completed_reject",
            "after_manual_expiry_reject",
            "after_time_expiry_reject",
        }:
            terminal_state = {
                "after_completed_reject": "completed",
                "after_manual_expiry_reject": "manual_expired",
                "after_time_expiry_reject": "time_expired",
            }[scenario.family]
            origin = scenario.offer_origin or "webapp"
            owner = owner_for_origin(users, origin)
            offer_id = await finalize_offer_for_terminal_state(
                terminal_state=terminal_state,
                origin=origin,
                owner=owner,
                users=users,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                shape=shape,
                offer_type=scenario.offer_type,
                prefix=scenario_prefix,
            )
            harness = worker.AiogramDispatcherHarness()
            try:
                async def _attempt(index: int) -> str:
                    responder = responder_for_index(users, owner_user_id=owner.user_id, index=index)
                    return await execute_trade_attempt(
                        surface=surface_for_index(index, telegram_ratio),
                        harness=harness,
                        user=responder,
                        offer_id=offer_id,
                        amount=shape.request_amount,
                        prefix=scenario_prefix,
                        index=index,
                        error_details=attempt_error_details,
                    )

                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                )
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)
            if summary["success"]:
                correctness_failures.append("terminal offer request expected zero successful trades")

        elif scenario.family == "active_view":
            origin = scenario.offer_origin or "webapp"
            owner = owner_for_origin(users, origin)
            offer_id = await create_offer(
                origin=origin,
                owner=owner,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                shape=shape,
                offer_type=scenario.offer_type,
                prefix=scenario_prefix,
                index=6000,
            )
            harness = worker.AiogramDispatcherHarness()
            try:
                async def _attempt(index: int) -> str:
                    viewer = responder_for_index(users, owner_user_id=owner.user_id, index=index)
                    if scenario.request_surface == "telegram":
                        with override_current_server(SERVER_FOREIGN):
                            return await worker.execute_bot_market_view_with_dispatcher(
                                harness=harness,
                                user=viewer,
                                error_details=attempt_error_details,
                            )
                    with override_current_server(SERVER_IRAN):
                        count = await worker.list_active_offers_for_user(user_id=viewer.user_id)
                    return "success" if count >= 1 else "error"

                outcomes, elapsed = await run_scheduled_attempts(
                    total=attempts_per_scenario,
                    target_rps=target_rps,
                    attempt=_attempt,
                    max_concurrency=scenario_read_view_max_concurrency,
                )
            finally:
                await harness.close()
            summary = summarize_outcomes(outcomes, elapsed)
            extra = {"seed_offer_id": offer_id}

        elif scenario.family in {"public_detail_view", "market_history_view"}:
            origin = scenario.offer_origin or "webapp"
            owner = owner_for_origin(users, origin)
            terminal_state = scenario.terminal_state or "active"
            offer_id = await finalize_offer_for_terminal_state(
                terminal_state=terminal_state,
                origin=origin,
                owner=owner,
                users=users,
                commodity_id=commodity_id,
                commodity_name=commodity_name,
                shape=shape,
                offer_type=scenario.offer_type,
                prefix=scenario_prefix,
            )
            offer = await worker.load_offer_snapshot(offer_id)
            public_id = worker.offer_public_identity(offer)
            if not public_id:
                raise worker.TradingProbeError("seed offer has no public id")

            async def _attempt(index: int) -> str:
                viewer = (
                    owner
                    if scenario.family == "public_detail_view"
                    else responder_for_index(users, owner_user_id=owner.user_id, index=index)
                )
                with override_current_server(SERVER_IRAN):
                    if scenario.family == "public_detail_view":
                        await worker.load_public_offer_detail_for_user(
                            user_id=viewer.user_id,
                            offer_public_id=public_id,
                        )
                    else:
                        await worker.list_market_history_for_user(user_id=viewer.user_id)
                return "success"

            outcomes, elapsed = await run_scheduled_attempts(
                total=attempts_per_scenario,
                target_rps=target_rps,
                attempt=_attempt,
                max_concurrency=scenario_read_view_max_concurrency,
            )
            summary = summarize_outcomes(outcomes, elapsed)
            extra = {"seed_offer_id": offer_id, "offer_public_id": public_id}

        else:
            raise worker.TradingProbeError(f"unsupported scenario family: {scenario.family}")
    except Exception as exc:
        correctness_failures.append(f"{type(exc).__name__}: {exc}")
        summary = summary or summarize_outcomes(outcomes, time.perf_counter() - started)
    finally:
        await worker.cleanup_prefix(scenario_prefix)

    if summary.get("error"):
        correctness_failures.append(f"{summary['error']} request(s) ended with error")
    if scenario_write_max_concurrency is not None:
        summary["admission_control"] = {
            "max_concurrency": scenario_write_max_concurrency,
            "scope": "write_heavy_non_contention",
        }
    if scenario_read_view_max_concurrency is not None:
        summary["admission_control"] = {
            "max_concurrency": scenario_read_view_max_concurrency,
            "scope": "read_view",
        }
    if attempt_error_details:
        summary["attempt_error_details"] = sorted(set(attempt_error_details))[:20]

    return {
        "scenario": asdict(scenario),
        "status": "ok" if not correctness_failures else "failed",
        "summary": summary,
        "correctness_failures": correctness_failures,
        "extra": extra,
    }


async def run_matrix(args: argparse.Namespace) -> int:
    all_scenarios = build_comprehensive_scenarios()
    selected = filter_scenarios(
        all_scenarios,
        families=set(args.family or []),
        names=set(args.scenario or []),
        max_scenarios=args.max_scenarios,
    )
    if args.list:
        print(
            json.dumps(
                {
                    "schema_version": MATRIX_SCHEMA_VERSION,
                    "scenario_count": len(all_scenarios),
                    "selected_count": len(selected),
                    "scenarios": [asdict(scenario) | {"name": scenario.name} for scenario in selected],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not selected:
        raise worker.TradingProbeError("no scenarios selected")

    worker.setup_event_listeners()
    prefix = args.prefix
    worker.assert_production_full_matrix_allowed(prefix, allow_flag=bool(args.allow_production_execution))
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(prefix, allow_flag=bool(args.allow_production_cleanup))
    await worker.cleanup_prefix(prefix)
    commodity_id, commodity_name = await worker.resolve_commodity()
    attempts_per_scenario = scenario_attempt_count(args.attempts_per_scenario)
    started = time.perf_counter()
    scenario_reports: list[dict[str, Any]] = []
    async with worker.patched_trading_boundaries():
        users = await worker.create_load_fixture_users(prefix, user_count=args.user_count)
        for scenario in selected:
            scenario_reports.append(
                await run_scenario(
                    scenario=scenario,
                    users=users,
                    commodity_id=commodity_id,
                    commodity_name=commodity_name,
                    attempts_per_scenario=attempts_per_scenario,
                    telegram_ratio=args.telegram_ratio,
                    target_rps=args.target_rps,
                    run_prefix=prefix,
                    write_max_concurrency=args.write_max_concurrency,
                    read_view_max_concurrency=args.read_view_max_concurrency,
                )
            )

    elapsed = time.perf_counter() - started
    total_attempts = sum(int((report.get("summary") or {}).get("total") or 0) for report in scenario_reports)
    scenario_attempt_start_rps = [
        float((report.get("summary") or {}).get("attempt_start_rps"))
        for report in scenario_reports
        if (report.get("summary") or {}).get("attempt_start_rps") is not None
    ]
    failed = [report for report in scenario_reports if report["status"] != "ok"]
    family_counts: dict[str, int] = {}
    for scenario in selected:
        family_counts[scenario.family] = family_counts.get(scenario.family, 0) + 1
    cleanup_report = None
    if not args.keep_data:
        cleanup_report = await worker.cleanup_prefix(prefix)

    payload = {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "status": "ok" if not failed else "failed",
        "prefix": prefix,
        "user_count": args.user_count,
        "telegram_ratio": args.telegram_ratio,
        "target_rps": args.target_rps,
        "attempts_per_scenario": attempts_per_scenario,
        "write_max_concurrency": args.write_max_concurrency,
        "read_view_max_concurrency": args.read_view_max_concurrency,
        "scenario_count": len(selected),
        "family_counts": dict(sorted(family_counts.items())),
        "total_business_requests": total_attempts,
        "elapsed_seconds": round(elapsed, 3),
        "aggregate_business_request_rps": round(total_attempts / max(elapsed, 0.001), 3),
        "min_attempt_start_rps": round(min(scenario_attempt_start_rps), 3) if scenario_attempt_start_rps else None,
        "failed_scenarios": [
            {
                "scenario_id": report["scenario"]["scenario_id"],
                "family": report["scenario"]["family"],
                "correctness_failures": report["correctness_failures"],
            }
            for report in failed
        ],
        "cleanup": cleanup_report,
        "reports": scenario_reports,
        "production_gate": {
            "status": "production_execution_allowed" if args.allow_production_execution else "blocked_until_owner_staging_validation",
            "reason": (
                "Production execution was explicitly confirmed for the guarded full-matrix run."
                if args.allow_production_execution
                else "Comprehensive load evidence is staging-only and does not authorize production by itself."
            ),
        },
    }
    if args.output:
        worker.write_json_artifact(Path(args.output), payload)
    print(json.dumps(stdout_payload_for_matrix(payload, output_path=args.output), ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" or not args.check else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run comprehensive Bot/WebApp staging load matrix.")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--user-count", type=int, default=1000)
    parser.add_argument("--attempts-per-scenario", type=int, default=DEFAULT_MIN_ATTEMPTS_PER_SCENARIO)
    parser.add_argument("--target-rps", type=float, default=600.0)
    parser.add_argument("--telegram-ratio", type=float, default=0.6)
    parser.add_argument(
        "--write-max-concurrency",
        type=int,
        default=24,
        help=(
            "Admission-control limit for write-heavy non-contention scenarios. "
            "Set <=0 to disable."
        ),
    )
    parser.add_argument(
        "--read-view-max-concurrency",
        "--telegram-read-max-concurrency",
        dest="read_view_max_concurrency",
        type=int,
        default=DEFAULT_READ_VIEW_MAX_CONCURRENCY,
        help=(
            "Admission-control limit for read/view scenarios. "
            "Set <=0 to disable."
        ),
    )
    parser.add_argument("--family", action="append", help="Run only this scenario family; repeatable.")
    parser.add_argument("--scenario", action="append", help="Run only this scenario id/name; repeatable.")
    parser.add_argument("--max-scenarios", type=int)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--allow-production-execution",
        action="store_true",
        help="Allow production market matrix fixture creation only with the production full-matrix confirmation env.",
    )
    parser.add_argument(
        "--allow-production-cleanup",
        action="store_true",
        help="Allow production market matrix cleanup only with the cleanup confirmation env.",
    )
    return parser


def stdout_payload_for_matrix(payload: dict[str, Any], *, output_path: str | None) -> dict[str, Any]:
    if not output_path:
        return payload
    cleanup = payload.get("cleanup")
    cleanup_counts = cleanup.get("planned_counts") if isinstance(cleanup, dict) else None
    return {
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "prefix": payload.get("prefix"),
        "result_path": output_path,
        "scenario_count": payload.get("scenario_count"),
        "total_business_requests": payload.get("total_business_requests"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "aggregate_business_request_rps": payload.get("aggregate_business_request_rps"),
        "min_attempt_start_rps": payload.get("min_attempt_start_rps"),
        "failed_scenarios": payload.get("failed_scenarios"),
        "cleanup_counts": cleanup_counts,
        "production_gate": payload.get("production_gate"),
    }


async def run_shutdown_step(label: str, awaitable: Awaitable[Any]) -> None:
    task = asyncio.create_task(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    if not done:
        task.cancel()
        print(
            json.dumps(
                {
                    "event": "shutdown_step_timeout",
                    "label": label,
                    "timeout_seconds": SHUTDOWN_TIMEOUT_SECONDS,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return
    try:
        task.result()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "shutdown_step_failed",
                    "error_type": type(exc).__name__,
                    "label": label,
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )


async def run_matrix_with_shutdown(args: argparse.Namespace) -> int:
    try:
        return await run_matrix(args)
    finally:
        await run_shutdown_step("redis", close_redis())
        await run_shutdown_step("database_engine", engine.dispose())


def run_async_entrypoint(args: argparse.Namespace) -> int:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_matrix_with_shutdown(args))
    finally:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.sleep(0))
        loop.close()
        asyncio.set_event_loop(None)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_async_entrypoint(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
