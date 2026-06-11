#!/usr/bin/env python3
"""Small in-container helpers for the production cross-server sync benchmark."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis
from sqlalchemy import delete, func, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.events import setup_event_listeners
from core.utils import utc_now
from models.change_log import ChangeLog
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_safe))


def parse_probe_date(value: str) -> date:
    return date.fromisoformat(value)


async def health(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count(ChangeLog.id), func.min(ChangeLog.created_at)).where(ChangeLog.synced == False)
        )
        unsynced_count, oldest = result.one()
        by_table_rows = await db.execute(
            select(ChangeLog.table_name, func.count(ChangeLog.id))
            .where(ChangeLog.synced == False)
            .group_by(ChangeLog.table_name)
            .order_by(ChangeLog.table_name)
        )
        unsynced_by_table = {table: int(count or 0) for table, count in by_table_rows.all()}
    redis_client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=0, decode_responses=True)
    try:
        outbound = int(await redis_client.llen("sync:outbound") or 0)
        retry = int(await redis_client.llen("sync:retry") or 0)
        redis_ok = True
    except Exception:
        outbound = 0
        retry = 0
        redis_ok = False
    finally:
        await redis_client.aclose()
    print_json(
        {
            "status": "ok",
            "server_mode": settings.server_mode,
            "unsynced_change_log_count": int(unsynced_count or 0),
            "oldest_unsynced_at": oldest,
            "unsynced_by_table": unsynced_by_table,
            "redis_ok": redis_ok,
            "redis_queues": {"sync:outbound": outbound, "sync:retry": retry},
        }
    )
    return 0


async def insert_probe(args: argparse.Namespace) -> int:
    setup_event_listeners()
    probe_date = parse_probe_date(args.date)
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(MarketScheduleOverride).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        await db.commit()
        row = MarketScheduleOverride(
            date=probe_date,
            override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
            note=args.note,
            created_by_user_id=None,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        print_json({"status": "ok", "id": row.id, "date": row.date, "note": row.note})
    return 0


async def delete_probe(args: argparse.Namespace) -> int:
    setup_event_listeners()
    probe_date = parse_probe_date(args.date)
    deleted_ids: list[int] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MarketScheduleOverride).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            deleted_ids.append(int(row.id))
            await db.delete(row)
        await db.commit()
    print_json({"status": "ok", "deleted_count": len(deleted_ids), "deleted_ids": deleted_ids})
    return 0


async def exists_probe(args: argparse.Namespace) -> int:
    probe_date = parse_probe_date(args.date)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MarketScheduleOverride.id).where(
                MarketScheduleOverride.date == probe_date,
                MarketScheduleOverride.note == args.note,
            )
        )
        ids = [int(item) for item in result.scalars().all()]
    print_json({"status": "ok", "exists": bool(ids), "count": len(ids), "ids": ids})
    return 0


async def cleanup_probes(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        row_result = await db.execute(
            text("DELETE FROM market_schedule_overrides WHERE note LIKE :pattern"),
            {"pattern": f"{args.note_prefix}%"},
        )
        invalid_result = await db.execute(
            text("DELETE FROM change_log WHERE hash LIKE :pattern"),
            {"pattern": f"p6_invalid_{args.stamp}%"},
        )
        await db.commit()
    print_json(
        {
            "status": "ok",
            "deleted_probe_rows": int(row_result.rowcount or 0),
            "deleted_invalid_change_logs": int(invalid_result.rowcount or 0),
        }
    )
    return 0


async def insert_invalid_change_log(args: argparse.Namespace) -> int:
    record_id = int(args.record_id)
    payload = {
        "id": record_id,
        "chat_id": record_id,
        "user_id": record_id,
        "role": "member",
        "membership_status": "ACTIVE",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    entry = ChangeLog(
        operation="INSERT",
        table_name="chat_members",
        record_id=record_id,
        data=payload,
        timestamp=utc_now(),
        hash=f"p6_invalid_{args.stamp}_{digest[:16]}",
        synced=False,
        verified=False,
    )
    async with AsyncSessionLocal() as db:
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
    print_json({"status": "ok", "change_log_id": entry.id, "record_id": record_id, "hash": entry.hash})
    return 0


async def change_log_status(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeLog).where(ChangeLog.id == int(args.change_log_id)))
        entry = result.scalar_one_or_none()
    print_json(
        {
            "status": "ok",
            "exists": entry is not None,
            "change_log_id": int(args.change_log_id),
            "synced": bool(entry.synced) if entry else None,
            "verified": bool(entry.verified) if entry else None,
            "table_name": entry.table_name if entry else None,
        }
    )
    return 0


async def delete_change_log(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(ChangeLog).where(ChangeLog.id == int(args.change_log_id)))
        await db.commit()
    print_json({"status": "ok", "deleted_count": int(result.rowcount or 0)})
    return 0


async def resync_table(args: argparse.Namespace) -> int:
    if not settings.dev_api_key:
        print_json({"status": "error", "message": "DEV_API_KEY is not configured"})
        return 2
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        response = await client.post(
            "http://127.0.0.1:8000/api/sync/resync",
            params={"limit": args.limit, "table_filter": args.table},
            headers={"X-Dev-Api-Key": settings.dev_api_key},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "invalid-json", "body_sha256": hashlib.sha256(response.content).hexdigest()[:16]}
    print_json({"status_code": response.status_code, "payload": payload})
    return 0 if response.status_code == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one cross-server sync benchmark helper inside an app container.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")

    insert_parser = subparsers.add_parser("insert-probe")
    insert_parser.add_argument("--date", required=True)
    insert_parser.add_argument("--note", required=True)

    delete_parser = subparsers.add_parser("delete-probe")
    delete_parser.add_argument("--date", required=True)
    delete_parser.add_argument("--note", required=True)

    exists_parser = subparsers.add_parser("exists-probe")
    exists_parser.add_argument("--date", required=True)
    exists_parser.add_argument("--note", required=True)

    cleanup_parser = subparsers.add_parser("cleanup-probes")
    cleanup_parser.add_argument("--note-prefix", required=True)
    cleanup_parser.add_argument("--stamp", required=True)

    invalid_parser = subparsers.add_parser("insert-invalid-change-log")
    invalid_parser.add_argument("--stamp", required=True)
    invalid_parser.add_argument("--record-id", required=True)

    status_parser = subparsers.add_parser("change-log-status")
    status_parser.add_argument("--change-log-id", required=True)

    delete_log_parser = subparsers.add_parser("delete-change-log")
    delete_log_parser.add_argument("--change-log-id", required=True)

    resync_parser = subparsers.add_parser("resync-table")
    resync_parser.add_argument("--table", required=True)
    resync_parser.add_argument("--limit", type=int, default=100)
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    handlers = {
        "health": health,
        "insert-probe": insert_probe,
        "delete-probe": delete_probe,
        "exists-probe": exists_probe,
        "cleanup-probes": cleanup_probes,
        "insert-invalid-change-log": insert_invalid_change_log,
        "change-log-status": change_log_status,
        "delete-change-log": delete_change_log,
        "resync-table": resync_table,
    }
    return await handlers[args.command](args)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
