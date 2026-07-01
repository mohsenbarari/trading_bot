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
    if table_name == "trade_delivery_receipts":
        return _string_or_none(data.get("destination_server"))
    if table_name == "offer_publication_states":
        return _string_or_none(data.get("publication_owner_server"))
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
    if table_name in {"accountant_relations", "customer_relations"}:
        invitation_token = _string_or_none(data.get("invitation_token"))
        if invitation_token:
            return invitation_token
    if table_name == "commodities":
        name = _string_or_none(data.get("name"))
        if name:
            return name
    if table_name == "commodity_aliases":
        alias = _string_or_none(data.get("alias"))
        if alias:
            return alias
    if table_name == "telegram_link_tokens":
        token_hash = _string_or_none(data.get("token_hash"))
        if token_hash:
            return token_hash
    if table_name == "invitations":
        token = _string_or_none(data.get("token"))
        if token:
            return token
    if table_name == "notifications":
        dedupe_key = _string_or_none(data.get("dedupe_key"))
        if dedupe_key:
            return dedupe_key
    if table_name == "user_notification_preferences":
        user_id = coerce_positive_int(data.get("user_id"))
        if user_id is not None:
            return str(user_id)
    if table_name == "market_schedule_overrides":
        override_date = _string_or_none(data.get("date"))
        if override_date:
            return override_date
    if table_name == "offer_publication_states":
        dedupe_key = _string_or_none(data.get("dedupe_key"))
        if dedupe_key:
            return dedupe_key
        public_id = _string_or_none(data.get("offer_public_id"))
        surface = _string_or_none(data.get("surface"))
        if public_id and surface:
            return f"{surface}:{public_id}"
    if table_name == "trade_delivery_receipts":
        dedupe_key = _string_or_none(data.get("dedupe_key"))
        if dedupe_key:
            return dedupe_key
        event_type = _string_or_none(data.get("event_type"))
        trade_number = _string_or_none(data.get("trade_number"))
        recipient_user_id = _string_or_none(data.get("recipient_user_id"))
        channel = _string_or_none(data.get("channel"))
        if event_type and trade_number and recipient_user_id and channel:
            return f"{event_type}:{channel}:{trade_number}:{recipient_user_id}"
    if table_name == "telegram_admin_broadcast_receipts":
        dedupe_key = _string_or_none(data.get("dedupe_key"))
        if dedupe_key:
            return dedupe_key
        broadcast_id = _string_or_none(data.get("broadcast_id"))
        recipient_user_id = _string_or_none(data.get("recipient_user_id"))
        if broadcast_id and recipient_user_id:
            return f"{broadcast_id}:{recipient_user_id}"
    if table_name == "telegram_notification_outbox":
        dedupe_key = _string_or_none(data.get("dedupe_key"))
        if dedupe_key:
            return dedupe_key
    if table_name == "user_blocks":
        blocker_id = coerce_positive_int(data.get("blocker_id"))
        blocked_id = coerce_positive_int(data.get("blocked_id"))
        if blocker_id and blocked_id:
            return f"{blocker_id}:{blocked_id}"
    if table_name == "offer_requests":
        request_home_server = _string_or_none(data.get("request_home_server"))
        idempotency_key = _string_or_none(data.get("idempotency_key"))
        if request_home_server and idempotency_key:
            return f"{request_home_server}:{idempotency_key}"
    if table_name == "trading_settings":
        key = _string_or_none(data.get("key"))
        if key:
            return key
    if table_name == "offers":
        public_id = _string_or_none(data.get("offer_public_id"))
        if public_id:
            return public_id
    if table_name == "trades":
        trade_number = _string_or_none(data.get("trade_number"))
        if trade_number:
            return trade_number
    return str(record_id)


