"""Closed table, field, and authority policy for the three-site event plane."""

from __future__ import annotations

from typing import Any

from core.runtime_sites import SITE_BOT_FI, SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.sync_authority import IRAN_AUTHORITATIVE_SYNC_TABLES
from core.sync_field_policy import sanitize_sync_payload
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from models.database import Base


WEBAPP_SITES = frozenset({SITE_WEBAPP_FI, SITE_WEBAPP_IR})
WEBAPP_DR_REPLICA_TABLES = frozenset(
    {
        "chat_files",
        "chat_members",
        "chats",
        "conversations",
        "invitation_identity_reservations",
        "invitation_sms_deliveries",
        "messages",
        "push_subscriptions",
        "session_login_requests",
        "single_session_recovery_admin_targets",
        "single_session_recovery_requests",
        "user_sessions",
    }
)
WEBAPP_DR_DROPPED_FIELDS: dict[str, frozenset[str]] = {
    # Provider diagnostics and browser fingerprint metadata are not required
    # to resume delivery after promotion. Endpoint/key material is required and
    # remains confined to the private FI<->IR WebApp stream.
    "push_subscriptions": frozenset({"last_error", "platform", "user_agent"}),
}
WEBAPP_DR_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    # These fields are transported only on the authenticated private WebApp
    # replica stream.  The legacy Bot/WebApp sync policy still hashes/drops
    # them, so Bot-FI can never receive browser endpoint credentials.
    "push_subscriptions": frozenset({"endpoint", "endpoint_hash", "p256dh", "auth"}),
}


def canonical_dr_row_payload(table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Remove local-only fields and normalize location-dependent blob paths."""

    # The legacy field policy describes what may cross the Bot/WebApp trust
    # boundary.  The private WebApp replica set never reaches Bot-FI and must
    # retain required Messenger/session columns so IR is a complete standby.
    if table_name in WEBAPP_DR_REPLICA_TABLES:
        excluded = WEBAPP_DR_DROPPED_FIELDS.get(table_name, frozenset())
        sanitized = {key: value for key, value in payload.items() if key not in excluded}
    else:
        sanitized = sanitize_sync_payload(table_name, payload)
    if not isinstance(sanitized, dict):
        raise ValueError("DR row payload must be an object")
    if table_name == "chat_files" and sanitized.get("content_hash"):
        sanitized["s3_key"] = f"sha256:{sanitized['content_hash']}"
    return sanitized


def event_policy_rejection_reason(
    *,
    table_name: str,
    origin_authority: str,
    origin_site: str,
    destination_site: str | None = None,
    payload: Any,
) -> str | None:
    """Return a stable reason when an immutable event is outside its authority."""

    try:
        entry = get_sync_registry_entry(table_name)
    except KeyError:
        return "unregistered_table"
    if entry.planned or entry.policy == SyncPolicy.INTERNAL_BOOKKEEPING:
        return "table_policy_forbidden"
    if entry.policy == SyncPolicy.NO_SYNC:
        if table_name not in WEBAPP_DR_REPLICA_TABLES:
            return "table_policy_forbidden"
        if origin_authority != "webapp" or origin_site not in WEBAPP_SITES:
            return "webapp_replica_authority_forbidden"
        if destination_site is not None and destination_site not in WEBAPP_SITES:
            return "webapp_replica_destination_forbidden"
    if table_name in IRAN_AUTHORITATIVE_SYNC_TABLES and origin_authority != "webapp":
        return "webapp_authority_required"
    if origin_site == SITE_BOT_FI and origin_authority != "foreign":
        return "origin_authority_mismatch"
    if origin_site in WEBAPP_SITES and origin_authority != "webapp":
        return "origin_authority_mismatch"
    if not isinstance(payload, dict):
        return "payload_not_object"
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return "mapped_table_missing"
    if set(payload) - set(table.columns.keys()):
        return "unknown_payload_fields"
    try:
        sanitized = canonical_dr_row_payload(table_name, payload)
    except Exception:
        return "payload_field_policy_invalid"
    if sanitized != payload:
        return "forbidden_or_unsanitized_payload_fields"
    return None
