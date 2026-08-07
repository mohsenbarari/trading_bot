#!/usr/bin/env python3
"""Sample Telegram delivery queue depth and send latency for combined-matrix."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select

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


async def _sample(prefix: str | None, *, lookback_minutes: int, timing: bool) -> dict[str, object]:
    _guard()
    from core.telegram_delivery_queue_contract import TelegramDeliveryState
    from models.telegram_delivery_job import TelegramDeliveryJobRecord

    async with AsyncSessionLocal() as session:
        pending_states = (
            TelegramDeliveryState.PENDING,
            TelegramDeliveryState.PENDING_RETRY,
            TelegramDeliveryState.LEASED,
            TelegramDeliveryState.PENDING_RECONCILE,
        )
        pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TelegramDeliveryJobRecord)
                    .where(TelegramDeliveryJobRecord.state.in_(pending_states))
                )
            ).scalar_one()
        )
        total = int(
            (
                await session.execute(select(func.count()).select_from(TelegramDeliveryJobRecord))
            ).scalar_one()
        )
        prefix_jobs = 0
        if prefix:
            prefix_jobs = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TelegramDeliveryJobRecord)
                        .where(TelegramDeliveryJobRecord.source_natural_id.like(f"%{prefix}%"))
                    )
                ).scalar_one()
            )

        timing_payload: dict[str, object] = {}
        if timing:
            since = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(lookback_minutes)))
            rows = (
                await session.execute(
                    select(
                        TelegramDeliveryJobRecord.created_at,
                        TelegramDeliveryJobRecord.sent_at,
                        TelegramDeliveryJobRecord.state,
                        TelegramDeliveryJobRecord.action_kind,
                    ).where(
                        TelegramDeliveryJobRecord.created_at >= since,
                        TelegramDeliveryJobRecord.sent_at.is_not(None),
                    )
                )
            ).all()
            latencies: list[float] = []
            by_minute: dict[str, list[float]] = {}
            for created_at, sent_at, _state, action_kind in rows:
                if created_at is None or sent_at is None:
                    continue
                created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
                sent = sent_at if sent_at.tzinfo else sent_at.replace(tzinfo=timezone.utc)
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
            best_minute = None
            if minute_means:
                best_minute = min(minute_means.items(), key=lambda item: item[1])
            timing_payload = {
                "lookback_minutes": lookback_minutes,
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
                "per_minute_mean_latency_sample": dict(list(minute_means.items())[:40]),
            }

    return {
        "ok": True,
        "at_utc": _utc(),
        "server_mode": getattr(settings, "server_mode", None),
        "pending_jobs": pending,
        "total_jobs": total,
        "prefix": prefix,
        "prefix_jobs": prefix_jobs,
        "backlog_under_threshold": pending <= 50,
        "timing": timing_payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", default=None)
    parser.add_argument("--timing", action="store_true")
    parser.add_argument("--lookback-minutes", type=int, default=45)
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(
            _sample(args.run_prefix, lookback_minutes=args.lookback_minutes, timing=bool(args.timing))
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
