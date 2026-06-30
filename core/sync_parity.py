"""Runtime parity snapshots for cross-server synced tables.

The parity checker never emits raw row values. It groups persisted columns into
business, local-only, and volatile buckets, then compares stable hashes between
servers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

import models  # noqa: F401 - register model metadata
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.sync_field_policy import (
    SyncFieldClassification,
    SyncFieldPolicyEntry,
    get_sync_field_policy_entry,
)
from core.sync_registry import SyncPolicy, sync_registry_entries
from models.database import Base


SYNC_PARITY_SCHEMA_VERSION = 1

PARITY_QUICK_TABLES = (
    "users",
    "accountant_relations",
    "customer_relations",
    "invitations",
    "notifications",
    "user_blocks",
    "market_runtime_state",
    "offers",
    "offer_requests",
    "trades",
    "trade_delivery_receipts",
    "telegram_admin_broadcasts",
    "telegram_admin_broadcast_receipts",
)

IDENTITY_FIELDS_BY_TABLE: dict[str, tuple[str, ...]] = {
    "accountant_relations": ("invitation_token",),
    "commodities": ("name",),
    "commodity_aliases": ("alias",),
    "customer_relations": ("invitation_token",),
    "invitations": ("token",),
    "market_schedule_overrides": ("date",),
    "notifications": ("dedupe_key",),
    "offer_publication_states": ("dedupe_key",),
    "offer_requests": ("request_home_server", "idempotency_key"),
    "offers": ("offer_public_id",),
    "telegram_link_tokens": ("token_hash",),
    "telegram_admin_broadcast_receipts": ("dedupe_key",),
    "trade_delivery_receipts": ("dedupe_key",),
    "trades": ("trade_number",),
    "trading_settings": ("key",),
    "user_blocks": ("blocker_id", "blocked_id"),
    "user_notification_preferences": ("user_id",),
}

FALLBACK_IDENTITY_FIELDS = ("id",)

VOLATILE_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "*": {"updated_at"},
    "users": {"last_seen_at", "updated_at"},
    "offer_publication_states": {"last_attempt_at", "last_success_at", "next_retry_at", "updated_at"},
    "trade_delivery_receipts": {"next_retry_at", "updated_at"},
    "telegram_admin_broadcasts": {"updated_at"},
    "telegram_admin_broadcast_receipts": {"next_retry_at", "updated_at"},
}

LOCAL_ONLY_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "offers": {"channel_message_id"},
    "offer_publication_states": {
        "id",
        "offer_id",
        "surface_resource_id",
        "telegram_chat_id",
        "telegram_message_id",
        "error_code",
        "error_message",
        "state_metadata",
    },
    "trade_delivery_receipts": {"trade_id", "offer_id", "notification_id", "worker_id", "lease_until"},
    "telegram_admin_broadcast_receipts": {"worker_id", "lease_until"},
}

SENSITIVE_IDENTITY_FIELDS = {
    "mobile_number",
    "phone_number",
    "token",
    "token_hash",
    "short_code",
    "dedupe_key",
    "used_telegram_id",
    "telegram_id",
}


def _canonical_value(value: Any) -> Any:
    raw = getattr(value, "value", value)
    if isinstance(raw, Enum):
        return raw.value
    if isinstance(raw, datetime):
        return raw.isoformat()
    if isinstance(raw, (date, time)):
        return raw.isoformat()
    if isinstance(raw, Decimal):
        return str(raw)
    if isinstance(raw, Mapping):
        return {str(key): _canonical_value(raw[key]) for key in sorted(raw)}
    if isinstance(raw, (list, tuple)):
        return [_canonical_value(item) for item in raw]
    return raw


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field_policy(table_name: str, field_name: str) -> SyncFieldPolicyEntry | None:
    return get_sync_field_policy_entry(table_name, field_name)


def _volatile_fields(table_name: str) -> set[str]:
    return set(VOLATILE_FIELDS_BY_TABLE.get("*", set())) | set(VOLATILE_FIELDS_BY_TABLE.get(table_name, set()))


def _local_only_fields(table_name: str) -> set[str]:
    return set(LOCAL_ONLY_FIELDS_BY_TABLE.get(table_name, set()))


def _local_db_identity_fields(table_name: str, row: Mapping[str, Any]) -> set[str]:
    identity_fields = set(_identity_fields_for_row(table_name, row))
    if identity_fields and identity_fields != {"id"}:
        return {"id"}
    return set()


def _identity_fields_for_row(table_name: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    configured = IDENTITY_FIELDS_BY_TABLE.get(table_name)
    if configured:
        values = [row.get(field) for field in configured]
        if all(value not in (None, "") for value in values):
            return configured
        if table_name == "notifications" and row.get("id") not in (None, ""):
            return ("id",)
    return FALLBACK_IDENTITY_FIELDS


def _identity_payload(table_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    fields = _identity_fields_for_row(table_name, row)
    return {
        "table": table_name,
        "fields": fields,
        "values": {field: _canonical_value(row.get(field)) for field in fields},
    }


def _identity_label(table_name: str, row: Mapping[str, Any]) -> str | None:
    fields = _identity_fields_for_row(table_name, row)
    if any(field in SENSITIVE_IDENTITY_FIELDS for field in fields):
        return None
    values = [row.get(field) for field in fields]
    if any(value in (None, "") for value in values):
        return None
    return "|".join(str(_canonical_value(value)) for value in values)


def _classify_fields(table_name: str, row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    business: dict[str, Any] = {}
    local_only: dict[str, Any] = {}
    volatile: dict[str, Any] = {}
    volatile_fields = _volatile_fields(table_name)
    local_only_fields = _local_only_fields(table_name) | _local_db_identity_fields(table_name, row)

    for key in sorted(str(field) for field in row.keys()):
        value = row.get(key)
        policy = _field_policy(table_name, key)
        if key in local_only_fields or (
            policy is not None and policy.classification == SyncFieldClassification.NO_SYNC
        ):
            local_only[key] = value
            continue
        if key in volatile_fields:
            volatile[key] = value
            continue
        business[key] = value

    return business, local_only, volatile


def build_record_parity(table_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity_payload(table_name, row)
    business, local_only, volatile = _classify_fields(table_name, row)
    payload = {
        "identity_hash": _hash_payload(identity),
        "identity_fields": list(identity["fields"]),
        "business_hash": _hash_payload(business),
        "local_only_hash": _hash_payload(local_only),
        "volatile_hash": _hash_payload(volatile),
    }
    label = _identity_label(table_name, row)
    if label is not None:
        payload["identity_label"] = label
    return payload


def build_table_parity_snapshot(
    table_name: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    max_rows: int | None = None,
) -> dict[str, Any]:
    records = []
    truncated = False
    for index, row in enumerate(rows):
        if max_rows is not None and index >= max_rows:
            truncated = True
            break
        records.append(build_record_parity(table_name, row))

    records.sort(key=lambda item: item["identity_hash"])
    identity_counts: dict[str, int] = {}
    for record in records:
        identity_hash = str(record["identity_hash"])
        identity_counts[identity_hash] = identity_counts.get(identity_hash, 0) + 1
    duplicate_identity_hashes = sorted(
        identity_hash
        for identity_hash, count in identity_counts.items()
        if count > 1
    )
    duplicate_identity_count = sum(
        count - 1
        for count in identity_counts.values()
        if count > 1
    )
    return {
        "table": table_name,
        "row_count": len(records),
        "truncated": truncated,
        "duplicate_identity_count": duplicate_identity_count,
        "duplicate_identity_hashes": duplicate_identity_hashes[:20],
        "records_hash": _hash_payload(
            [
                {
                    "identity_hash": record["identity_hash"],
                    "business_hash": record["business_hash"],
                    "local_only_hash": record["local_only_hash"],
                    "volatile_hash": record["volatile_hash"],
                }
                for record in records
            ]
        ),
        "records": records,
    }


def synced_parity_table_names(mode: str = "quick") -> tuple[str, ...]:
    normalized = str(mode or "quick").strip().lower()
    synced = {
        table_name
        for table_name, entry in sync_registry_entries().items()
        if entry.policy == SyncPolicy.SYNC and table_name in Base.metadata.tables
    }
    if normalized == "quick":
        return tuple(table for table in PARITY_QUICK_TABLES if table in synced)
    if normalized == "deep":
        return tuple(sorted(synced))
    raise ValueError("parity mode must be 'quick' or 'deep'")


async def build_database_parity_snapshot(
    db: AsyncSession,
    *,
    mode: str = "quick",
    max_rows_per_table: int = 5000,
) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    table_names = synced_parity_table_names(mode)

    for table_name in table_names:
        table = Base.metadata.tables[table_name]
        order_columns = [table.c[field] for field in IDENTITY_FIELDS_BY_TABLE.get(table_name, ()) if field in table.c]
        if not order_columns and "id" in table.c:
            order_columns = [table.c.id]
        stmt = select(table)
        if order_columns:
            stmt = stmt.order_by(*order_columns)
        if max_rows_per_table > 0:
            stmt = stmt.limit(max_rows_per_table + 1)
        result = await db.execute(stmt)
        rows = [dict(row) for row in result.mappings().all()]
        tables[table_name] = build_table_parity_snapshot(
            table_name,
            rows,
            max_rows=max_rows_per_table if max_rows_per_table > 0 else None,
        )

    return {
        "status": "ok",
        "schema_version": SYNC_PARITY_SCHEMA_VERSION,
        "mode": str(mode or "quick").strip().lower(),
        "table_count": len(tables),
        "max_rows_per_table": max_rows_per_table,
        "tables": tables,
    }


def _records_by_identity(table_snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records = table_snapshot.get("records") if isinstance(table_snapshot, Mapping) else []
    if not isinstance(records, Sequence):
        return {}
    return {
        str(record.get("identity_hash")): record
        for record in records
        if isinstance(record, Mapping) and record.get("identity_hash")
    }


def _duplicate_identity_hashes(table_snapshot: Mapping[str, Any]) -> list[str]:
    records = table_snapshot.get("records") if isinstance(table_snapshot, Mapping) else []
    if not isinstance(records, Sequence):
        return []
    counts: dict[str, int] = {}
    for record in records:
        if not isinstance(record, Mapping) or not record.get("identity_hash"):
            continue
        identity_hash = str(record["identity_hash"])
        counts[identity_hash] = counts.get(identity_hash, 0) + 1
    return sorted(identity_hash for identity_hash, count in counts.items() if count > 1)


def _duplicate_identity_count(table_snapshot: Mapping[str, Any]) -> int:
    explicit = table_snapshot.get("duplicate_identity_count") if isinstance(table_snapshot, Mapping) else None
    try:
        explicit_count = int(explicit)
    except (TypeError, ValueError):
        explicit_count = -1
    if explicit_count >= 0:
        return explicit_count

    records = table_snapshot.get("records") if isinstance(table_snapshot, Mapping) else []
    if not isinstance(records, Sequence):
        return 0
    counts: dict[str, int] = {}
    for record in records:
        if not isinstance(record, Mapping) or not record.get("identity_hash"):
            continue
        identity_hash = str(record["identity_hash"])
        counts[identity_hash] = counts.get(identity_hash, 0) + 1
    return sum(count - 1 for count in counts.values() if count > 1)


def compare_parity_snapshots(
    local_snapshot: Mapping[str, Any],
    peer_snapshot: Mapping[str, Any],
    *,
    sample_limit: int = 5,
) -> dict[str, Any]:
    local_tables = local_snapshot.get("tables") if isinstance(local_snapshot, Mapping) else {}
    peer_tables = peer_snapshot.get("tables") if isinstance(peer_snapshot, Mapping) else {}
    local_tables = local_tables if isinstance(local_tables, Mapping) else {}
    peer_tables = peer_tables if isinstance(peer_tables, Mapping) else {}

    table_names = sorted(set(local_tables) | set(peer_tables))
    table_reports: dict[str, Any] = {}
    severity_counts = {
        "incomplete": 0,
        "critical_drift": 0,
        "business_drift": 0,
        "local_only_difference": 0,
        "volatile_difference": 0,
    }

    for table_name in table_names:
        local_table = local_tables.get(table_name) or {}
        peer_table = peer_tables.get(table_name) or {}
        local_records = _records_by_identity(local_table)
        peer_records = _records_by_identity(peer_table)
        local_ids = set(local_records)
        peer_ids = set(peer_records)
        local_row_count = int(local_table.get("row_count") or len(local_records))
        peer_row_count = int(peer_table.get("row_count") or len(peer_records))
        local_truncated = bool(local_table.get("truncated"))
        peer_truncated = bool(peer_table.get("truncated"))
        local_duplicate_hashes = _duplicate_identity_hashes(local_table)
        peer_duplicate_hashes = _duplicate_identity_hashes(peer_table)
        local_duplicate_count = _duplicate_identity_count(local_table)
        peer_duplicate_count = _duplicate_identity_count(peer_table)
        row_count_mismatch = local_row_count != peer_row_count

        missing_on_local = sorted(peer_ids - local_ids)
        missing_on_peer = sorted(local_ids - peer_ids)
        business_mismatches: list[str] = []
        local_only_mismatches: list[str] = []
        volatile_mismatches: list[str] = []

        for identity_hash in sorted(local_ids & peer_ids):
            local_record = local_records[identity_hash]
            peer_record = peer_records[identity_hash]
            if local_record.get("business_hash") != peer_record.get("business_hash"):
                business_mismatches.append(identity_hash)
            elif local_record.get("local_only_hash") != peer_record.get("local_only_hash"):
                local_only_mismatches.append(identity_hash)
            elif local_record.get("volatile_hash") != peer_record.get("volatile_hash"):
                volatile_mismatches.append(identity_hash)

        if local_truncated or peer_truncated:
            severity = "incomplete"
        elif local_duplicate_count or peer_duplicate_count:
            severity = "critical_drift"
        elif missing_on_local or missing_on_peer or row_count_mismatch:
            severity = "critical_drift"
        elif business_mismatches:
            severity = "business_drift"
        elif local_only_mismatches:
            severity = "local_only_difference"
        elif volatile_mismatches:
            severity = "volatile_difference"
        else:
            severity = "ok"

        if severity != "ok":
            severity_counts[severity] += 1

        table_reports[table_name] = {
            "severity": severity,
            "local_row_count": local_row_count,
            "peer_row_count": peer_row_count,
            "local_truncated": local_truncated,
            "peer_truncated": peer_truncated,
            "row_count_mismatch": row_count_mismatch,
            "local_duplicate_identity_count": local_duplicate_count,
            "peer_duplicate_identity_count": peer_duplicate_count,
            "missing_on_local_count": len(missing_on_local),
            "missing_on_peer_count": len(missing_on_peer),
            "business_mismatch_count": len(business_mismatches),
            "local_only_difference_count": len(local_only_mismatches),
            "volatile_difference_count": len(volatile_mismatches),
            "samples": {
                "missing_on_local": missing_on_local[:sample_limit],
                "missing_on_peer": missing_on_peer[:sample_limit],
                "local_duplicate_identities": local_duplicate_hashes[:sample_limit],
                "peer_duplicate_identities": peer_duplicate_hashes[:sample_limit],
                "business_mismatches": business_mismatches[:sample_limit],
                "local_only_differences": local_only_mismatches[:sample_limit],
                "volatile_differences": volatile_mismatches[:sample_limit],
            },
        }

    if severity_counts["incomplete"]:
        status = "incomplete"
    elif severity_counts["critical_drift"]:
        status = "critical_drift"
    elif severity_counts["business_drift"]:
        status = "business_drift"
    elif severity_counts["local_only_difference"] or severity_counts["volatile_difference"]:
        status = "non_business_difference"
    else:
        status = "ok"

    return {
        "status": status,
        "schema_version": SYNC_PARITY_SCHEMA_VERSION,
        "table_count": len(table_names),
        "severity_counts": severity_counts,
        "tables": table_reports,
    }
