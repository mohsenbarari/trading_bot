#!/usr/bin/env python3
"""Run a bounded, channel-safe live probe of the publisher B2B transport.

The probe uses real durable delivery jobs and the normal central/publisher
Telegram routes, but every synthetic job is held past its own freshness
deadline.  Consequently a process crash cannot turn this transport probe into
a channel post: the queue will supersede the missing synthetic offer before a
provider call is allowed.  A successful run terminalizes the jobs immediately.

This script intentionally reads all Telegram credentials only through the
already-running environment.  It neither accepts nor prints credentials,
Telegram IDs, message contents, nor API responses.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import sys
import time
from typing import Any, Sequence

from sqlalchemy import func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
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
from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_IDENTITIES,
    TelegramPublisherDispatchState,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_publisher_dispatch_command import TelegramPublisherDispatchCommand


_RUN_ID = re.compile(r"^b2b-light-[a-z0-9-]{12,96}$")
_FINAL_COMMAND_STATES = frozenset(
    {
        TelegramPublisherDispatchState.ACKNOWLEDGED.value,
        TelegramPublisherDispatchState.FAILED.value,
        TelegramPublisherDispatchState.SUPERSEDED.value,
    }
)
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


class B2BHarnessError(RuntimeError):
    """Raised when the live B2B probe cannot satisfy its safety contract."""


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _positive_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise B2BHarnessError(reason)
    return value


def _nonzero_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise B2BHarnessError(reason)
    return value


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower()
    return f"b2b-light-{stamp}-{secrets.token_hex(6)}"


def _require_live_configuration() -> tuple[int, tuple[str, ...]]:
    if current_server() != SERVER_FOREIGN:
        raise B2BHarnessError("b2b_harness_requires_foreign_execution_server")
    if not bool(getattr(settings, "telegram_multi_publisher_enabled", False)):
        raise B2BHarnessError("b2b_harness_multi_publisher_disabled")
    if not bool(getattr(settings, "telegram_b2b_dispatch_enabled", False)):
        raise B2BHarnessError("b2b_harness_dispatch_disabled")
    _positive_int(
        getattr(settings, "telegram_delivery_queue_expected_primary_bot_id", None),
        reason="b2b_harness_expected_primary_missing",
    )
    channel_id = _nonzero_int(
        getattr(settings, "channel_id", None),
        reason="b2b_harness_channel_missing",
    )
    expected_channel = _nonzero_int(
        getattr(settings, "telegram_delivery_queue_expected_channel_id", None),
        reason="b2b_harness_expected_channel_missing",
    )
    if channel_id != expected_channel:
        raise B2BHarnessError("b2b_harness_channel_identity_mismatch")
    lanes: list[str] = []
    for index, identity in enumerate(TELEGRAM_PUBLISHER_IDENTITIES, start=1):
        if not bool(getattr(settings, f"telegram_publisher_{index}_enabled", False)):
            raise B2BHarnessError("b2b_harness_publisher_lane_disabled")
        _positive_int(
            getattr(settings, f"telegram_publisher_{index}_expected_bot_id", None),
            reason="b2b_harness_publisher_identity_missing",
        )
        if not str(
            getattr(settings, f"telegram_publisher_{index}_expected_username", "")
            or ""
        ).strip():
            raise B2BHarnessError("b2b_harness_publisher_username_missing")
        lanes.append(identity)
    return channel_id, tuple(lanes)


async def _assert_quiet_outbox() -> None:
    async with AsyncSessionLocal() as db:
        active = int(
            (
                await db.execute(
                    select(func.count(TelegramPublisherDispatchCommand.id)).where(
                        TelegramPublisherDispatchCommand.state.notin_(
                            tuple(_FINAL_COMMAND_STATES)
                        )
                    )
                )
            ).scalar_one()
            or 0
        )
    if active:
        raise B2BHarnessError("b2b_harness_active_outbox_not_empty")


async def _create_probe_jobs(
    *,
    run_id: str,
    lanes: Sequence[str],
    channel_id: int,
    messages_per_lane: int,
    guard_delay_seconds: float,
    freshness_window_seconds: float,
) -> tuple[int, ...]:
    now = utc_now()
    eligible_at = now + timedelta(seconds=guard_delay_seconds)
    freshness_deadline_at = now + timedelta(seconds=freshness_window_seconds)
    job_ids: list[int] = []
    try:
        async with AsyncSessionLocal() as db:
            for identity in lanes:
                for ordinal in range(1, messages_per_lane + 1):
                    source_id = f"{run_id}:{identity}:{ordinal}"
                    result = await enqueue_telegram_delivery_job(
                        db,
                        current_server=SERVER_FOREIGN,
                        feeder=TelegramFeederKind.OFFER_CONTROL,
                        source_natural_id=source_id,
                        source_version=1,
                        action=TelegramDeliveryAction.OFFER_PUBLISH,
                        bot_identity=identity,
                        destination_key=telegram_channel_destination_key(channel_id),
                        destination_class=TelegramDestinationClass.CHANNEL,
                        method="sendMessage",
                        # This payload must be syntactically valid for the durable
                        # queue but can never reach Telegram: the source offer does
                        # not exist, and eligibility is after the freshness deadline.
                        payload={
                            "chat_id": channel_id,
                            "text": f"b2b transport probe {run_id} {identity} {ordinal}",
                        },
                        template_version="b2b-harness-v1",
                        eligible_at=eligible_at,
                        freshness_deadline_at=freshness_deadline_at,
                        run_id=run_id,
                    )
                    if not result.created:
                        raise B2BHarnessError("b2b_harness_job_identity_collision")
                    command = await get_or_create_telegram_publisher_dispatch_command(
                        db,
                        current_server=SERVER_FOREIGN,
                        job=result.job,
                        publisher_bot_identity=identity,
                        now=now,
                    )
                    if _enum_value(command.state) != TelegramPublisherDispatchState.PENDING.value:
                        raise B2BHarnessError("b2b_harness_command_not_pending")
                    job_ids.append(int(result.job.id))
            await db.commit()
    except BaseException:
        # The transaction has not made partial fixtures visible on an exception.
        raise
    return tuple(job_ids)


async def _load_probe_rows(job_ids: Sequence[int]) -> list[tuple[Any, Any]]:
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


def _report_rows(rows: Sequence[tuple[Any, Any]], *, lanes: Sequence[str]) -> dict[str, Any]:
    by_lane = Counter(_enum_value(command.publisher_bot_identity) for _, command in rows)
    states = Counter(_enum_value(command.state) for _, command in rows)
    attempts = [int(command.attempt_count or 0) for _, command in rows]
    acknowledgement_lags: list[int] = []
    for _, command in rows:
        created_at = getattr(command, "created_at", None)
        acknowledged_at = getattr(command, "acknowledged_at", None)
        if isinstance(created_at, datetime) and isinstance(acknowledged_at, datetime):
            acknowledgement_lags.append(
                max(0, int((acknowledged_at - created_at).total_seconds() * 1000))
            )
    all_acknowledged = (
        len(rows) == len(lanes) * 2
        and all(
            _enum_value(command.state)
            == TelegramPublisherDispatchState.ACKNOWLEDGED.value
            for _, command in rows
        )
    )
    no_duplicate_dispatch = bool(attempts) and all(value == 1 for value in attempts)
    all_receipts_match = all(
        int(command.receipt_sequence or 0) == int(job.enqueued_seq or 0)
        and getattr(command, "receipt_received_at", None) is not None
        for job, command in rows
    )
    channel_provider_attempts = sum(
        int(getattr(job, "provider_attempt_count", 0) or 0)
        for job, _ in rows
    )
    no_channel_provider_attempt = channel_provider_attempts == 0 and all(
        getattr(job, "dispatch_started_at", None) is None for job, _ in rows
    )
    return {
        "commands_expected": len(lanes) * 2,
        "commands_observed": len(rows),
        "acknowledged": states.get(TelegramPublisherDispatchState.ACKNOWLEDGED.value, 0),
        "per_lane_count_valid": all(by_lane.get(lane, 0) == 2 for lane in lanes),
        "no_duplicate_dispatch": no_duplicate_dispatch,
        "receipt_sequence_valid": all_receipts_match,
        "channel_provider_attempts": channel_provider_attempts,
        "ack_lag_ms": {
            "max": max(acknowledgement_lags, default=0),
            "min": min(acknowledgement_lags, default=0),
        },
        "passed": all_acknowledged
        and no_duplicate_dispatch
        and all_receipts_match
        and no_channel_provider_attempt
        and all(by_lane.get(lane, 0) == 2 for lane in lanes),
    }


async def _wait_for_acknowledgements(
    *, job_ids: Sequence[int], lanes: Sequence[str], timeout_seconds: float
) -> tuple[list[tuple[Any, Any]], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    latest: list[tuple[Any, Any]] = []
    while time.monotonic() < deadline:
        latest = await _load_probe_rows(job_ids)
        report = _report_rows(latest, lanes=lanes)
        if report["passed"]:
            return latest, report
        await asyncio.sleep(0.5)
    latest = await _load_probe_rows(job_ids)
    return latest, _report_rows(latest, lanes=lanes)


async def _terminalize_probe_jobs(job_ids: Sequence[int]) -> bool:
    current_time = utc_now()
    async with AsyncSessionLocal() as db:
        rows = list(
            (
                await db.execute(
                    select(TelegramDeliveryJobRecord, TelegramPublisherDispatchCommand)
                    .join(
                        TelegramPublisherDispatchCommand,
                        TelegramPublisherDispatchCommand.job_id
                        == TelegramDeliveryJobRecord.id,
                    )
                    .where(TelegramDeliveryJobRecord.id.in_(tuple(job_ids)))
                    .with_for_update()
                )
            ).all()
        )
        if len(rows) != len(job_ids):
            raise B2BHarnessError("b2b_harness_cleanup_fixture_missing")
        for job, command in rows:
            if _enum_value(job.state) not in _FINAL_JOB_STATES:
                job.state = TelegramDeliveryState.SUPERSEDED
                job.outcome_reason = "b2b_harness_cleanup"
                job.terminal_at = current_time
                job.next_retry_at = None
                job.updated_at = current_time
            if _enum_value(command.state) not in _FINAL_COMMAND_STATES:
                command.state = TelegramPublisherDispatchState.SUPERSEDED.value
                command.next_retry_at = None
                command.lease_until = None
                command.last_error_class = "B2BHarnessCleanup"
                command.last_error_message = "b2b_harness_cleanup"
                command.updated_at = current_time
        await db.commit()
    rows = await _load_probe_rows(job_ids)
    return bool(rows) and all(
        _enum_value(job.state) in _FINAL_JOB_STATES
        and _enum_value(command.state) in _FINAL_COMMAND_STATES
        for job, command in rows
    )


async def run_live_probe(args: argparse.Namespace) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise B2BHarnessError("b2b_harness_live_confirmation_required")
    run_id = args.run_id or _new_run_id()
    if not _RUN_ID.fullmatch(run_id):
        raise B2BHarnessError("b2b_harness_run_id_invalid")
    if args.messages_per_lane != 2:
        raise B2BHarnessError("b2b_harness_light_shape_requires_two_per_lane")
    if args.guard_delay_seconds <= args.freshness_window_seconds:
        raise B2BHarnessError("b2b_harness_guard_must_exceed_freshness_window")
    channel_id, lanes = _require_live_configuration()
    await _assert_quiet_outbox()
    job_ids = await _create_probe_jobs(
        run_id=run_id,
        lanes=lanes,
        channel_id=channel_id,
        messages_per_lane=args.messages_per_lane,
        guard_delay_seconds=args.guard_delay_seconds,
        freshness_window_seconds=args.freshness_window_seconds,
    )
    report: dict[str, Any]
    cleanup_ok = False
    try:
        _rows, report = await _wait_for_acknowledgements(
            job_ids=job_ids,
            lanes=lanes,
            timeout_seconds=args.timeout_seconds,
        )
    finally:
        cleanup_ok = await _terminalize_probe_jobs(job_ids)
    report.update(
        {
            "schema_version": 1,
            "run_id": run_id,
            "cleanup_terminal": cleanup_ok,
            "channel_posts_attempted": report["channel_provider_attempts"],
        }
    )
    report["passed"] = bool(report["passed"] and cleanup_ok)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded live Telegram publisher B2B transport probe."
    )
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--messages-per-lane", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--freshness-window-seconds", type=float, default=120.0)
    parser.add_argument("--guard-delay-seconds", type=float, default=300.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.timeout_seconds <= 0 or args.freshness_window_seconds <= 0 or args.guard_delay_seconds <= 0:
        raise SystemExit("b2b_harness_positive_timeouts_required")
    try:
        report = asyncio.run(run_live_probe(args))
    except B2BHarnessError as exc:
        print(json.dumps({"passed": False, "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
