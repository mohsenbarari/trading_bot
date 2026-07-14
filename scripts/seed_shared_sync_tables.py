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
from dataclasses import dataclass
from datetime import date, datetime, time as time_type
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import httpx
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers.sync import TABLE_ORDER, get_model_class
from core.config import settings
from core.db import AsyncSessionLocal
from core.registration_sync_policy import (
    REGISTRATION_USER_REFERENCE_FIELDS,
    REGISTRATION_USER_REFERENCES_FIELD,
)
from core.server_routing import current_server, default_peer_server_url, peer_server_url_for
from core.sync_field_policy import sanitize_sync_payload
from core.sync_metadata import build_sync_metadata, build_sync_public_identity
from core.sync_protocol import build_sync_protocol_metadata
from core.sync_transport import assert_runtime_sync_transport_allowed, runtime_sync_tls_verify_setting
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.user_counter_sync import USER_SYNC_IDENTITY_FIELD, build_user_sync_identity


SEED_TABLE_ORDER = {
    **TABLE_ORDER,
    # A completed offer request references its resulting trade by a local FK.
    # Current-state recovery therefore seeds trades before request ledgers.
    "trades": 19,
    "offer_requests": 20,
    # Notifications have no downstream FK consumers in the shared snapshot.
    # Seed them last so any recovery-time reconciliation cannot remove the
    # final authoritative notification set.
    "notifications": 25,
}

DEFAULT_TABLES = tuple(
    table
    for table, _order in sorted(SEED_TABLE_ORDER.items(), key=lambda item: item[1])
    if (entry := get_sync_registry_entry(table)) is not None and entry.policy == SyncPolicy.SYNC
)

SKIP_COLUMNS_BY_TABLE = {
    "users": {"avatar_file_id"},
    "chats": {"avatar_file_id", "last_message_id", "pinned_message_id"},
    "chat_members": {"last_read_message_id"},
}


@dataclass(frozen=True)
class SeedReferenceIndex:
    commodity_names_by_id: Mapping[int, str]
    offer_public_ids_by_id: Mapping[int, str]
    trade_numbers_by_id: Mapping[int, int]
    customer_relation_tokens_by_id: Mapping[int, str]
    user_identities_by_id: Mapping[int, Mapping[str, Any]]


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
    sanitized = sanitize_sync_payload(table_name, payload)
    if not isinstance(sanitized, dict):
        raise ValueError(f"Sanitized seed payload for {table_name!r} is not an object")
    return sanitized


def _required_reference(
    references: Mapping[int, Any],
    raw_id: Any,
    *,
    table_name: str,
    field_name: str,
) -> Any:
    try:
        reference_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {table_name}.{field_name} reference id") from exc
    value = references.get(reference_id)
    if value in (None, ""):
        raise ValueError(
            f"Cannot resolve required seed reference for {table_name}.{field_name} id={reference_id}"
        )
    return value


def enrich_seed_payload(
    table_name: str,
    row: Any,
    payload: dict[str, Any],
    references: SeedReferenceIndex,
) -> dict[str, Any]:
    """Add stable cross-server identities for local FK columns.

    Raw integer IDs are not portable for tables whose receiver upserts by a
    natural/public identity. Recovery must fail before network delivery when a
    required source identity cannot be resolved.
    """
    enriched = dict(payload)

    if table_name == "users":
        enriched[USER_SYNC_IDENTITY_FIELD] = build_user_sync_identity(row, include_previous=False)

    user_reference_fields = REGISTRATION_USER_REFERENCE_FIELDS.get(table_name, ())
    user_references: dict[str, Mapping[str, Any]] = {}
    for field_name in user_reference_fields:
        user_id = getattr(row, field_name, None)
        if user_id is None:
            continue
        identity = _required_reference(
            references.user_identities_by_id,
            user_id,
            table_name=table_name,
            field_name=field_name,
        )
        user_references[field_name] = {"current": dict(identity), "previous": {}}
    if user_references:
        enriched[REGISTRATION_USER_REFERENCES_FIELD] = user_references

    if table_name in {"commodity_aliases", "offers", "trades"}:
        commodity_id = getattr(row, "commodity_id", None)
        if commodity_id is not None:
            enriched["commodity_name"] = _required_reference(
                references.commodity_names_by_id,
                commodity_id,
                table_name=table_name,
                field_name="commodity_id",
            )

    if table_name == "trades":
        offer_id = getattr(row, "offer_id", None)
        if offer_id is not None:
            enriched["offer_public_id"] = _required_reference(
                references.offer_public_ids_by_id,
                offer_id,
                table_name=table_name,
                field_name="offer_id",
            )

    if table_name == "offers":
        republished_offer_id = getattr(row, "republished_offer_id", None)
        if republished_offer_id is not None:
            enriched["republished_offer_public_id"] = _required_reference(
                references.offer_public_ids_by_id,
                republished_offer_id,
                table_name=table_name,
                field_name="republished_offer_id",
            )

    if table_name == "offer_requests":
        resulting_trade_id = getattr(row, "resulting_trade_id", None)
        if resulting_trade_id is not None:
            enriched["resulting_trade_number"] = _required_reference(
                references.trade_numbers_by_id,
                resulting_trade_id,
                table_name=table_name,
                field_name="resulting_trade_id",
            )

        customer_relation_id = getattr(row, "customer_relation_id", None)
        if customer_relation_id is not None:
            enriched["customer_relation_invitation_token"] = _required_reference(
                references.customer_relation_tokens_by_id,
                customer_relation_id,
                table_name=table_name,
                field_name="customer_relation_id",
            )

    return enriched


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
    if table_name == "offers" and primary_key_columns:
        # Republished offers point from an older row to its newer replacement.
        # Newest-first makes that local reference resolvable during recovery.
        order_columns = [column.desc() for column in primary_key_columns]
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(model).order_by(*order_columns))
        return list(result.scalars().all())


