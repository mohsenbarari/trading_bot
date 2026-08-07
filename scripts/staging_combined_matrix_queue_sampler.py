#!/usr/bin/env python3
"""Sample global and run-scoped Telegram queue evidence for combined-matrix."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import Text, cast, func, or_, select

from core.config import settings
from core.db import AsyncSessionLocal


class DriverRefusal(RuntimeError):
    pass


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _guard() -> None:
    environment = (getattr(settings, "environment", "") or "").strip().lower()
    if environment != "staging":
        raise DriverRefusal(f"refuses non-staging environment={environment!r}")


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 3)
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return round(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight, 3)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _timing_payload(
    rows: list[tuple[object, ...]], *, since: datetime
) -> dict[str, object]:
    latencies: list[float] = []
    by_minute: dict[str, list[float]] = {}
    for _job_id, created_at, sent_at, _state, _action_kind, *_rest in rows:
        if created_at is None or sent_at is None:
            continue
        created = _aware(created_at)
        sent = _aware(sent_at)
        latency = (sent - created).total_seconds()
        if latency < 0:
            continue
        latencies.append(latency)
        bucket = sent.replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
        by_minute.setdefault(bucket, []).append(latency)
    latencies.sort()
    minute_means = {
        minute: round(sum(values) / len(values), 3)
        for minute, values in sorted(by_minute.items())
        if values
    }
    best_minute = min(minute_means.items(), key=lambda item: item[1]) if minute_means else None
    return {
        "since_utc": since.isoformat().replace("+00:00", "Z"),
        "sent_sample_count": len(latencies),
        "latency_seconds": {
            "p50": _percentile(latencies, 50),
            "p90": _percentile(latencies, 90),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(latencies[-1], 3) if latencies else None,
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
        },
        "best_send_minute_utc": best_minute[0] if best_minute else None,
        "best_send_minute_mean_latency_seconds": best_minute[1] if best_minute else None,
        "per_minute_mean_latency_sample": dict(list(minute_means.items())[:80]),
    }


def _provider_timing_payload(
    rows: list[tuple[object, ...]], *, slow_edit_threshold_seconds: float
) -> dict[str, object]:
    latencies: list[float] = []
    edit_latencies: list[float] = []
    for row in rows:
        sent_at = row[2]
        dispatch_started_at = row[9] if len(row) > 9 else None
        method = str(row[10] or "") if len(row) > 10 else ""
        provider_response = row[12] if len(row) > 12 else None
        raw_latency_ms = (
            provider_response.get("_provider_latency_ms")
            if isinstance(provider_response, dict)
            else None
        )
        if isinstance(raw_latency_ms, (int, float)):
            latency = float(raw_latency_ms) / 1000.0
        elif sent_at is not None and dispatch_started_at is not None:
            # Compatibility fallback for rows created before exact monotonic
            # provider latency was persisted.
            latency = (
                _aware(sent_at) - _aware(dispatch_started_at)
            ).total_seconds()
        else:
            continue
        if latency < 0:
            continue
        latencies.append(latency)
        if method in {"editMessageText", "editMessageReplyMarkup"}:
            edit_latencies.append(latency)
    latencies.sort()
    edit_latencies.sort()
    return {
        "sample_count": len(latencies),
        "latency_seconds": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(latencies[-1], 3) if latencies else None,
        },
        "edit_sample_count": len(edit_latencies),
        "edit_latency_seconds": {
            "p50": _percentile(edit_latencies, 50),
            "p95": _percentile(edit_latencies, 95),
            "max": round(edit_latencies[-1], 3) if edit_latencies else None,
        },
        "slow_edit_threshold_seconds": float(slow_edit_threshold_seconds),
        "slow_edit_count": sum(
            value >= float(slow_edit_threshold_seconds)
            for value in edit_latencies
        ),
    }


def _parse_since(value: str | None, *, lookback_minutes: int) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return _aware(parsed)
    return datetime.now(timezone.utc) - timedelta(minutes=max(1, int(lookback_minutes)))


async def _sample(
    prefix: str | None,
    *,
    lookback_minutes: int,
    timing: bool,
    since_utc: str | None = None,
) -> dict[str, object]:
    _guard()
    from core.telegram_delivery_queue_contract import TelegramDeliveryState
    from core.telegram_delivery_trade_result_binding import (
        trade_result_queue_job_id_from_receipt,
    )
    from models.offer import Offer
    from models.telegram_delivery_job import TelegramDeliveryJobRecord
    from models.trade import Trade
    from models.trade_delivery_receipt import TradeDeliveryReceipt

    async with AsyncSessionLocal() as session:
        pending_states = (
            TelegramDeliveryState.PENDING,
            TelegramDeliveryState.PENDING_RETRY,
            TelegramDeliveryState.LEASED,
            TelegramDeliveryState.PENDING_RECONCILE,
        )
        global_pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TelegramDeliveryJobRecord)
                    .where(TelegramDeliveryJobRecord.state.in_(pending_states))
                )
            ).scalar_one()
        )
        global_total = int(
            (
                await session.execute(select(func.count()).select_from(TelegramDeliveryJobRecord))
            ).scalar_one()
        )
        scoped_rows: list[tuple[object, ...]] = []
        scoped_offer_db_ids: list[int] = []
        scoped_offer_ids: list[str] = []
        scoped_private_job_ids: list[int] = []
        if prefix:
            offer_rows = (
                await session.execute(
                    select(Offer.id, Offer.offer_public_id).where(
                            Offer.offer_public_id.is_not(None),
                            func.left(func.coalesce(Offer.notes, ""), len(prefix)) == prefix,
                    )
                )
            ).all()
            scoped_offer_db_ids = [int(row[0]) for row in offer_rows]
            scoped_offer_ids = [str(row[1]) for row in offer_rows]
        if scoped_offer_db_ids:
            scoped_trade_ids = [
                int(row[0])
                for row in (
                    await session.execute(
                        select(Trade.id).where(
                            Trade.offer_id.in_(scoped_offer_db_ids)
                        )
                    )
                ).all()
            ]
            receipt_rows = (
                await session.execute(
                    select(TradeDeliveryReceipt.worker_id).where(
                        or_(
                            TradeDeliveryReceipt.offer_id.in_(
                                scoped_offer_db_ids
                            ),
                            TradeDeliveryReceipt.trade_id.in_(
                                scoped_trade_ids or [-1]
                            ),
                        )
                    )
                )
            ).all()
            for (worker_id,) in receipt_rows:
                job_id = trade_result_queue_job_id_from_receipt(
                    SimpleNamespace(worker_id=worker_id)
                )
                if job_id is not None:
                    scoped_private_job_ids.append(job_id)
        if prefix or scoped_offer_ids or scoped_private_job_ids:
            scope_conditions = []
            if scoped_offer_ids:
                scope_conditions.append(
                    TelegramDeliveryJobRecord.source_natural_id.in_(
                        scoped_offer_ids
                    )
                )
            if scoped_private_job_ids:
                scope_conditions.append(
                    TelegramDeliveryJobRecord.id.in_(scoped_private_job_ids)
                )
            if prefix:
                scope_conditions.extend(
                    (
                        TelegramDeliveryJobRecord.source_natural_id.contains(
                            prefix
                        ),
                        TelegramDeliveryJobRecord.dedupe_key.contains(prefix),
                        TelegramDeliveryJobRecord.run_id == prefix,
                        cast(TelegramDeliveryJobRecord.payload, Text).contains(
                            prefix
                        ),
                    )
                )
            scoped_rows = list(
                (
                    await session.execute(
                        select(
                            TelegramDeliveryJobRecord.id,
                            TelegramDeliveryJobRecord.created_at,
                            TelegramDeliveryJobRecord.sent_at,
                            TelegramDeliveryJobRecord.state,
                            TelegramDeliveryJobRecord.action_kind,
                            TelegramDeliveryJobRecord.attempt_count,
                            TelegramDeliveryJobRecord.provider_attempt_count,
                            TelegramDeliveryJobRecord.last_rate_limited_at,
                            TelegramDeliveryJobRecord.outcome_reason,
                            TelegramDeliveryJobRecord.dispatch_started_at,
                            TelegramDeliveryJobRecord.method,
                            TelegramDeliveryJobRecord.source_natural_id,
                            TelegramDeliveryJobRecord.provider_response,
                        ).where(or_(*scope_conditions))
                    )
                ).all()
            )

        def _value(item: object) -> str:
            return str(getattr(item, "value", item) or "")

        pending_values = {item.value for item in pending_states}
        failure_values = {
            TelegramDeliveryState.AMBIGUOUS.value,
            TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value,
            TelegramDeliveryState.PERMANENT_UNDELIVERABLE.value,
            TelegramDeliveryState.TERMINAL_FAILED.value,
            TelegramDeliveryState.QUARANTINED.value,
            TelegramDeliveryState.BLOCKED_DESTINATION.value,
            TelegramDeliveryState.BLOCKED_BOT.value,
            TelegramDeliveryState.BLOCKED_GATEWAY.value,
        }
        state_counts: dict[str, int] = {}
        action_counts: dict[str, int] = {}
        sent_action_counts: dict[str, int] = {}
        sent_offer_public_ids: list[str] = []
        for row in scoped_rows:
            state = _value(row[3])
            action = _value(row[4])
            state_counts[state] = state_counts.get(state, 0) + 1
            action_counts[action] = action_counts.get(action, 0) + 1
            if row[2] is not None:
                sent_action_counts[action] = sent_action_counts.get(action, 0) + 1
                if action == "offer_publish" and len(row) > 11:
                    sent_offer_public_ids.append(str(row[11]))
        scoped_pending = sum(
            count for state, count in state_counts.items() if state in pending_values
        )
        scoped_failures = sum(
            count for state, count in state_counts.items() if state in failure_values
        )
        scoped_retried = sum(
            1
            for row in scoped_rows
            if int(row[5] or 0) > 1 or int(row[6] or 0) > 1
        )
        scoped_rate_limited = sum(1 for row in scoped_rows if row[7] is not None)
        since = _parse_since(since_utc, lookback_minutes=lookback_minutes)
        scoped_timing_rows = [
            row for row in scoped_rows if row[1] is not None and _aware(row[1]) >= since
        ]
        timing_payload = (
            _timing_payload(scoped_timing_rows, since=since) if timing else {}
        )
        provider_timing_payload = (
            _provider_timing_payload(
                scoped_timing_rows,
                slow_edit_threshold_seconds=float(
                    getattr(
                        settings,
                        "telegram_delivery_queue_edit_slow_response_seconds",
                        2.0,
                    )
                ),
            )
            if timing
            else {}
        )

        global_timing_payload: dict[str, object] = {}
        if timing:
            global_rows = list(
                (
                    await session.execute(
                        select(
                            TelegramDeliveryJobRecord.id,
                            TelegramDeliveryJobRecord.created_at,
                            TelegramDeliveryJobRecord.sent_at,
                            TelegramDeliveryJobRecord.state,
                            TelegramDeliveryJobRecord.action_kind,
                            TelegramDeliveryJobRecord.attempt_count,
                            TelegramDeliveryJobRecord.provider_attempt_count,
                            TelegramDeliveryJobRecord.last_rate_limited_at,
                            TelegramDeliveryJobRecord.outcome_reason,
                            TelegramDeliveryJobRecord.dispatch_started_at,
                            TelegramDeliveryJobRecord.method,
                            TelegramDeliveryJobRecord.source_natural_id,
                            TelegramDeliveryJobRecord.provider_response,
                        ).where(TelegramDeliveryJobRecord.created_at >= since)
                    )
                ).all()
            )
            global_timing_payload = _timing_payload(global_rows, since=since)

    return {
        "ok": True,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "prefix": prefix,
        "scoped": {
            "offer_count": len(scoped_offer_ids),
            "offer_public_ids": scoped_offer_ids,
            "private_trade_job_ids": sorted(set(scoped_private_job_ids)),
            "job_count": len(scoped_rows),
            "job_ids": [int(row[0]) for row in scoped_rows],
            "pending_jobs": scoped_pending,
            "sent_jobs": sum(1 for row in scoped_rows if row[2] is not None),
            "failed_jobs": scoped_failures,
            "retried_jobs": scoped_retried,
            "rate_limited_jobs": scoped_rate_limited,
            "state_counts": dict(sorted(state_counts.items())),
            "action_counts": dict(sorted(action_counts.items())),
            "sent_action_counts": dict(sorted(sent_action_counts.items())),
            "sent_offer_public_ids": sorted(set(sent_offer_public_ids)),
            "timing": timing_payload,
            "provider_timing": provider_timing_payload,
        },
        "global": {
            "pending_jobs": global_pending,
            "total_jobs": global_total,
            "timing": global_timing_payload,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", default=None)
    parser.add_argument("--timing", action="store_true")
    parser.add_argument("--lookback-minutes", type=int, default=45)
    parser.add_argument(
        "--since-utc",
        default=None,
        help="ISO-8601 lower bound for timing; preferred over rolling lookback",
    )
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(
            _sample(
                args.run_prefix,
                lookback_minutes=args.lookback_minutes,
                timing=bool(args.timing),
                since_utc=args.since_utc,
            )
        )
    except DriverRefusal as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
