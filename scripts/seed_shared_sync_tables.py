#!/usr/bin/env python3
"""Seed current shared-table state to the peer sync endpoint.

This is for initial Iran host migrations where the peer database starts empty.
It sends current rows, not historical change_log entries, so old intermediate
unique-key states cannot block the seed.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import hashlib
import hmac
import json
import sys
import time
import uuid
from datetime import date, datetime, time as time_type
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.sync import TABLE_ORDER, get_model_class
from core.config import settings
from core.db import AsyncSessionLocal
from core.server_routing import default_peer_server_url, peer_server_url_for
from core.sync_transport import assert_runtime_sync_transport_allowed, runtime_sync_tls_verify_setting
from core.sync_registry import SyncPolicy, get_sync_registry_entry


DEFAULT_TABLES = tuple(
    table
    for table, _order in sorted(TABLE_ORDER.items(), key=lambda item: item[1])
    if (entry := get_sync_registry_entry(table)) is not None and entry.policy == SyncPolicy.SYNC
)

SKIP_COLUMNS_BY_TABLE = {
    "users": {"avatar_file_id"},
    "chats": {"avatar_file_id", "last_message_id", "pinned_message_id"},
    "chat_members": {"last_read_message_id"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed current shared sync tables to the peer server.")
    parser.add_argument("--target-server", choices=("foreign", "iran"), help="Resolve target URL by server name.")
    parser.add_argument("--target-url", help="Explicit peer base URL. Overrides --target-server/default peer.")
    parser.add_argument("--table", action="append", choices=DEFAULT_TABLES, help="Limit to one table. Repeatable.")
    parser.add_argument("--batch-size", type=int, default=100, help="Rows per /api/sync/receive request.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without sending data.")
    return parser.parse_args()


def json_safe(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date, time_type)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def row_payload(table_name: str, row: Any) -> dict[str, Any]:
    skip_columns = SKIP_COLUMNS_BY_TABLE.get(table_name, set())
    payload: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name in skip_columns:
            continue
        payload[column.name] = json_safe(getattr(row, column.name))
    return payload


def record_id_for(row: Any) -> Any:
    if hasattr(row, "id"):
        return getattr(row, "id")
    if hasattr(row, "key"):
        return getattr(row, "key")
    raise ValueError(f"Cannot resolve record id for {row!r}")


def build_signed_headers(api_key: str, body: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(api_key.encode(), f"{timestamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


async def load_table_rows(table_name: str) -> list[Any]:
    model = get_model_class(table_name)
    if model is None:
        raise ValueError(f"Unknown sync table: {table_name}")

    primary_key_columns = list(model.__table__.primary_key.columns)
    order_columns = primary_key_columns or [next(iter(model.__table__.columns))]
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(model).order_by(*order_columns))
        return list(result.scalars().all())


async def load_chat_sync_flags() -> dict[int, dict[str, Any]]:
    model = get_model_class("chats")
    if model is None:
        raise ValueError("Unknown sync table: chats")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(model.id, model.type, model.is_system, model.is_mandatory)
        )
        return {
            int(chat_id): {
                "chat_type": json_safe(chat_type),
                "chat_is_system": bool(is_system),
                "chat_is_mandatory": bool(is_mandatory),
            }
            for chat_id, chat_type, is_system, is_mandatory in result.all()
        }


async def send_items(target_url: str, api_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps(items, sort_keys=True, default=str)
    assert_runtime_sync_transport_allowed()
    async with httpx.AsyncClient(timeout=60.0, verify=runtime_sync_tls_verify_setting()) as client:
        response = await client.post(
            f"{target_url.rstrip('/')}/api/sync/receive",
            content=body,
            headers=build_signed_headers(api_key, body),
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "invalid-json", "body_sha256": hashlib.sha256(response.content).hexdigest()[:16]}
    if response.status_code != 200 or payload.get("status") not in {"success", "ok"} or int(payload.get("errors") or 0) > 0:
        raise RuntimeError(
            f"Peer rejected seed batch: status_code={response.status_code} payload={json.dumps(payload, default=str)}"
        )
    return payload


async def seed_table(table_name: str, *, target_url: str, api_key: str, batch_size: int, dry_run: bool) -> dict[str, int]:
    rows = await load_table_rows(table_name)
    chat_sync_flags = await load_chat_sync_flags() if table_name == "chat_members" else {}
    if dry_run:
        print(f"[dry-run][{table_name}] rows={len(rows)}")
        return {"rows": len(rows), "sent": 0}

    sent = 0
    for index in range(0, len(rows), batch_size):
        batch_rows = rows[index : index + batch_size]
        items = []
        for row in batch_rows:
            data = row_payload(table_name, row)
            if table_name == "chat_members":
                data.update(chat_sync_flags.get(int(data.get("chat_id") or 0), {}))
            items.append(
                {
                    "type": "db_change",
                    "operation": "UPDATE",
                    "table": table_name,
                    "id": record_id_for(row),
                    "data": data,
                    "hash": "",
                    "timestamp": time.time(),
                }
            )
        if not items:
            continue
        await send_items(target_url, api_key, items)
        sent += len(items)
        print(f"[{table_name}] sent={sent}/{len(rows)}")
    return {"rows": len(rows), "sent": sent}


async def main_async() -> int:
    args = parse_args()
    api_key = settings.sync_api_key
    if not api_key:
        print("SYNC_API_KEY is not configured", file=sys.stderr)
        return 2

    target_url = args.target_url or (peer_server_url_for(args.target_server) if args.target_server else default_peer_server_url())
    if not target_url:
        print("No target URL configured", file=sys.stderr)
        return 2

    tables = tuple(args.table or DEFAULT_TABLES)
    total_rows = 0
    total_sent = 0
    for table_name in tables:
        stats = await seed_table(
            table_name,
            target_url=target_url,
            api_key=api_key,
            batch_size=max(args.batch_size, 1),
            dry_run=args.dry_run,
        )
        total_rows += stats["rows"]
        total_sent += stats["sent"]

    print(json.dumps({"status": "ok", "tables": list(tables), "rows": total_rows, "sent": total_sent}, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
