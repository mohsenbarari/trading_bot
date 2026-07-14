"""Field-level sync policy for sensitive and local-only references."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


SYNC_FIELD_POLICY_VERSION = 2


class SyncFieldClassification(str, Enum):
    SYNC = "sync"
    NO_SYNC = "no-sync"
    HASH_ONLY = "hash-only"
    ENCRYPTED_DERIVED = "encrypted/derived"


class SyncFieldAction(str, Enum):
    KEEP = "keep"
    DROP = "drop"
    HASH = "hash"


@dataclass(frozen=True)
class SyncFieldPolicyEntry:
    table_name: str
    field_name: str
    classification: SyncFieldClassification
    action: SyncFieldAction = SyncFieldAction.KEEP
    sensitive: bool = False
    output_field: str | None = None
    references_no_sync_table: str | None = None
    reason: str = ""


def _entry(
    table_name: str,
    field_name: str,
    classification: SyncFieldClassification,
    *,
    action: SyncFieldAction = SyncFieldAction.KEEP,
    sensitive: bool = False,
    output_field: str | None = None,
    references_no_sync_table: str | None = None,
    reason: str = "",
) -> SyncFieldPolicyEntry:
    return SyncFieldPolicyEntry(
        table_name=table_name,
        field_name=field_name,
        classification=classification,
        action=action,
        sensitive=sensitive,
        output_field=output_field,
        references_no_sync_table=references_no_sync_table,
        reason=reason,
    )


_FIELD_POLICIES: dict[tuple[str, str], SyncFieldPolicyEntry] = {
    ("users", "mobile_number"): _entry("users", "mobile_number", SyncFieldClassification.SYNC, sensitive=True),
    ("users", "normalized_account_name"): _entry(
        "users",
        "normalized_account_name",
        SyncFieldClassification.ENCRYPTED_DERIVED,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="database-generated canonical identity derived from account_name",
    ),
    ("users", "normalized_mobile_number"): _entry(
        "users",
        "normalized_mobile_number",
        SyncFieldClassification.ENCRYPTED_DERIVED,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="database-generated canonical identity derived from mobile_number",
    ),
    ("users", "address"): _entry("users", "address", SyncFieldClassification.SYNC, sensitive=True),
    ("users", "telegram_id"): _entry(
        "users",
        "telegram_id",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram identity is shared product state committed by Iran",
    ),
    ("users", "username"): _entry(
        "users",
        "username",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram username/profile identity",
    ),
    ("users", "full_name"): _entry(
        "users",
        "full_name",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="user profile identity",
    ),
    ("users", "admin_password_hash"): _entry(
        "users",
        "admin_password_hash",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="local WebApp admin authentication secret",
    ),
    ("users", "must_change_password"): _entry(
        "users",
        "must_change_password",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="local WebApp password lifecycle state",
    ),
    ("users", "avatar_file_id"): _entry(
        "users",
        "avatar_file_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        references_no_sync_table="chat_files",
        reason="raw avatar file FK points to Iran-only messenger/upload storage",
    ),
    ("trades", "offer_user_mobile"): _entry("trades", "offer_user_mobile", SyncFieldClassification.SYNC, sensitive=True),
    ("trades", "responder_user_mobile"): _entry("trades", "responder_user_mobile", SyncFieldClassification.SYNC, sensitive=True),
    ("invitations", "mobile_number"): _entry("invitations", "mobile_number", SyncFieldClassification.SYNC, sensitive=True),
    ("invitations", "token"): _entry("invitations", "token", SyncFieldClassification.SYNC, sensitive=True),
    ("invitations", "short_code"): _entry(
        "invitations",
        "short_code",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="short invitation credential used to resolve the full token",
    ),
    ("invitation_identity_reservations", "normalized_mobile"): _entry(
        "invitation_identity_reservations",
        "normalized_mobile",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="Iran-local pending identity reservation",
    ),
    ("invitation_identity_reservations", "normalized_account_name"): _entry(
        "invitation_identity_reservations",
        "normalized_account_name",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="Iran-local pending identity reservation",
    ),
    ("telegram_registration_intents", "invitation_token"): _entry(
        "telegram_registration_intents",
        "invitation_token",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local Telegram registration secret",
    ),
    ("telegram_registration_intents", "normalized_mobile"): _entry(
        "telegram_registration_intents",
        "normalized_mobile",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local registration identity",
    ),
    ("telegram_registration_intents", "telegram_id"): _entry(
        "telegram_registration_intents",
        "telegram_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local registration identity",
    ),
    ("telegram_registration_intents", "telegram_username"): _entry(
        "telegram_registration_intents",
        "telegram_username",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local Telegram profile snapshot",
    ),
    ("telegram_registration_intents", "telegram_full_name"): _entry(
        "telegram_registration_intents",
        "telegram_full_name",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local Telegram profile snapshot",
    ),
    ("telegram_registration_intents", "address"): _entry(
        "telegram_registration_intents",
        "address",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="foreign-local registration address",
    ),
    ("telegram_registration_command_receipts", "request_hash"): _entry(
        "telegram_registration_command_receipts",
        "request_hash",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="Iran-local command replay fingerprint",
    ),
    ("telegram_registration_command_receipts", "invitation_token_hash"): _entry(
        "telegram_registration_command_receipts",
        "invitation_token_hash",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="Iran-local invitation reference hash",
    ),
    ("offer_expiry_command_receipts", "request_hash"): _entry(
        "offer_expiry_command_receipts",
        "request_hash",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="home-local forwarded expiry command fingerprint",
    ),
    ("telegram_link_tokens", "token_hash"): _entry(
        "telegram_link_tokens",
        "token_hash",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="hash of a short-lived WebApp-issued Telegram link token",
    ),
    ("telegram_link_tokens", "used_telegram_id"): _entry(
        "telegram_link_tokens",
        "used_telegram_id",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram account id used for link audit",
    ),
    ("telegram_admin_broadcasts", "content"): _entry(
        "telegram_admin_broadcasts",
        "content",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="administrator-authored Telegram broadcast message content",
    ),
    ("telegram_admin_broadcast_receipts", "telegram_id_at_enqueue"): _entry(
        "telegram_admin_broadcast_receipts",
        "telegram_id_at_enqueue",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram account id snapshot for broadcast delivery audit",
    ),
    ("telegram_admin_broadcast_receipts", "telegram_id_at_send"): _entry(
        "telegram_admin_broadcast_receipts",
        "telegram_id_at_send",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram account id used at send time for delivery audit",
    ),
    ("telegram_admin_broadcast_receipts", "last_error_message"): _entry(
        "telegram_admin_broadcast_receipts",
        "last_error_message",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram provider diagnostics may contain account-specific text",
    ),
    ("telegram_admin_broadcast_receipts", "worker_id"): _entry(
        "telegram_admin_broadcast_receipts",
        "worker_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local Telegram broadcast delivery lease owner; opposite server must not use it for execution",
    ),
    ("telegram_admin_broadcast_receipts", "lease_until"): _entry(
        "telegram_admin_broadcast_receipts",
        "lease_until",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local Telegram broadcast delivery lease deadline; opposite server must not use it for execution",
    ),
    ("telegram_notification_outbox", "text"): _entry(
        "telegram_notification_outbox",
        "text",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="private Telegram notification message content",
    ),
    ("telegram_notification_outbox", "telegram_id_at_enqueue"): _entry(
        "telegram_notification_outbox",
        "telegram_id_at_enqueue",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram account id snapshot for notification delivery audit",
    ),
    ("telegram_notification_outbox", "telegram_id_at_send"): _entry(
        "telegram_notification_outbox",
        "telegram_id_at_send",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram account id used at send time for delivery audit",
    ),
    ("telegram_notification_outbox", "last_error_message"): _entry(
        "telegram_notification_outbox",
        "last_error_message",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="Telegram provider diagnostics may contain account-specific text",
    ),
    ("telegram_notification_outbox", "extra_payload"): _entry(
        "telegram_notification_outbox",
        "extra_payload",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="delivery policy metadata may contain recipient routing context",
    ),
    ("telegram_notification_outbox", "worker_id"): _entry(
        "telegram_notification_outbox",
        "worker_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local Telegram notification outbox lease owner; opposite server must not use it for execution",
    ),
    ("telegram_notification_outbox", "lease_until"): _entry(
        "telegram_notification_outbox",
        "lease_until",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local Telegram notification outbox lease deadline; opposite server must not use it for execution",
    ),
    ("accountant_relations", "mobile_number"): _entry(
        "accountant_relations",
        "mobile_number",
        SyncFieldClassification.SYNC,
        sensitive=True,
    ),
    ("accountant_relations", "invitation_token"): _entry(
        "accountant_relations",
        "invitation_token",
        SyncFieldClassification.SYNC,
        sensitive=True,
    ),
    ("customer_relations", "invitation_token"): _entry(
        "customer_relations",
        "invitation_token",
        SyncFieldClassification.SYNC,
        sensitive=True,
    ),
    ("offer_requests", "customer_relation_invitation_token"): _entry(
        "offer_requests",
        "customer_relation_invitation_token",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="stable relationship identity used to localize the target-side customer relation FK",
    ),
    ("notifications", "message"): _entry("notifications", "message", SyncFieldClassification.SYNC, sensitive=True),
    ("notifications", "extra_payload"): _entry(
        "notifications",
        "extra_payload",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="trade notification routing and recipient metadata persisted for history/support",
    ),
    ("trade_delivery_receipts", "last_error"): _entry(
        "trade_delivery_receipts",
        "last_error",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="delivery diagnostics may contain provider or account-specific error text",
    ),
    ("trade_delivery_receipts", "audit_payload"): _entry(
        "trade_delivery_receipts",
        "audit_payload",
        SyncFieldClassification.SYNC,
        sensitive=True,
        reason="operator audit payload may include trade notification metadata",
    ),
    ("trade_delivery_receipts", "trade_id"): _entry(
        "trade_delivery_receipts",
        "trade_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local FK; shared receipt identity uses trade_number and dedupe_key",
    ),
    ("trade_delivery_receipts", "offer_id"): _entry(
        "trade_delivery_receipts",
        "offer_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local FK derived from the local trade row during sync receive",
    ),
    ("trade_delivery_receipts", "notification_id"): _entry(
        "trade_delivery_receipts",
        "notification_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local WebApp notification row id; opposite server must not reuse it",
    ),
    ("trade_delivery_receipts", "worker_id"): _entry(
        "trade_delivery_receipts",
        "worker_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local delivery lease owner; opposite server must not use it for execution",
    ),
    ("trade_delivery_receipts", "lease_until"): _entry(
        "trade_delivery_receipts",
        "lease_until",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local delivery lease deadline; opposite server must not use it for execution",
    ),
    ("offer_publication_states", "offer_id"): _entry(
        "offer_publication_states",
        "offer_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="local FK; shared publication identity uses offer_public_id and dedupe_key",
    ),
    ("offer_publication_states", "surface_resource_id"): _entry(
        "offer_publication_states",
        "surface_resource_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="provider/runtime resource id is owned by the publication surface",
    ),
    ("offer_publication_states", "telegram_chat_id"): _entry(
        "offer_publication_states",
        "telegram_chat_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="Telegram channel id is foreign-local runtime execution evidence",
    ),
    ("offer_publication_states", "telegram_message_id"): _entry(
        "offer_publication_states",
        "telegram_message_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="Telegram message id is foreign-local runtime execution evidence",
    ),
    ("offer_publication_states", "last_attempt_at"): _entry(
        "offer_publication_states",
        "last_attempt_at",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="publication retry timing is local execution state",
    ),
    ("offer_publication_states", "last_success_at"): _entry(
        "offer_publication_states",
        "last_success_at",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="publication success timing is local execution state",
    ),
    ("offer_publication_states", "next_retry_at"): _entry(
        "offer_publication_states",
        "next_retry_at",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="publication retry timing is local execution state",
    ),
    ("offer_publication_states", "error_code"): _entry(
        "offer_publication_states",
        "error_code",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="provider error code is local execution state",
    ),
    ("offer_publication_states", "error_message"): _entry(
        "offer_publication_states",
        "error_message",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="provider error detail may contain local runtime diagnostics",
    ),
    ("offer_publication_states", "state_metadata"): _entry(
        "offer_publication_states",
        "state_metadata",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="provider/runtime metadata is not shared product truth",
    ),
    ("push_subscriptions", "endpoint"): _entry(
        "push_subscriptions",
        "endpoint",
        SyncFieldClassification.HASH_ONLY,
        action=SyncFieldAction.HASH,
        sensitive=True,
        output_field="endpoint_hash",
        reason="browser push endpoints are Iran-local runtime secrets",
    ),
    ("push_subscriptions", "p256dh"): _entry(
        "push_subscriptions",
        "p256dh",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="browser push key material is Iran-local runtime state",
    ),
    ("push_subscriptions", "auth"): _entry(
        "push_subscriptions",
        "auth",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="browser push auth secret is Iran-local runtime state",
    ),
    ("push_subscriptions", "user_agent"): _entry(
        "push_subscriptions",
        "user_agent",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="browser runtime fingerprint is not product sync data",
    ),
    ("push_subscriptions", "platform"): _entry(
        "push_subscriptions",
        "platform",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        reason="browser runtime metadata is Iran-local",
    ),
    ("push_subscriptions", "last_error"): _entry(
        "push_subscriptions",
        "last_error",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="local Web Push delivery diagnostics must not cross servers",
    ),
    ("upload_sessions", "resume_token"): _entry(
        "upload_sessions",
        "resume_token",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        sensitive=True,
        reason="upload resume token is local runtime auth material",
    ),
    ("chats", "avatar_file_id"): _entry(
        "chats",
        "avatar_file_id",
        SyncFieldClassification.NO_SYNC,
        action=SyncFieldAction.DROP,
        references_no_sync_table="chat_files",
        reason="chat avatar file FK points to no-sync media storage",
    ),
}


def sync_field_policy_entries() -> dict[tuple[str, str], SyncFieldPolicyEntry]:
    return dict(_FIELD_POLICIES)


def get_sync_field_policy_entry(table_name: str, field_name: str) -> SyncFieldPolicyEntry | None:
    return _FIELD_POLICIES.get((str(table_name), str(field_name)))


def _hash_value(value: Any) -> str | None:
    if value is None:
        return None
    encoded = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_sync_payload(table_name: str, data: Any) -> Any:
    """Apply field policy to one sync row payload.

    The function is intentionally tolerant: non-dict payloads are returned
    unchanged so legacy error paths can continue to report malformed items.
    """
    if not isinstance(data, Mapping):
        return data

    table = str(table_name)
    sanitized: dict[str, Any] = {}
    for raw_key, value in data.items():
        key = str(raw_key)
        entry = get_sync_field_policy_entry(table, key)
        if entry is None:
            sanitized[key] = value
            continue

        if entry.action == SyncFieldAction.DROP:
            continue
        if entry.action == SyncFieldAction.HASH:
            output_field = entry.output_field or f"{key}_hash"
            sanitized[output_field] = _hash_value(value)
            continue
        sanitized[key] = value
    return sanitized


def sync_log_payload_context(table_name: str, data: Any) -> dict[str, Any]:
    """Return log-safe field context without raw sensitive values."""
    if not isinstance(data, Mapping):
        return {"data_kind": type(data).__name__}

    table = str(table_name)
    field_names = [str(key) for key in data.keys()]
    sensitive_fields: list[str] = []
    dropped_fields: list[str] = []
    no_sync_reference_fields: list[str] = []
    hash_only_fields: list[str] = []

    for field_name in field_names:
        entry = get_sync_field_policy_entry(table, field_name)
        if entry is None:
            continue
        if entry.sensitive:
            sensitive_fields.append(field_name)
        if entry.action == SyncFieldAction.DROP:
            dropped_fields.append(field_name)
        if entry.references_no_sync_table:
            no_sync_reference_fields.append(field_name)
        if entry.classification == SyncFieldClassification.HASH_ONLY:
            hash_only_fields.append(field_name)

    return {
        "data_kind": "dict",
        "data_key_count": len(field_names),
        "sensitive_field_count": len(sensitive_fields),
        "sensitive_fields": sorted(sensitive_fields),
        "dropped_fields": sorted(dropped_fields),
        "hash_only_fields": sorted(hash_only_fields),
        "no_sync_reference_fields": sorted(no_sync_reference_fields),
    }


def sync_field_policy_fingerprint_payload() -> list[dict[str, Any]]:
    entries = sorted(_FIELD_POLICIES.values(), key=lambda entry: (entry.table_name, entry.field_name))
    return [
        {
            "table": entry.table_name,
            "field": entry.field_name,
            "classification": entry.classification.value,
            "action": entry.action.value,
            "sensitive": entry.sensitive,
            "output_field": entry.output_field,
            "references_no_sync_table": entry.references_no_sync_table,
        }
        for entry in entries
    ]


def sync_field_policy_fingerprint() -> str:
    encoded = json.dumps(
        sync_field_policy_fingerprint_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
