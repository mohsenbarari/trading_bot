"""Source-aware registration sync contract used behind the Stage 1 feature flag."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.server_routing import SERVER_FOREIGN, SERVER_IRAN
from core.user_counter_sync import (
    USER_COUNTER_EVENT_DELTAS_FIELD,
    USER_COUNTER_EVENT_EPOCH_FIELD,
    USER_COUNTER_EVENT_ID_FIELD,
    USER_COUNTER_EVENT_KIND_FIELD,
    USER_COUNTER_EVENT_OCCURRED_AT_FIELD,
    USER_COUNTER_SYNC_CONTRACT,
    USER_COUNTER_SYNC_CONTRACT_FIELD,
    USER_SYNC_IDENTITY_FIELD,
    is_user_counter_event_payload,
    normalize_counter_event_occurred_at,
)


REGISTRATION_VERSIONED_TABLES = frozenset(
    {"users", "invitations", "customer_relations", "accountant_relations"}
)

USER_SYNC_IDENTITY_FIELDS = frozenset(
    {
        "telegram_id",
        "username",
        "full_name",
        "mobile_number",
        "account_name",
        "address",
        "role",
        "account_status",
        "deactivated_at",
        "messenger_grace_expires_at",
        "messenger_blocked_at",
        "has_bot_access",
        "home_server",
        "is_deleted",
        "deleted_at",
        "can_block_users",
        "max_blocked_users",
        "max_daily_trades",
        "max_active_commodities",
        "max_daily_requests",
        "trading_restricted_until",
        "limitations_expire_at",
        "max_sessions",
        "max_accountants",
        "max_customers",
    }
)
USER_SYNC_FOREIGN_FIELDS = frozenset(
    {
        "bot_onboarding_required_step",
        "bot_onboarding_completed_step",
        "bot_onboarding_completed_at",
    }
)
USER_SYNC_SHARED_FIELDS = frozenset({"last_seen_at"})
USER_SYNC_COUNTER_FIELDS = frozenset(
    {"trades_count", "commodities_traded_count", "channel_messages_count"}
)
USER_SYNC_METADATA_FIELDS = frozenset(
    {"id", "created_at", "updated_at", "sync_version", USER_SYNC_IDENTITY_FIELD}
)

USER_COUNTER_EVENT_FIELDS = frozenset(
    {
        "id",
        USER_COUNTER_SYNC_CONTRACT_FIELD,
        USER_COUNTER_EVENT_ID_FIELD,
        USER_COUNTER_EVENT_KIND_FIELD,
        USER_COUNTER_EVENT_EPOCH_FIELD,
        USER_COUNTER_EVENT_DELTAS_FIELD,
        USER_COUNTER_EVENT_OCCURRED_AT_FIELD,
        USER_SYNC_IDENTITY_FIELD,
    }
)


@dataclass(frozen=True)
class RegistrationSyncPayloadDecision:
    accepted: bool
    data: dict[str, Any]
    dropped_fields: tuple[str, ...] = ()
    reason: str | None = None


def registration_sync_capabilities(settings_obj) -> dict[str, object]:
    return {
        "schema_version": 2,
        "versioned_events_supported": True,
        "v2_enabled": bool(getattr(settings_obj, "registration_sync_v2_enabled", False)),
        "accept_unversioned": bool(
            getattr(settings_obj, "registration_sync_accept_unversioned", True)
        ),
    }


def allowed_user_fields_for_source(source_server: str) -> frozenset[str]:
    source = str(source_server or "").strip().lower()
    if source == SERVER_IRAN:
        return USER_SYNC_IDENTITY_FIELDS | USER_SYNC_SHARED_FIELDS | USER_SYNC_METADATA_FIELDS
    if source == SERVER_FOREIGN:
        return USER_SYNC_FOREIGN_FIELDS | USER_SYNC_SHARED_FIELDS | USER_SYNC_METADATA_FIELDS
    return frozenset()


def sanitize_registration_sync_payload(
    *,
    table: str,
    operation: str,
    data: Mapping[str, Any],
    source_server: str | None,
    v2_enabled: bool,
    accept_unversioned: bool,
) -> RegistrationSyncPayloadDecision:
    payload = {str(key): value for key, value in data.items()}
    if not v2_enabled or table not in REGISTRATION_VERSIONED_TABLES:
        return RegistrationSyncPayloadDecision(True, payload)

    source = str(source_server or "").strip().lower()
    if source not in {SERVER_IRAN, SERVER_FOREIGN}:
        return RegistrationSyncPayloadDecision(False, {}, reason="missing_or_invalid_source_server")

    if table == "users" and is_user_counter_event_payload(payload):
        return _sanitize_user_counter_event(
            operation=operation,
            payload=payload,
            source_server=source,
        )

    has_version = payload.get("sync_version") is not None
    if not has_version and not accept_unversioned:
        return RegistrationSyncPayloadDecision(False, {}, reason="unversioned_event_forbidden")
    if has_version:
        try:
            normalized_version = int(payload["sync_version"])
        except (TypeError, ValueError):
            return RegistrationSyncPayloadDecision(False, {}, reason="invalid_sync_version")
        if normalized_version < 1:
            return RegistrationSyncPayloadDecision(False, {}, reason="invalid_sync_version")
        payload["sync_version"] = normalized_version

    if table != "users":
        if source != SERVER_IRAN:
            return RegistrationSyncPayloadDecision(False, {}, reason=f"source_authority_forbidden:{source}")
        return RegistrationSyncPayloadDecision(True, payload)

    if operation in {"INSERT", "DELETE"} and source != SERVER_IRAN:
        return RegistrationSyncPayloadDecision(False, {}, reason=f"source_authority_forbidden:{source}")

    if has_version and not _has_user_sync_identity(payload.get(USER_SYNC_IDENTITY_FIELD)):
        return RegistrationSyncPayloadDecision(False, {}, reason="versioned_user_identity_missing")

    allowed = allowed_user_fields_for_source(source)
    sanitized = {key: value for key, value in payload.items() if key in allowed}
    dropped = tuple(sorted(set(payload) - set(sanitized)))
    return RegistrationSyncPayloadDecision(True, sanitized, dropped_fields=dropped)


def _has_user_sync_identity(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    for section_name in ("current", "previous"):
        section = value.get(section_name)
        if not isinstance(section, Mapping):
            continue
        if any(
            section.get(field_name) is not None and section.get(field_name) != ""
            for field_name in ("account_name", "mobile_number", "telegram_id")
        ):
            return True
    return False


def _sanitize_user_counter_event(
    *,
    operation: str,
    payload: dict[str, Any],
    source_server: str,
) -> RegistrationSyncPayloadDecision:
    from uuid import UUID

    if operation != "UPDATE":
        return RegistrationSyncPayloadDecision(False, {}, reason="counter_event_operation_forbidden")
    try:
        event_id = str(UUID(str(payload.get(USER_COUNTER_EVENT_ID_FIELD))))
        epoch = int(payload.get(USER_COUNTER_EVENT_EPOCH_FIELD))
        occurred_at = normalize_counter_event_occurred_at(
            payload.get(USER_COUNTER_EVENT_OCCURRED_AT_FIELD)
        )
    except (TypeError, ValueError, AttributeError):
        return RegistrationSyncPayloadDecision(False, {}, reason="invalid_counter_event_metadata")
    if epoch < 1:
        return RegistrationSyncPayloadDecision(False, {}, reason="invalid_counter_event_metadata")

    kind = str(payload.get(USER_COUNTER_EVENT_KIND_FIELD) or "")
    raw_deltas = payload.get(USER_COUNTER_EVENT_DELTAS_FIELD)
    if kind not in {"increment", "reset"} or not isinstance(raw_deltas, Mapping):
        return RegistrationSyncPayloadDecision(False, {}, reason="invalid_counter_event_payload")
    if kind == "reset" and source_server != SERVER_IRAN:
        return RegistrationSyncPayloadDecision(False, {}, reason="counter_reset_source_forbidden")
    try:
        deltas = {str(key): int(value) for key, value in raw_deltas.items()}
    except (TypeError, ValueError):
        return RegistrationSyncPayloadDecision(False, {}, reason="invalid_counter_event_payload")
    if (
        set(deltas) - set(USER_SYNC_COUNTER_FIELDS)
        or any(value < 0 for value in deltas.values())
        or (kind == "increment" and not any(value > 0 for value in deltas.values()))
        or (kind == "reset" and any(value != 0 for value in deltas.values()))
    ):
        return RegistrationSyncPayloadDecision(False, {}, reason="invalid_counter_event_payload")
    identity = payload.get(USER_SYNC_IDENTITY_FIELD)
    if not _has_user_sync_identity(identity):
        return RegistrationSyncPayloadDecision(False, {}, reason="counter_event_identity_missing")

    sanitized = {key: value for key, value in payload.items() if key in USER_COUNTER_EVENT_FIELDS}
    sanitized[USER_COUNTER_SYNC_CONTRACT_FIELD] = USER_COUNTER_SYNC_CONTRACT
    sanitized[USER_COUNTER_EVENT_ID_FIELD] = event_id
    sanitized[USER_COUNTER_EVENT_EPOCH_FIELD] = epoch
    sanitized[USER_COUNTER_EVENT_KIND_FIELD] = kind
    sanitized[USER_COUNTER_EVENT_DELTAS_FIELD] = deltas
    sanitized[USER_COUNTER_EVENT_OCCURRED_AT_FIELD] = occurred_at.isoformat()
    dropped = tuple(sorted(set(payload) - set(sanitized)))
    return RegistrationSyncPayloadDecision(True, sanitized, dropped_fields=dropped)
