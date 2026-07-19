"""Deterministic every-table/every-field three-site DR classification."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import models  # noqa: F401 - register every mapped table

from core.dr_data_policy import WEBAPP_DR_DROPPED_FIELDS, WEBAPP_DR_REPLICA_TABLES
from core.sync_field_policy import (
    SyncFieldAction,
    SyncFieldClassification,
    SYNC_DERIVED_FIELDS,
    sync_field_policy_entries,
)
from core.sync_registry import SyncPolicy, sync_registry_entries
from models.database import Base


MATRIX_SCHEMA = "three-site-dr-data-matrix-v1"
_SENSITIVE_NAME_MARKERS = (
    "address",
    "auth",
    "content",
    "endpoint",
    "error",
    "full_name",
    "message",
    "mobile",
    "password",
    "p256dh",
    "secret",
    "text",
    "token",
    "username",
)


class DrDataMatrixError(RuntimeError):
    """Raised when mapped schema and declared DR policy diverge."""


def _replication_class(table_name: str, policy: SyncPolicy) -> str:
    if policy == SyncPolicy.INTERNAL_BOOKKEEPING:
        return "internal_control"
    if policy == SyncPolicy.SYNC:
        return "product_sync"
    if table_name in WEBAPP_DR_REPLICA_TABLES:
        return "webapp_private_dr"
    return "site_local"


def _destinations(replication_class: str) -> list[str]:
    if replication_class == "product_sync":
        return ["bot_fi", "webapp_fi", "webapp_ir"]
    if replication_class == "webapp_private_dr":
        return ["webapp_fi", "webapp_ir"]
    return []


def _field_classification(table_name: str, field_name: str, replication_class: str) -> dict[str, Any]:
    explicit = sync_field_policy_entries().get((table_name, field_name))
    if replication_class in {"site_local", "internal_control"}:
        classification = SyncFieldClassification.NO_SYNC.value
        action = SyncFieldAction.DROP.value
        reason = "table is not transported by the three-site business event plane"
        source = "table_policy"
    elif field_name in WEBAPP_DR_DROPPED_FIELDS.get(table_name, frozenset()):
        classification = SyncFieldClassification.NO_SYNC.value
        action = SyncFieldAction.DROP.value
        reason = "explicitly excluded from the private WebApp DR stream"
        source = "webapp_dr_exclusion"
    elif explicit is not None:
        classification = explicit.classification.value
        action = explicit.action.value
        reason = explicit.reason or "explicit field policy"
        source = "explicit_field_policy"
    else:
        classification = SyncFieldClassification.SYNC.value
        action = SyncFieldAction.KEEP.value
        reason = "inherits the mapped table replication contract"
        source = "table_policy"
    transported = bool(
        replication_class in {"product_sync", "webapp_private_dr"}
        and action != SyncFieldAction.DROP.value
    )
    inferred_sensitive = any(marker in field_name.lower() for marker in _SENSITIVE_NAME_MARKERS)
    return {
        "action": action,
        "classification": classification,
        "output_field": explicit.output_field if explicit else None,
        "policy_reason": reason,
        "policy_source": source,
        "references_no_sync_table": explicit.references_no_sync_table if explicit else None,
        "sensitive": bool((explicit and explicit.sensitive) or inferred_sensitive),
        "sensitive_source": (
            "explicit" if explicit and explicit.sensitive else "name_inference" if inferred_sensitive else "none"
        ),
        "transported": transported,
    }


def build_three_site_data_matrix() -> dict[str, Any]:
    registry = sync_registry_entries(include_planned=False)
    mapped = dict(Base.metadata.tables)
    missing = sorted(set(mapped) - set(registry))
    stale = sorted(set(registry) - set(mapped))
    if missing or stale:
        raise DrDataMatrixError(
            "registry/schema mismatch: missing=" + ",".join(missing) + ";stale=" + ",".join(stale)
        )
    explicit_fields = sync_field_policy_entries()
    stale_fields = sorted(
        f"{table}.{field}"
        for table, field in explicit_fields
        if (table not in mapped or field not in mapped[table].columns)
        and (table, field) not in SYNC_DERIVED_FIELDS
    )
    if stale_fields:
        raise DrDataMatrixError("field policy references unmapped fields: " + ",".join(stale_fields))

    tables: list[dict[str, Any]] = []
    field_count = 0
    for table_name in sorted(mapped):
        table = mapped[table_name]
        entry = registry[table_name]
        replication_class = _replication_class(table_name, entry.policy)
        fields: list[dict[str, Any]] = []
        for column in table.columns:
            field_count += 1
            policy = _field_classification(table_name, column.name, replication_class)
            fields.append(
                {
                    "foreign_keys": sorted(str(key.target_fullname) for key in column.foreign_keys),
                    "mapped_column": True,
                    "name": column.name,
                    "nullable": bool(column.nullable),
                    "primary_key": bool(column.primary_key),
                    "projection_allowed": policy["transported"],
                    "sql_type": str(column.type),
                    **policy,
                }
            )
        for derived_table, derived_field in sorted(SYNC_DERIVED_FIELDS):
            if derived_table != table_name:
                continue
            field_count += 1
            fields.append(
                {
                    "foreign_keys": [],
                    "mapped_column": False,
                    "name": derived_field,
                    "nullable": True,
                    "primary_key": False,
                    "projection_allowed": True,
                    "sql_type": "DERIVED_CANONICAL_PAYLOAD",
                    **_field_classification(table_name, derived_field, replication_class),
                }
            )
        transported = replication_class in {"product_sync", "webapp_private_dr"}
        tables.append(
            {
                "authority": entry.authority,
                "conflict_rule": entry.conflict_rule,
                "delete_tombstone_rule": (
                    "required_until_all_destinations_and_repair_horizon"
                    if transported
                    else "not_transported"
                ),
                "destinations": _destinations(replication_class),
                "fields": fields,
                "name": table_name,
                "notes": entry.notes,
                "replication_class": replication_class,
                "retention": (
                    "business_retention_plus_all_destination_repair_horizon"
                    if transported
                    else "control_plane_specific"
                    if replication_class == "internal_control"
                    else "site_local_business_or_runtime_policy"
                ),
                "side_effect_classification": entry.side_effect_classification,
                "sync_policy": entry.policy.value,
                "write_surfaces": list(entry.write_surfaces),
            }
        )
    body = {
        "field_count": field_count,
        "schema": MATRIX_SCHEMA,
        "table_count": len(tables),
        "tables": tables,
    }
    body["matrix_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def render_three_site_data_matrix() -> str:
    return json.dumps(build_three_site_data_matrix(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
