#!/usr/bin/env python3
"""Run the owner-approved sustained Telegram publisher B2B acceptance matrix.

The matrix is deliberately transport-only: it creates real durable commands
and lets the normal primary/publisher pollers exchange their actual Telegram
messages and ACKs.  Its synthetic queue jobs are non-deliverable by design:
they reference no domain offer and become eligible only after their freshness
deadline.  A crash therefore cannot turn the probe into a channel post.

The ten user interactions are in-process aiogram dispatcher simulations using
the same bot middleware/handlers, synthetic users, and a recording Telegram
gateway.  They run concurrently with B2B ingress but never call Telegram.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
import json
import math
import sys
import time
from typing import Any, Sequence

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN
from core.services.telegram_delivery_queue_service import enqueue_telegram_delivery_job
from core.services.telegram_publisher_dispatch_service import (
    get_or_create_telegram_publisher_dispatch_command,
)
from core.telegram_delivery_offer_freshness import telegram_channel_destination_key
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_multi_publisher_contract import TelegramPublisherDispatchState
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_publisher_dispatch_command import TelegramPublisherDispatchCommand
from scripts.run_telegram_publisher_b2b_harness import (
    B2BHarnessError,
    _FINAL_COMMAND_STATES,
    _assert_quiet_outbox,
    _enum_value,
    _new_run_id,
    _require_live_configuration,
    _terminalize_probe_jobs,
)


MATRIX_TOTAL_COMMANDS = 1_000
MATRIX_INTERACTIONS = 10
MATRIX_INGRESS_INTERVAL_SECONDS = 0.5
MATRIX_FRESHNESS_WINDOW_SECONDS = 120.0
MATRIX_GUARD_DELAY_SECONDS = 300.0
MATRIX_ACK_TIMEOUT_SECONDS = 150.0


@dataclass(frozen=True, slots=True)
class MatrixWorkload:
    lane_sequence: tuple[str, ...]
    interaction_offsets_seconds: tuple[float, ...]


def build_matrix_workload(
    *,
    lanes: Sequence[str],
    total_commands: int,
    interaction_count: int,
    ingress_interval_seconds: float,
) -> MatrixWorkload:
    normalized_lanes = tuple(str(lane) for lane in lanes)
    if not normalized_lanes:
        raise B2BHarnessError("b2b_matrix_lanes_missing")
    if total_commands != MATRIX_TOTAL_COMMANDS:
        raise B2BHarnessError("b2b_matrix_total_commands_must_equal_1000")
    if interaction_count != MATRIX_INTERACTIONS:
        raise B2BHarnessError("b2b_matrix_interactions_must_equal_10")
    if not math.isclose(
        float(ingress_interval_seconds),
        MATRIX_INGRESS_INTERVAL_SECONDS,
        abs_tol=0.000_001,
    ):
        raise B2BHarnessError("b2b_matrix_ingress_must_be_two_per_second")
    if total_commands % len(normalized_lanes) != 0:
        raise B2BHarnessError("b2b_matrix_lanes_must_divide_total")
    lane_sequence = tuple(
        normalized_lanes[index % len(normalized_lanes)]
        for index in range(total_commands)
    )
    duration = total_commands * float(ingress_interval_seconds)
    interaction_offsets = tuple(
        duration * (index + 1) / (interaction_count + 1)
        for index in range(interaction_count)
    )
    return MatrixWorkload(
        lane_sequence=lane_sequence,
        interaction_offsets_seconds=interaction_offsets,
    )


async def _create_matrix_job(
    *,
    run_id: str,
    lane: str,
    lane_ordinal: int,
    channel_id: int,
) -> int:
    now = utc_now()
    # Eligibility deliberately follows freshness.  If the process dies before
    # cleanup, a Queue-v1 worker can only supersede this missing source offer;
    # it cannot issue a channel provider call.
    result: Any
    async with AsyncSessionLocal() as db:
        result = await enqueue_telegram_delivery_job(
            db,
            current_server=SERVER_FOREIGN,
            feeder=TelegramFeederKind.OFFER_CONTROL,
            source_natural_id=f"{run_id}:{lane}:{lane_ordinal}",
            source_version=1,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            bot_identity=lane,
            destination_key=telegram_channel_destination_key(channel_id),
            destination_class=TelegramDestinationClass.CHANNEL,
            method="sendMessage",
            payload={
                "chat_id": channel_id,
                "text": f"b2b matrix transport probe {run_id} {lane} {lane_ordinal}",
            },
            template_version="b2b-matrix-v1",
            eligible_at=now + timedelta(seconds=MATRIX_GUARD_DELAY_SECONDS),
            freshness_deadline_at=now
            + timedelta(seconds=MATRIX_FRESHNESS_WINDOW_SECONDS),
            run_id=run_id,
        )
        if not result.created:
            raise B2BHarnessError("b2b_matrix_job_identity_collision")
        command = await get_or_create_telegram_publisher_dispatch_command(
            db,
            current_server=SERVER_FOREIGN,
            job=result.job,
            publisher_bot_identity=lane,
            now=now,
        )
        if _enum_value(command.state) != TelegramPublisherDispatchState.PENDING.value:
            raise B2BHarnessError("b2b_matrix_command_not_pending")
        await db.commit()
    return int(result.job.id)


async def _active_matrix_commands(job_ids: Sequence[int]) -> int:
    if not job_ids:
        return 0
    async with AsyncSessionLocal() as db:
        return int(
            (
                await db.execute(
                    select_count_commands(job_ids)
                )
            ).scalar_one()
            or 0
        )


def select_count_commands(job_ids: Sequence[int]):
    """Return a narrow count statement without exposing fixture payloads."""
    from sqlalchemy import func, select

    return select(func.count(TelegramPublisherDispatchCommand.id)).where(
        TelegramPublisherDispatchCommand.job_id.in_(tuple(job_ids)),
        TelegramPublisherDispatchCommand.state.notin_(tuple(_FINAL_COMMAND_STATES)),
    )


async def _load_matrix_rows(job_ids: Sequence[int]) -> list[tuple[Any, Any]]:
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(TelegramDeliveryJobRecord, TelegramPublisherDispatchCommand)
                    .join(
                        TelegramPublisherDispatchCommand,
                        TelegramPublisherDispatchCommand.job_id
                        == TelegramDeliveryJobRecord.id,
                    )
                    .where(TelegramDeliveryJobRecord.id.in_(tuple(job_ids)))
                    .order_by(TelegramDeliveryJobRecord.id.asc())
                )
            ).all()
        )


def _matrix_report(
    rows: Sequence[tuple[Any, Any]], *, lanes: Sequence[str], max_outstanding: int
) -> dict[str, Any]:
    lane_counts = Counter(str(command.publisher_bot_identity) for _, command in rows)
    state_counts = Counter(_enum_value(command.state) for _, command in rows)
    attempts = [int(command.attempt_count or 0) for _, command in rows]
    acknowledgement_lags: list[int] = []
    for _, command in rows:
        created_at = getattr(command, "created_at", None)
        acknowledged_at = getattr(command, "acknowledged_at", None)
        if created_at is not None and acknowledged_at is not None:
            acknowledgement_lags.append(
                max(0, int((acknowledged_at - created_at).total_seconds() * 1000))
            )
    provider_attempts = sum(
        int(getattr(job, "provider_attempt_count", 0) or 0)
        for job, _ in rows
    )
    expected_per_lane = MATRIX_TOTAL_COMMANDS // len(lanes)
    per_lane_valid = all(lane_counts.get(lane, 0) == expected_per_lane for lane in lanes)
    all_acknowledged = (
        len(rows) == MATRIX_TOTAL_COMMANDS
        and state_counts.get(TelegramPublisherDispatchState.ACKNOWLEDGED.value, 0)
        == MATRIX_TOTAL_COMMANDS
    )
    no_duplicate_dispatch = bool(attempts) and all(value == 1 for value in attempts)
    receipts_valid = all(
        int(command.receipt_sequence or 0) == int(job.enqueued_seq or 0)
        and getattr(command, "receipt_received_at", None) is not None
        for job, command in rows
    )
    no_channel_provider_attempt = provider_attempts == 0 and all(
        getattr(job, "dispatch_started_at", None) is None for job, _ in rows
    )
    return {
        "commands_expected": MATRIX_TOTAL_COMMANDS,
        "commands_observed": len(rows),
        "acknowledged": state_counts.get(
            TelegramPublisherDispatchState.ACKNOWLEDGED.value, 0
        ),
        "per_lane_count_valid": per_lane_valid,
        "no_duplicate_dispatch": no_duplicate_dispatch,
        "receipt_sequence_valid": receipts_valid,
        "channel_provider_attempts": provider_attempts,
        "max_observed_unacknowledged": max_outstanding,
        "ack_lag_ms": {
            "min": min(acknowledgement_lags, default=0),
            "max": max(acknowledgement_lags, default=0),
        },
        "passed": all_acknowledged
        and per_lane_valid
        and no_duplicate_dispatch
        and receipts_valid
        and no_channel_provider_attempt,
    }


async def _wait_for_matrix_acknowledgements(
    *, job_ids: Sequence[int], lanes: Sequence[str], max_outstanding: int
) -> dict[str, Any]:
    deadline = time.monotonic() + MATRIX_ACK_TIMEOUT_SECONDS
    latest: list[tuple[Any, Any]] = []
    while time.monotonic() < deadline:
        latest = await _load_matrix_rows(job_ids)
        report = _matrix_report(latest, lanes=lanes, max_outstanding=max_outstanding)
        if report["passed"]:
            return report
        await asyncio.sleep(0.5)
    latest = await _load_matrix_rows(job_ids)
    return _matrix_report(latest, lanes=lanes, max_outstanding=max_outstanding)


async def _run_simulated_user_interactions(
    *, run_id: str, interaction_offsets_seconds: Sequence[float], started_at: float
) -> dict[str, Any]:
    # Imports are local so the B2B driver stays independent from the broad
    # synthetic-market harness except for the explicitly requested simulation.
    from scripts import trading_core_probe_worker as worker

    prefix = f"{run_id}-interaction-"
    users = []
    harness = None
    outcomes: list[str] = []
    cleanup_ok = False
    try:
        await worker.cleanup_prefix(prefix)
        users = await worker.create_load_fixture_users(
            prefix, user_count=max(3, len(interaction_offsets_seconds))
        )
        harness = worker.AiogramDispatcherHarness()
        async with worker.patched_trading_boundaries():
            for index, offset in enumerate(interaction_offsets_seconds):
                delay = started_at + float(offset) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                user = users[index % len(users)]
                outcomes.append(
                    await worker.execute_bot_market_view_with_dispatcher(
                        harness=harness,
                        user=user,
                    )
                )
    finally:
        if harness is not None:
            await harness.close()
        cleanup = await worker.cleanup_prefix(prefix)
        cleanup_ok = str(cleanup.get("status") or "") == "ok"
    return {
        "expected": MATRIX_INTERACTIONS,
        "completed": len(outcomes),
        "successful": sum(value == "success" for value in outcomes),
        "cleanup_terminal": cleanup_ok,
        "passed": len(outcomes) == MATRIX_INTERACTIONS
        and all(value == "success" for value in outcomes)
        and cleanup_ok,
    }


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise B2BHarnessError("b2b_matrix_live_confirmation_required")
    run_id = args.run_id or _new_run_id().replace("b2b-light-", "b2b-matrix-", 1)
    if not run_id.startswith("b2b-matrix-"):
        raise B2BHarnessError("b2b_matrix_run_id_invalid")
    channel_id, lanes = _require_live_configuration()
    workload = build_matrix_workload(
        lanes=lanes,
        total_commands=args.total_commands,
        interaction_count=args.simulated_user_interactions,
        ingress_interval_seconds=args.ingress_interval_seconds,
    )
    configured_interval = float(
        getattr(settings, "telegram_b2b_dispatch_interval_seconds", 0.0)
    )
    if configured_interval > MATRIX_INGRESS_INTERVAL_SECONDS:
        raise B2BHarnessError("b2b_matrix_dispatcher_cadence_below_two_per_second")
    await _assert_quiet_outbox()
    job_ids: list[int] = []
    max_outstanding = 0
    interaction_task: asyncio.Task[dict[str, Any]] | None = None
    b2b_report: dict[str, Any] | None = None
    interaction_report: dict[str, Any] | None = None
    started_at = time.monotonic()
    cleanup_ok = False
    try:
        interaction_task = asyncio.create_task(
            _run_simulated_user_interactions(
                run_id=run_id,
                interaction_offsets_seconds=workload.interaction_offsets_seconds,
                started_at=started_at,
            ),
            name="telegram-b2b-matrix-user-interactions",
        )
        lane_ordinals: Counter[str] = Counter()
        for index, lane in enumerate(workload.lane_sequence):
            lane_ordinals[lane] += 1
            job_ids.append(
                await _create_matrix_job(
                    run_id=run_id,
                    lane=lane,
                    lane_ordinal=lane_ordinals[lane],
                    channel_id=channel_id,
                )
            )
            if (index + 1) % 20 == 0:
                max_outstanding = max(
                    max_outstanding, await _active_matrix_commands(job_ids)
                )
            if interaction_task.done():
                interaction_report = interaction_task.result()
                if not interaction_report["passed"]:
                    raise B2BHarnessError("b2b_matrix_user_interaction_failed")
            due_at = started_at + (index + 1) * MATRIX_INGRESS_INTERVAL_SECONDS
            delay = due_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        max_outstanding = max(max_outstanding, await _active_matrix_commands(job_ids))
        interaction_report = await interaction_task
        b2b_report = await _wait_for_matrix_acknowledgements(
            job_ids=job_ids,
            lanes=lanes,
            max_outstanding=max_outstanding,
        )
    finally:
        if interaction_task is not None and not interaction_task.done():
            interaction_task.cancel()
            await asyncio.gather(interaction_task, return_exceptions=True)
        cleanup_ok = await _terminalize_probe_jobs(job_ids) if job_ids else True
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "ingress_interval_seconds": MATRIX_INGRESS_INTERVAL_SECONDS,
        "configured_dispatch_interval_seconds": configured_interval,
        "b2b": b2b_report or {"passed": False},
        "simulated_user_interactions": interaction_report or {"passed": False},
        "cleanup_terminal": cleanup_ok,
    }
    report["passed"] = bool(
        bool(b2b_report and b2b_report["passed"])
        and bool(interaction_report and interaction_report["passed"])
        and cleanup_ok
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the 1000-command Telegram publisher B2B staging matrix."
    )
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--total-commands", type=int, default=MATRIX_TOTAL_COMMANDS)
    parser.add_argument(
        "--simulated-user-interactions", type=int, default=MATRIX_INTERACTIONS
    )
    parser.add_argument(
        "--ingress-interval-seconds",
        type=float,
        default=MATRIX_INGRESS_INTERVAL_SECONDS,
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        report = asyncio.run(run_matrix(args))
    except B2BHarnessError as exc:
        print(json.dumps({"passed": False, "reason": str(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"passed": False, "error_type": type(exc).__name__}, sort_keys=True
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
