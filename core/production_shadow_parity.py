"""Pure, redacted parity snapshot validation and comparison helpers.

This module intentionally depends on the Python standard library only.  It is
used by controller-local source-set preparation, which must be importable
without loading database models or runtime configuration.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any


SYNC_PARITY_SCHEMA_VERSION = 1


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


def business_snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """Return the deterministic business-only fingerprint for a parity snapshot.

    The function deliberately refuses malformed/truncated input.  A local
    runtime field (for example a Telegram message id or a worker lease) is
    allowed to differ between physical sites, while an omitted row, duplicate
    natural identity, or changed business value is not.  Callers must still
    use :func:`compare_parity_snapshots` for the detailed mismatch counts.
    """

    tables = snapshot.get("tables") if isinstance(snapshot, Mapping) else None
    if not isinstance(tables, Mapping) or not tables:
        raise ValueError("parity snapshot has no tables")
    normalized: list[dict[str, Any]] = []
    for table_name in sorted(str(name) for name in tables):
        table = tables.get(table_name)
        if not isinstance(table, Mapping) or bool(table.get("truncated")):
            raise ValueError("parity snapshot is incomplete")
        records = table.get("records")
        if not isinstance(records, list):
            raise ValueError("parity table records are invalid")
        row_count = table.get("row_count")
        if type(row_count) is not int or row_count != len(records):
            raise ValueError("parity table row count is invalid")
        duplicate_count = table.get("duplicate_identity_count")
        if type(duplicate_count) is not int or duplicate_count != 0:
            raise ValueError("parity snapshot has duplicate identities")
        entries: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("parity record is invalid")
            identity_hash = str(record.get("identity_hash") or "")
            business_hash = str(record.get("business_hash") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", identity_hash) or not re.fullmatch(
                r"[0-9a-f]{64}", business_hash
            ):
                raise ValueError("parity record hash is invalid")
            entries.append(
                {"identity_hash": identity_hash, "business_hash": business_hash}
            )
        entries.sort(key=lambda item: item["identity_hash"])
        if len({item["identity_hash"] for item in entries}) != len(entries):
            raise ValueError("parity snapshot has duplicate identities")
        full_entries = [
            {
                "identity_hash": str(record.get("identity_hash") or ""),
                "business_hash": str(record.get("business_hash") or ""),
                "local_only_hash": str(record.get("local_only_hash") or ""),
                "volatile_hash": str(record.get("volatile_hash") or ""),
            }
            for record in records
        ]
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", item[field])
            for item in full_entries
            for field in item
        ) or table.get("records_hash") != _hash_payload(full_entries):
            raise ValueError("parity table record fingerprint is invalid")
        if table.get("business_records_hash") != _hash_payload(entries):
            raise ValueError("parity table business fingerprint is invalid")
        normalized.append({"table": table_name, "records": entries})
    return _hash_payload(normalized)


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