async def load_seed_reference_index() -> SeedReferenceIndex:
    commodity_model = get_model_class("commodities")
    offer_model = get_model_class("offers")
    trade_model = get_model_class("trades")
    customer_relation_model = get_model_class("customer_relations")
    user_model = get_model_class("users")
    if any(
        model is None
        for model in (commodity_model, offer_model, trade_model, customer_relation_model, user_model)
    ):
        raise ValueError("Seed reference models are not fully registered")

    async with AsyncSessionLocal() as db:
        commodity_rows = (
            await db.execute(select(commodity_model.id, commodity_model.name))
        ).all()
        offer_rows = (
            await db.execute(select(offer_model.id, offer_model.offer_public_id))
        ).all()
        trade_rows = (
            await db.execute(select(trade_model.id, trade_model.trade_number))
        ).all()
        customer_relation_rows = (
            await db.execute(
                select(customer_relation_model.id, customer_relation_model.invitation_token)
            )
        ).all()
        user_rows = (
            await db.execute(
                select(
                    user_model.id,
                    user_model.account_name,
                    user_model.mobile_number,
                    user_model.telegram_id,
                )
            )
        ).all()

    return SeedReferenceIndex(
        commodity_names_by_id={int(row_id): str(value) for row_id, value in commodity_rows if value},
        offer_public_ids_by_id={int(row_id): str(value) for row_id, value in offer_rows if value},
        trade_numbers_by_id={int(row_id): int(value) for row_id, value in trade_rows if value is not None},
        customer_relation_tokens_by_id={
            int(row_id): str(value) for row_id, value in customer_relation_rows if value
        },
        user_identities_by_id={
            int(row_id): {
                key: value
                for key, value in {
                    "account_name": account_name,
                    "mobile_number": mobile_number,
                    "telegram_id": telegram_id,
                }.items()
                if value not in (None, "")
            }
            for row_id, account_name, mobile_number, telegram_id in user_rows
        },
    )


def build_seed_sync_item(table_name: str, row: Any, data: dict[str, Any]) -> dict[str, Any]:
    record_id = record_id_for(row)
    operation = "INSERT"
    item = {
        "type": "db_change",
        "operation": operation,
        "table": table_name,
        "id": record_id,
        "data": data,
        "hash": "",
        "timestamp": time.time(),
        "sync_protocol": build_sync_protocol_metadata(),
        "sync_meta": build_sync_metadata(
            table_name,
            record_id,
            operation,
            data,
            source_server=current_server(),
        ),
    }
    public_identity = build_sync_public_identity(table_name, record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return item


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


async def seed_table(
    table_name: str,
    *,
    target_url: str,
    api_key: str,
    batch_size: int,
    dry_run: bool,
    references: SeedReferenceIndex | None = None,
) -> dict[str, int]:
    rows = await load_table_rows(table_name)
    chat_sync_flags = await load_chat_sync_flags() if table_name == "chat_members" else {}
    if references is None:
        references = await load_seed_reference_index()

    if dry_run:
        for row in rows:
            data = row_payload(table_name, row)
            if table_name == "chat_members":
                data.update(chat_sync_flags.get(int(data.get("chat_id") or 0), {}))
            enrich_seed_payload(table_name, row, data, references)
        print(f"[dry-run][{table_name}] rows={len(rows)} validated={len(rows)}")
        return {"rows": len(rows), "sent": 0}

    sent = 0
    for index in range(0, len(rows), batch_size):
        batch_rows = rows[index : index + batch_size]
        items = []
        for row in batch_rows:
            data = row_payload(table_name, row)
            if table_name == "chat_members":
                data.update(chat_sync_flags.get(int(data.get("chat_id") or 0), {}))
            data = enrich_seed_payload(table_name, row, data, references)
            items.append(build_seed_sync_item(table_name, row, data))
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
    references = await load_seed_reference_index()
    total_rows = 0
    total_sent = 0
    for table_name in tables:
        stats = await seed_table(
            table_name,
            target_url=target_url,
            api_key=api_key,
            batch_size=max(args.batch_size, 1),
            dry_run=args.dry_run,
            references=references,
        )
        total_rows += stats["rows"]
        total_sent += stats["sent"]

    print(json.dumps({"status": "ok", "tables": list(tables), "rows": total_rows, "sent": total_sent}, sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
