"""Runtime parity snapshots for cross-server synced tables.

The parity checker never emits raw row values. It groups persisted columns into
business, local-only, and volatile buckets, then compares stable hashes between
servers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import models  # noqa: F401 - register model metadata
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.production_shadow_parity import (
    SYNC_PARITY_SCHEMA_VERSION,
    _canonical_value,
    _duplicate_identity_count,
    _duplicate_identity_hashes,
    _hash_payload,
    _records_by_identity,
    business_snapshot_fingerprint,
    compare_parity_snapshots,
)
from core.sync_field_policy import (
    SyncFieldClassification,
    SyncFieldPolicyEntry,
    get_sync_field_policy_entry,
)
from models.database import Base


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
    "telegram_notification_outbox",
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
    "telegram_notification_outbox": ("dedupe_key",),
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
    "telegram_notification_outbox": {"next_retry_at", "updated_at"},
}

LOCAL_ONLY_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "commodity_aliases": {"commodity_id"},
    "offers": {"channel_message_id", "commodity_id", "republished_offer_id"},
    "offer_requests": {"local_offer_id", "resulting_trade_id", "customer_relation_id"},
    "trades": {"offer_id", "commodity_id"},
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
    "telegram_notification_outbox": {"worker_id", "lease_until"},
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
        # The complete records hash intentionally includes local execution and
        # volatile fields.  It is valuable for diagnosis, but it must never be
        # used as a claim that two independently-running sites have the same
        # business state.  This companion hash is the canonical, redacted
        # business-only value used by the three-site convergence gate.
        "business_records_hash": _hash_payload(
            [
                {
                    "identity_hash": record["identity_hash"],
                    "business_hash": record["business_hash"],
                }
                for record in records
            ]
        ),
        "records": records,
    }


def synced_parity_table_names(mode: str = "quick") -> tuple[str, ...]:
    # Keep redacted snapshot comparison usable from an offline controller.
    # Importing the registry reads runtime settings, while a comparison of two
    # already-collected snapshots needs neither credentials nor a database.
    from core.sync_registry import SyncPolicy, sync_registry_entries

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


def _parity_select_for_table(table_name: str, table):
    """Select persisted rows plus stable identities for local FK columns."""
    if table_name == "commodity_aliases":
        commodities = Base.metadata.tables["commodities"]
        return (
            select(table, commodities.c.name.label("commodity_name"))
            .select_from(table.outerjoin(commodities, commodities.c.id == table.c.commodity_id))
        )

    if table_name == "offers":
        commodities = Base.metadata.tables["commodities"]
        republished = table.alias("parity_republished_offer")
        columns = [table, commodities.c.name.label("commodity_name")]
        if "republished_offer_public_id" not in table.c:
            columns.append(
                republished.c.offer_public_id.label("republished_offer_public_id")
            )
        return (
            select(*columns)
            .select_from(
                table
                .outerjoin(commodities, commodities.c.id == table.c.commodity_id)
                .outerjoin(republished, republished.c.id == table.c.republished_offer_id)
            )
        )

    if table_name == "trades":
        commodities = Base.metadata.tables["commodities"]
        offers = Base.metadata.tables["offers"]
        return (
            select(
                table,
                commodities.c.name.label("commodity_name"),
                offers.c.offer_public_id.label("offer_public_id"),
            )
            .select_from(
                table
                .outerjoin(commodities, commodities.c.id == table.c.commodity_id)
                .outerjoin(offers, offers.c.id == table.c.offer_id)
            )
        )

    if table_name == "offer_requests":
        trades = Base.metadata.tables["trades"]
        customer_relations = Base.metadata.tables["customer_relations"]
        return (
            select(
                table,
                trades.c.trade_number.label("resulting_trade_number"),
                customer_relations.c.invitation_token.label("customer_relation_invitation_token"),
            )
            .select_from(
                table
                .outerjoin(trades, trades.c.id == table.c.resulting_trade_id)
                .outerjoin(
                    customer_relations,
                    customer_relations.c.id == table.c.customer_relation_id,
                )
            )
        )

    return select(table)


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
        stmt = _parity_select_for_table(table_name, table)
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
