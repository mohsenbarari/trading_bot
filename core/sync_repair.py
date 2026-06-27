"""Dry-run-first repair helpers for cross-server sync parity drift."""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime, time as time_type
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.sync import get_model_class
from core.server_routing import current_server
from core.sync_field_policy import sanitize_sync_payload
from core.sync_metadata import build_sync_metadata, build_sync_public_identity, coerce_positive_int
from core.sync_parity import build_record_parity, compare_parity_snapshots
from core.sync_protocol import build_sync_protocol_metadata


REPAIR_TOOL_SCHEMA_VERSION = 1

REPLAY_SKIP_COLUMNS_BY_TABLE: dict[str, set[str]] = {
    "users": {"avatar_file_id"},
    "offers": {"channel_message_id"},
    "chats": {"avatar_file_id", "last_message_id", "pinned_message_id"},
    "chat_members": {"last_read_message_id"},
    "trade_delivery_receipts": {"worker_id", "lease_until"},
}

REPLAY_IDENTITY_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "accountant_relations": {"id", "invitation_token"},
    "admin_broadcast_messages": {"id"},
    "admin_market_messages": {"id"},
    "commodities": {"id", "name"},
    "commodity_aliases": {"id", "alias"},
    "customer_relations": {"id", "invitation_token"},
    "invitations": {"id", "token", "short_code"},
    "market_runtime_state": {"id"},
    "market_schedule_overrides": {"id", "date"},
    "notifications": {"id", "dedupe_key"},
    "offer_publication_states": {"id", "dedupe_key", "offer_public_id", "surface"},
    "offer_requests": {"id", "request_home_server", "idempotency_key", "offer_public_id"},
    "offers": {"id", "offer_public_id", "idempotency_key"},
    "telegram_link_tokens": {"id", "token_hash", "user_id"},
    "trades": {"id", "trade_number", "idempotency_key"},
    "trade_delivery_receipts": {"id", "dedupe_key", "trade_number"},
    "trading_settings": {"id", "key"},
    "user_blocks": {"id", "blocker_id", "blocked_id"},
    "user_notification_preferences": {"id", "user_id"},
    "users": {"id", "mobile_number", "telegram_id", "account_name"},
}


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


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_replay_identity(table_name: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    table = str(table_name or "").strip()
    if not table:
        raise ValueError("table is required")
    if not isinstance(identity, Mapping) or not identity:
        raise ValueError("identity must be a non-empty JSON object")
    allowed = REPLAY_IDENTITY_FIELDS_BY_TABLE.get(table)
    if not allowed:
        raise ValueError(f"replay identity is not configured for table {table!r}")

    normalized: dict[str, Any] = {}
    for raw_key, value in identity.items():
        key = str(raw_key)
        if key not in allowed:
            raise ValueError(f"identity field {key!r} is not allowed for table {table!r}")
        if value in (None, ""):
            raise ValueError(f"identity field {key!r} cannot be empty")
        normalized[key] = value
    return normalized


def replay_identity_summary(table_name: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_replay_identity(table_name, identity)
    return {
        "table": table_name,
        "identity_fields": sorted(normalized),
        "identity_hash": stable_hash({"table": table_name, "identity": normalized}),
    }


def record_id_for_row(row: Any) -> Any:
    if hasattr(row, "id"):
        return getattr(row, "id")
    if hasattr(row, "key"):
        return getattr(row, "key")
    raise ValueError(f"Cannot resolve record id for {row!r}")


def row_to_sync_data(table_name: str, row: Any) -> dict[str, Any]:
    skip_columns = REPLAY_SKIP_COLUMNS_BY_TABLE.get(table_name, set())
    payload: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name in skip_columns:
            continue
        payload[column.name] = json_safe(getattr(row, column.name))
    sanitized = sanitize_sync_payload(table_name, payload)
    if not isinstance(sanitized, dict):
        raise ValueError(f"sanitized replay payload for {table_name!r} is not an object")
    return sanitized


def build_current_state_replay_item(
    *,
    table_name: str,
    row: Any,
    operation: str = "UPDATE",
    source_server: str | None = None,
    source_sequence: int | None = None,
) -> dict[str, Any]:
    record_id = record_id_for_row(row)
    data = row_to_sync_data(table_name, row)
    payload_hash = stable_hash(data)
    source_server = source_server or current_server()
    item = {
        "type": "db_change",
        "operation": operation,
        "table": table_name,
        "id": record_id,
        "data": data,
        "hash": payload_hash,
        "timestamp": time.time(),
        "sync_protocol": build_sync_protocol_metadata(producer_server=source_server),
        "sync_meta": build_sync_metadata(
            table_name,
            record_id,
            operation,
            data,
            change_log_id=source_sequence,
            source_server=source_server,
        ),
    }
    if source_sequence is not None:
        item["change_log_id"] = int(source_sequence)
    public_identity = build_sync_public_identity(table_name, record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return item


def summarize_replay_item(item: Mapping[str, Any]) -> dict[str, Any]:
    table_name = str(item.get("table") or "")
    data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
    return {
        "schema_version": REPAIR_TOOL_SCHEMA_VERSION,
        "type": "current_state_replay",
        "dry_run": True,
        "table": table_name,
        "operation": item.get("operation"),
        "record_id": item.get("id"),
        "data_key_count": len(data),
        "payload_hash": item.get("hash"),
        "record_parity": build_record_parity(table_name, data) if table_name and data else None,
        "sync_meta": item.get("sync_meta"),
        "public_identity": item.get("public_identity"),
    }


async def load_row_by_identity(db: AsyncSession, table_name: str, identity: Mapping[str, Any]) -> Any:
    normalized = validate_replay_identity(table_name, identity)
    model = get_model_class(table_name)
    if model is None:
        raise ValueError(f"unknown sync table: {table_name}")
    conditions = []
    for key, value in normalized.items():
        column = getattr(model, key, None)
        if column is None:
            raise ValueError(f"identity field {key!r} is not a model column for {table_name!r}")
        conditions.append(column == value)
    result = await db.execute(select(model).where(*conditions).limit(2))
    rows = list(result.scalars().all())
    if not rows:
        raise LookupError(f"no {table_name} row matched the supplied identity")
    if len(rows) > 1:
        raise ValueError(f"identity for {table_name} matched more than one row")
    return rows[0]


def build_signed_headers(api_key: str, body: str, *, timestamp: int | None = None) -> dict[str, str]:
    if not api_key:
        raise ValueError("sync api key is required")
    timestamp = int(timestamp or time.time())
    signature = hmac.new(api_key.encode(), f"{timestamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
        "X-Timestamp": str(timestamp),
        "X-Signature": signature,
    }


def build_repair_plan(
    local_snapshot: Mapping[str, Any],
    peer_snapshot: Mapping[str, Any],
    *,
    direction: str = "local-to-peer",
    sample_limit: int = 5,
) -> dict[str, Any]:
    if direction not in {"local-to-peer", "peer-to-local"}:
        raise ValueError("direction must be local-to-peer or peer-to-local")
    comparison = compare_parity_snapshots(local_snapshot, peer_snapshot, sample_limit=sample_limit)
    actions: list[dict[str, Any]] = []
    source_side = "local" if direction == "local-to-peer" else "peer"
    target_side = "peer" if direction == "local-to-peer" else "local"

    for table_name, table_report in sorted((comparison.get("tables") or {}).items()):
        if not isinstance(table_report, Mapping):
            continue
        samples = table_report.get("samples") if isinstance(table_report.get("samples"), Mapping) else {}
        missing_on_target_key = "missing_on_peer" if target_side == "peer" else "missing_on_local"
        missing_on_source_key = "missing_on_local" if target_side == "peer" else "missing_on_peer"

        for identity_hash in list(samples.get(missing_on_target_key) or [])[:sample_limit]:
            actions.append(
                {
                    "action": "replay_current_state",
                    "reason": f"missing_on_{target_side}",
                    "table": table_name,
                    "source": source_side,
                    "target": target_side,
                    "identity_hash": identity_hash,
                }
            )
        for identity_hash in list(samples.get("business_mismatches") or [])[:sample_limit]:
            actions.append(
                {
                    "action": "replay_current_state",
                    "reason": "business_drift",
                    "table": table_name,
                    "source": source_side,
                    "target": target_side,
                    "identity_hash": identity_hash,
                }
            )
        for identity_hash in list(samples.get(missing_on_source_key) or [])[:sample_limit]:
            actions.append(
                {
                    "action": "manual_review_required",
                    "reason": f"row_missing_on_{source_side}",
                    "table": table_name,
                    "identity_hash": identity_hash,
                }
            )

    return {
        "schema_version": REPAIR_TOOL_SCHEMA_VERSION,
        "status": "dry_run",
        "direction": direction,
        "comparison_status": comparison.get("status"),
        "severity_counts": comparison.get("severity_counts"),
        "action_count": len(actions),
        "actions": actions,
    }


def build_watermark_repair_payload(
    *,
    source_server: str,
    aggregate_table: str,
    aggregate_key: str,
    source_sequence: int,
    payload_hash: str,
    operation: str,
    record_id: Any,
) -> dict[str, Any]:
    sequence = coerce_positive_int(source_sequence)
    if sequence is None:
        raise ValueError("source_sequence must be a positive integer")
    if not source_server or not aggregate_table or not aggregate_key or not payload_hash:
        raise ValueError("source_server, aggregate_table, aggregate_key, and payload_hash are required")
    return {
        "schema_version": REPAIR_TOOL_SCHEMA_VERSION,
        "status": "dry_run",
        "action": "repair_sync_apply_watermark",
        "requires_backup_and_operator_approval": True,
        "aggregate_table": aggregate_table,
        "aggregate_key_hash": stable_hash({"aggregate_table": aggregate_table, "aggregate_key": aggregate_key}),
        "source_server": source_server,
        "source_sequence": sequence,
        "payload_hash": payload_hash,
        "operation": operation,
        "record_id": str(record_id) if record_id is not None else None,
        "sql": (
            "INSERT INTO sync_apply_watermarks "
            "(source_server, aggregate_table, aggregate_key, last_source_sequence, "
            "last_payload_hash, last_operation, last_record_id) "
            "VALUES (:source_server, :aggregate_table, :aggregate_key, :source_sequence, "
            ":payload_hash, :operation, :record_id) "
            "ON CONFLICT (source_server, aggregate_table, aggregate_key) DO UPDATE SET "
            "last_source_sequence = EXCLUDED.last_source_sequence, "
            "last_payload_hash = EXCLUDED.last_payload_hash, "
            "last_operation = EXCLUDED.last_operation, "
            "last_record_id = EXCLUDED.last_record_id, "
            "updated_at = NOW() "
            "WHERE sync_apply_watermarks.last_source_sequence <= EXCLUDED.last_source_sequence"
        ),
        "parameters_redacted": {
            "source_server": source_server,
            "aggregate_table": aggregate_table,
            "aggregate_key_hash": stable_hash(aggregate_key),
            "source_sequence": sequence,
            "payload_hash": payload_hash,
            "operation": operation,
            "record_id": str(record_id) if record_id is not None else None,
        },
    }
