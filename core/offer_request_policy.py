"""Field visibility and legacy mapping policy for offer links/request ledger."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any


class OfferRequestVisibility(str, Enum):
    PUBLIC_LINK = "public_link"
    REQUESTER = "requester"
    OWNER = "owner"
    ADMIN_AUDIT = "admin_audit"


PUBLIC_OFFER_REQUEST_FIELDS = frozenset(
    {
        "offer_public_id",
        "requested_quantity",
        "result_status",
        "public_failure_code",
        "public_failure_message",
        "received_at",
        "decided_at",
    }
)

REQUESTER_OFFER_REQUEST_FIELDS = PUBLIC_OFFER_REQUEST_FIELDS | frozenset(
    {
        "id",
        "requester_user_id",
        "actor_user_id",
        "resulting_trade_id",
    }
)

OWNER_AUDIT_OFFER_REQUEST_FIELDS = REQUESTER_OFFER_REQUEST_FIELDS | frozenset(
    {
        "local_offer_id",
        "request_home_server",
        "request_source_surface",
        "request_source_server",
        "idempotency_key",
        "customer_relation_id",
        "customer_owner_user_id",
        "customer_tier_snapshot",
        "customer_management_name_snapshot",
        "customer_commission_rate_snapshot",
        "customer_commission_context",
    }
)

ADMIN_AUDIT_OFFER_REQUEST_FIELDS = OWNER_AUDIT_OFFER_REQUEST_FIELDS | frozenset(
    {
        "internal_failure_code",
        "internal_failure_context",
        "version_id",
        "archived",
        "created_at",
        "updated_at",
    }
)

SENSITIVE_OFFER_REQUEST_FIELDS = frozenset(
    {
        "requester_user_id",
        "actor_user_id",
        "request_source_server",
        "idempotency_key",
        "internal_failure_code",
        "internal_failure_context",
        "customer_relation_id",
        "customer_owner_user_id",
        "customer_tier_snapshot",
        "customer_management_name_snapshot",
        "customer_commission_rate_snapshot",
        "customer_commission_context",
    }
)


@dataclass(frozen=True)
class ExpiryReasonMapping:
    legacy_reason: str
    normalized_category: str
    default_source_surface: str
    metadata_known: bool


_EXPIRY_REASON_MAP = {
    "time_limit": ExpiryReasonMapping("time_limit", "lifetime_expiry", "system", True),
    "manual": ExpiryReasonMapping("manual", "owner_action", "legacy_unknown", False),
    "cancel_all": ExpiryReasonMapping("cancel_all", "owner_bulk_action", "legacy_unknown", False),
    "bot_cancel_all": ExpiryReasonMapping("bot_cancel_all", "owner_bulk_action", "legacy_unknown", False),
    "republished": ExpiryReasonMapping("republished", "owner_republish", "legacy_unknown", False),
    "market_closed": ExpiryReasonMapping("market_closed", "market_close", "system", True),
    "market_close": ExpiryReasonMapping("market_close", "market_close", "system", True),
    "telegram_send_failed": ExpiryReasonMapping("telegram_send_failed", "publication_failure", "telegram_bot", True),
    "user_deleted": ExpiryReasonMapping("user_deleted", "account_cleanup", "system", True),
    "recovery_finalization": ExpiryReasonMapping("recovery_finalization", "recovery_finalization", "system", True),
    "admin": ExpiryReasonMapping("admin", "admin_action", "admin", False),
    "admin_action": ExpiryReasonMapping("admin_action", "admin_action", "admin", False),
}


def allowed_offer_request_fields(visibility: OfferRequestVisibility | str) -> frozenset[str]:
    role = OfferRequestVisibility(str(getattr(visibility, "value", visibility)))
    if role == OfferRequestVisibility.PUBLIC_LINK:
        return PUBLIC_OFFER_REQUEST_FIELDS
    if role == OfferRequestVisibility.REQUESTER:
        return REQUESTER_OFFER_REQUEST_FIELDS
    if role == OfferRequestVisibility.OWNER:
        return OWNER_AUDIT_OFFER_REQUEST_FIELDS
    return ADMIN_AUDIT_OFFER_REQUEST_FIELDS


def sanitize_offer_request_payload(
    payload: Mapping[str, Any],
    visibility: OfferRequestVisibility | str,
) -> dict[str, Any]:
    allowed = allowed_offer_request_fields(visibility)
    return {key: value for key, value in payload.items() if key in allowed}


def map_legacy_expire_reason(reason: str | None) -> ExpiryReasonMapping:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        normalized = "legacy_unknown"
    return _EXPIRY_REASON_MAP.get(
        normalized,
        ExpiryReasonMapping(normalized, "legacy_unknown", "legacy_unknown", False),
    )
