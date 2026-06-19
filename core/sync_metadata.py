"""Shared sync payload metadata helpers."""

from __future__ import annotations

import json
from typing import Any


def coerce_positive_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def deserialize_sync_data(raw_data: Any) -> Any:
    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except json.JSONDecodeError:
            return raw_data
    return raw_data


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _authority_server(table_name: str, data: dict[str, Any]) -> str | None:
    if table_name == "offers":
        return _string_or_none(data.get("home_server"))
    if table_name == "offer_requests":
        return _string_or_none(data.get("request_home_server") or data.get("request_source_server"))
    return _string_or_none(
        data.get("home_server")
        or data.get("request_home_server")
        or data.get("request_source_server")
        or data.get("expire_source_server")
        or data.get("source_server")
    )


def _aggregate_identity(table_name: str, record_id: Any, data: dict[str, Any]) -> str:
    if table_name in {"offers", "offer_requests"}:
        public_id = _string_or_none(data.get("offer_public_id"))
        if public_id:
            return public_id
    if table_name == "trades":
        trade_number = _string_or_none(data.get("trade_number"))
        if trade_number:
            return trade_number
    return str(record_id)


def build_sync_metadata(
    table_name: str,
    record_id: Any,
    operation: str,
    data: Any,
    *,
    change_log_id: Any = None,
) -> dict[str, Any]:
    payload_data = data if isinstance(data, dict) else {}
    authoritative_version = coerce_positive_int(payload_data.get("version_id"))
    outbox_id = coerce_positive_int(change_log_id)

    return {
        "aggregate_table": table_name,
        "aggregate_id": _aggregate_identity(table_name, record_id, payload_data),
        "aggregate_db_id": record_id,
        "authority_server": _authority_server(table_name, payload_data),
        "operation": operation,
        "authoritative_version": authoritative_version,
        "event_sequence": authoritative_version or outbox_id,
        "outbox_id": outbox_id,
        "command_idempotency_id": _string_or_none(payload_data.get("idempotency_key")),
    }