def build_sync_public_identity(table_name: str, record_id: Any, data: Any) -> dict[str, Any] | None:
    payload_data = data if isinstance(data, dict) else {}
    references: dict[str, str] = {}
    offer_public_id = _string_or_none(payload_data.get("offer_public_id"))
    if offer_public_id:
        references["offer_public_id"] = offer_public_id

    if table_name == "offers" and offer_public_id:
        return {
            "table": table_name,
            "kind": "offer_public_id",
            "value": offer_public_id,
            "record_id": record_id,
        }

    if table_name == "trades":
        trade_number = _string_or_none(payload_data.get("trade_number"))
        if trade_number:
            identity = {
                "table": table_name,
                "kind": "trade_number",
                "value": trade_number,
                "record_id": record_id,
            }
            if references:
                identity["references"] = references
            return identity

    if table_name == "offer_publication_states":
        dedupe_key = _string_or_none(payload_data.get("dedupe_key"))
        if dedupe_key:
            identity = {
                "table": table_name,
                "kind": "dedupe_key",
                "value": dedupe_key,
                "record_id": record_id,
            }
            if references:
                identity["references"] = references
            return identity

    if table_name == "trade_delivery_receipts":
        dedupe_key = _string_or_none(payload_data.get("dedupe_key"))
        if dedupe_key:
            identity = {
                "table": table_name,
                "kind": "dedupe_key",
                "value": dedupe_key,
                "record_id": record_id,
            }
            trade_number = _string_or_none(payload_data.get("trade_number"))
            if trade_number:
                identity["references"] = {"trade_number": trade_number}
            return identity

    if table_name == "telegram_admin_broadcast_receipts":
        dedupe_key = _string_or_none(payload_data.get("dedupe_key"))
        if dedupe_key:
            identity = {
                "table": table_name,
                "kind": "dedupe_key",
                "value": dedupe_key,
                "record_id": record_id,
            }
            broadcast_id = _string_or_none(payload_data.get("broadcast_id"))
            if broadcast_id:
                identity["references"] = {"broadcast_id": broadcast_id}
            return identity

    if table_name == "telegram_notification_outbox":
        dedupe_key = _string_or_none(payload_data.get("dedupe_key"))
        if dedupe_key:
            identity = {
                "table": table_name,
                "kind": "dedupe_key",
                "value": dedupe_key,
                "record_id": record_id,
            }
            references = {
                key: value
                for key, value in {
                    "source_type": _string_or_none(payload_data.get("source_type")),
                    "source_id": _string_or_none(payload_data.get("source_id")),
                    "recipient_user_id": _string_or_none(payload_data.get("recipient_user_id")),
                }.items()
                if value
            }
            if references:
                identity["references"] = references
            return identity

    if table_name == "offer_requests":
        request_home_server = _string_or_none(payload_data.get("request_home_server"))
        idempotency_key = _string_or_none(payload_data.get("idempotency_key"))
        if request_home_server and idempotency_key:
            identity = {
                "table": table_name,
                "kind": "request_home_server:idempotency_key",
                "value": f"{request_home_server}:{idempotency_key}",
                "record_id": record_id,
            }
            if references:
                identity["references"] = references
            return identity
        if offer_public_id:
            return {
                "table": table_name,
                "kind": "offer_public_id",
                "value": offer_public_id,
                "record_id": record_id,
            }

    simple_identity_keys = {
        "accountant_relations": "invitation_token",
        "commodities": "name",
        "commodity_aliases": "alias",
        "customer_relations": "invitation_token",
        "invitations": "token",
        "market_schedule_overrides": "date",
        "notifications": "dedupe_key",
        "telegram_link_tokens": "token_hash",
        "telegram_admin_broadcast_receipts": "dedupe_key",
        "telegram_notification_outbox": "dedupe_key",
        "user_notification_preferences": "user_id",
    }
    simple_key = simple_identity_keys.get(table_name)
    if simple_key:
        simple_value = _string_or_none(payload_data.get(simple_key))
        if simple_value:
            return {
                "table": table_name,
                "kind": simple_key,
                "value": simple_value,
                "record_id": record_id,
            }

    if references:
        return {
            "table": table_name,
            "kind": "referenced_offer_public_id",
            "value": offer_public_id,
            "record_id": record_id,
            "references": references,
        }
    return None


def build_sync_metadata(
    table_name: str,
    record_id: Any,
    operation: str,
    data: Any,
    *,
    change_log_id: Any = None,
    source_server: Any = None,
) -> dict[str, Any]:
    payload_data = data if isinstance(data, dict) else {}
    authoritative_version = coerce_positive_int(payload_data.get("version_id"))
    outbox_id = coerce_positive_int(change_log_id)
    source_server_value = _string_or_none(source_server)
    if source_server_value is None:
        try:
            from core.server_routing import current_server

            source_server_value = current_server()
        except Exception:
            source_server_value = None

    return {
        "aggregate_table": table_name,
        "aggregate_id": _aggregate_identity(table_name, record_id, payload_data),
        "aggregate_db_id": record_id,
        "source_server": source_server_value,
        "source_sequence": outbox_id,
        "authority_server": _authority_server(table_name, payload_data),
        "operation": operation,
        "authoritative_version": authoritative_version,
        "event_sequence": authoritative_version or outbox_id,
        "outbox_id": outbox_id,
        "command_idempotency_id": _string_or_none(payload_data.get("idempotency_key")),
    }
