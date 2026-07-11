from fastapi import APIRouter, HTTPException, Depends, Request, Body
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from models.change_log import ChangeLog
from core.config import settings
from core.audit_logger import audit_log
from core.metrics import (
    record_offer_publication_health,
    record_sync_conflict,
    record_sync_health,
    record_sync_parity_summary,
    record_sync_source_authority_rejection,
    record_sync_watermark_decision,
)
from core.redis import get_redis_client
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, default_peer_server_url, normalize_server, peer_server_url_for
from core.sync_authority import IRAN_AUTHORITATIVE_SYNC_TABLES
from core.sync_field_policy import sanitize_sync_payload
from core.registration_sync_policy import (
    REGISTRATION_VERSIONED_TABLES,
    sanitize_registration_sync_payload,
    registration_sync_capabilities,
)
from core.user_counter_sync import (
    USER_COUNTER_FIELDS,
    USER_COUNTER_EVENT_DELTAS_FIELD,
    USER_COUNTER_EVENT_EPOCH_FIELD,
    USER_COUNTER_EVENT_ID_FIELD,
    USER_COUNTER_EVENT_KIND_FIELD,
    USER_COUNTER_EVENT_OCCURRED_AT_FIELD,
    USER_COUNTER_MAX_EPOCH,
    USER_COUNTER_MAX_VALUE,
    USER_SYNC_IDENTITY_FIELD,
    is_user_counter_event_payload,
    normalize_counter_event_occurred_at,
    user_counter_event_content_hash,
)
from core.sync_metadata import build_sync_metadata, build_sync_public_identity, coerce_positive_int
from core.sync_parity import build_database_parity_snapshot, synced_parity_table_names
from core.sync_parity_observability import summarize_parity_comparison
from core.sync_protocol import build_sync_protocol_metadata, validate_sync_protocol_metadata
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.sync_transport import assert_runtime_sync_transport_allowed, runtime_sync_tls_verify_setting
from core.security import constant_time_secret_equals
from core.registration_identity import normalize_account_name, normalize_mobile_number
from core.services.cross_server_recovery_service import active_publication_is_gated, load_active_publication_gate
from core.services.market_transition_service import reconcile_market_runtime_side_effects_for_current_state
from core.services.offer_publication_reconciliation_service import publication_observability_summary
import hmac
import hashlib
import ipaddress
import time
import json
import logging
from datetime import date as date_cls, datetime, time as time_cls, timezone
from dataclasses import dataclass

router = APIRouter()
logger = logging.getLogger(__name__)
OBSERVABILITY_API_KEY_HEADER = "X-Observability-Api-Key"
PRODUCTION_FULL_MATRIX_SYNC_MARKERS = ("PFM_", "PRODTEST_", "FMX_")
SYNC_PARITY_STATUS_REDIS_KEY = "sync:parity:latest_comparison"


def _require_dev_key(request: Request) -> None:
    dev_key = request.headers.get("X-Dev-Api-Key")
    if not constant_time_secret_equals(dev_key, settings.dev_api_key):
        raise HTTPException(status_code=403, detail="Dev API Key required")


def _is_loopback_client(client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host in {"localhost"}


def _is_loopback_sync_request(request: Request) -> bool:
    client_host = request.client.host if getattr(request, "client", None) else None
    return _is_loopback_client(client_host)


def _require_observability_key(request: Request) -> None:
    configured_key = getattr(settings, "observability_api_key", None)
    supplied_key = request.headers.get(OBSERVABILITY_API_KEY_HEADER)
    if constant_time_secret_equals(supplied_key, configured_key):
        return
    if _is_loopback_sync_request(request):
        return
    if not configured_key:
        raise HTTPException(status_code=503, detail="Observability API key is not configured for remote sync health access")
    if not constant_time_secret_equals(supplied_key, configured_key):
        audit_log(
            "sync.health_access",
            target_type="sync_health",
            result="denied",
            reason="invalid_observability_api_key",
            extra={"path": str(request.url.path), "status_code": 403},
        )
        raise HTTPException(status_code=403, detail="Observability API Key required")


def _age_seconds(value) -> float:
    if not value:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - value).total_seconds(), 0.0)


def _summarize_peer_response(response) -> dict[str, object]:
    body = getattr(response, "text", "") or ""
    body_bytes = str(body).encode("utf-8", errors="replace")
    headers = getattr(response, "headers", {}) or {}
    return {
        "status_code": getattr(response, "status_code", None),
        "peer_response_size_bytes": len(body_bytes),
        "peer_response_sha256": hashlib.sha256(body_bytes).hexdigest()[:16] if body_bytes else None,
        "peer_content_type": headers.get("content-type") if hasattr(headers, "get") else None,
    }


def _summarize_exception(exc: Exception) -> dict[str, object]:
    exc_text = str(exc) or ""
    exc_bytes = exc_text.encode("utf-8", errors="replace")
    return {
        "error_type": type(exc).__name__,
        "error_digest": hashlib.sha256(exc_bytes).hexdigest()[:16] if exc_bytes else None,
    }


async def _active_publication_gated_for_sync_receive(surface: str) -> bool:
    try:
        gated = await active_publication_is_gated()
    except Exception as exc:
        logger.warning(
            "Could not evaluate active publication gate during sync receive",
            extra={
                "event": "sync.active_publication_gate_check_failed",
                "surface": surface,
                **_summarize_exception(exc),
            },
        )
        return False
    if gated:
        logger.warning(
            "Skipping active synced-offer publication while cross-server recovery gate is enabled",
            extra={
                "event": "sync.active_publication_gated",
                "surface": surface,
                "server_mode": settings.server_mode,
            },
        )
    return gated


def _summarize_payload(data) -> dict[str, object]:
    if not isinstance(data, dict):
        return {"data_kind": type(data).__name__}
    return {
        "data_kind": "dict",
        "data_key_count": len(data),
    }


def _contains_production_full_matrix_marker(value) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in PRODUCTION_FULL_MATRIX_SYNC_MARKERS)
    if isinstance(value, dict):
        return any(_contains_production_full_matrix_marker(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_production_full_matrix_marker(item) for item in value)
    return False


def _sync_batch_has_production_full_matrix_users(items: list[dict]) -> bool:
    for item in items:
        if not isinstance(item, dict) or item.get("table") != "users":
            continue
        if _contains_production_full_matrix_marker(item.get("data")):
            return True
    return False


def _enum_value(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


TERMINAL_OFFER_STATUSES = {"completed", "expired", "cancelled"}
TERMINAL_TELEGRAM_LINK_TOKEN_STATUSES = {"used", "revoked", "expired"}
OFFER_PUBLICATION_STATUS_PRECEDENCE = {
    "pending": 10,
    "failed": 20,
    "lagged": 30,
    "disabled": 40,
    "sent": 50,
    "visible": 60,
}
TERMINAL_OFFER_REQUEST_STATUSES = {
    "rejected_business_rule",
    "rejected_offer_expired",
    "rejected_lot_unavailable",
    "rejected_conflict",
    "completed_trade",
    "duplicate_replay",
    "failed_internal",
}
RELATION_LINK_FIELDS = {
    "accountant_relations": "accountant_user_id",
    "customer_relations": "customer_user_id",
}
NON_TERMINAL_RELATION_STATUSES = {"pending", "active"}
COMPLETED_TRADE_STATUS = "completed"
COMPLETED_TRADE_PROTECTED_FIELDS = (
    "offer_id",
    "offer_user_id",
    "responder_user_id",
    "actor_user_id",
    "commodity_id",
    "trade_type",
    "quantity",
    "price",
    "status",
    "trade_number",
)
COMPLETED_TRADE_VISIBILITY_FIELDS = ("archived",)
SYNC_WATERMARK_KNOWN_SOURCES = {SERVER_FOREIGN, SERVER_IRAN}


@dataclass(frozen=True)
class SyncWatermarkContext:
    source_server: str
    aggregate_table: str
    aggregate_key: str
    source_sequence: int
    payload_hash: str
    operation: str
    record_id: str | None


@dataclass(frozen=True)
class SyncWatermarkDecision:
    action: str
    reason: str | None = None


def _completed_trade_offer_id_from_sync(table: str, data: dict) -> int | None:
    if table != "trades":
        return None
    if _enum_value(data.get("status")) != "completed":
        return None
    raw_offer_id = data.get("offer_id")
    if raw_offer_id is None or raw_offer_id == "":
        return None
    try:
        offer_id = int(raw_offer_id)
    except (TypeError, ValueError):
        return None
    return offer_id if offer_id > 0 else None


async def _publish_terminal_offer_realtime_after_sync(db: AsyncSession, terminal_offer_ids: list[int] | tuple[int, ...]) -> None:
    unique_offer_ids = sorted({int(offer_id) for offer_id in terminal_offer_ids if offer_id})
    if not unique_offer_ids:
        return

    from api.routers.realtime import REALTIME_SOURCE_SYNC_APPLY, publish_event
    from models.offer import Offer, OfferStatus

    result = await db.execute(select(Offer).where(Offer.id.in_(unique_offer_ids)))
    terminal_offer_rows = result.scalars().all()
    for offer in terminal_offer_rows:
        status_value = _enum_value(getattr(offer, "status", None))
        try:
            if status_value == OfferStatus.EXPIRED.value:
                await publish_event("offer:expired", {"id": offer.id}, source=REALTIME_SOURCE_SYNC_APPLY)
            else:
                await publish_event(
                    "offer:updated",
                    {
                        "id": offer.id,
                        "status": status_value,
                        "remaining_quantity": getattr(offer, "remaining_quantity", None),
                        "lot_sizes": getattr(offer, "lot_sizes", None),
                    },
                    source=REALTIME_SOURCE_SYNC_APPLY,
                )
        except Exception as exc:
            logger.warning(
                "Failed to publish synced terminal offer realtime event",
                extra={
                    "event": "sync.terminal_offer_realtime_publish_failed",
                    "offer_id": getattr(offer, "id", None),
                    **_summarize_exception(exc),
                },
            )


async def _publish_synced_offer_created_realtime_after_sync(db: AsyncSession, offer_ids: list[int] | tuple[int, ...]) -> None:
    unique_offer_ids = sorted({int(offer_id) for offer_id in offer_ids if offer_id})
    if not unique_offer_ids:
        return

    from api.routers.realtime import REALTIME_SOURCE_SYNC_APPLY, publish_event
    from core.offer_identity import build_offer_public_link, ensure_offer_public_id
    from core.trading_settings import get_trading_settings_async
    from core.utils import to_jalali_str
    from models.offer import Offer, OfferStatus
    from sqlalchemy.orm import selectinload

    try:
        trading_settings = await get_trading_settings_async()
    except Exception as exc:
        trading_settings = None
        logger.warning(
            "Failed to load trading settings for synced offer realtime create projection",
            extra={
                "event": "sync.created_offer_realtime_settings_failed",
                **_summarize_exception(exc),
            },
        )

    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(Offer.id.in_(unique_offer_ids))
    )
    created_offer_rows = result.scalars().all()
    for offer in created_offer_rows:
        status_value = _enum_value(getattr(offer, "status", None))
        if status_value != OfferStatus.ACTIVE.value:
            continue

        expires_at_ts = None
        if trading_settings is not None:
            try:
                expires_at_ts = int(
                    offer.created_at.timestamp()
                    + int(getattr(trading_settings, "offer_expiry_minutes", 0) or 0) * 60
                )
            except Exception:
                expires_at_ts = None

        offer_public_id = ensure_offer_public_id(offer)
        commodity = getattr(offer, "commodity", None)
        payload = {
            "id": offer.id,
            "offer_public_id": offer_public_id,
            "public_link": build_offer_public_link(offer_public_id),
            "user_id": None,
            "offer_type": _enum_value(getattr(offer, "offer_type", None)),
            "commodity_id": getattr(offer, "commodity_id", None),
            "commodity_name": getattr(commodity, "name", None) or "نامشخص",
            "quantity": getattr(offer, "quantity", None),
            "remaining_quantity": getattr(offer, "remaining_quantity", None) or getattr(offer, "quantity", None),
            "price": getattr(offer, "price", None),
            "status": status_value,
            "created_at": to_jalali_str(getattr(offer, "created_at", None)) or "",
            "user_account_name": "",
            "is_own_offer": False,
            "notes": getattr(offer, "notes", None),
            "is_wholesale": getattr(offer, "is_wholesale", True),
            "lot_sizes": getattr(offer, "lot_sizes", None),
            "original_lot_sizes": getattr(offer, "original_lot_sizes", None),
            "expires_at_ts": expires_at_ts,
        }

        try:
            await publish_event("offer:created", payload, source=REALTIME_SOURCE_SYNC_APPLY)
        except Exception as exc:
            logger.warning(
                "Failed to publish synced offer created realtime event",
                extra={
                    "event": "sync.created_offer_realtime_publish_failed",
                    "offer_id": getattr(offer, "id", None),
                    **_summarize_exception(exc),
                },
            )

# Table processing order: dependencies first
TABLE_ORDER = {
    "users": 0,
    "accountant_relations": 1,
    "customer_relations": 2,
    "telegram_link_tokens": 3,
    "chats": 4,
    "chat_members": 5,
    "invitations": 6,
    "admin_market_messages": 7,
    "admin_broadcast_messages": 8,
    "notifications": 9,
    "user_notification_preferences": 10,
    "user_blocks": 11,
    "commodities": 12,
    "commodity_aliases": 13,
    "trading_settings": 14,
    "market_schedule_overrides": 15,
    "market_runtime_state": 16,
    "offers": 17,
    "offer_publication_states": 18,
    "offer_requests": 19,
    "trades": 20,
    "trade_delivery_receipts": 21,
    "telegram_admin_broadcasts": 22,
    "telegram_admin_broadcast_receipts": 23,
    "telegram_notification_outbox": 24,
}

async def verify_signature(request: Request):
    """Verify HMAC signature and timestamp"""
    api_key = request.headers.get("X-API-Key")
    timestamp = request.headers.get("X-Timestamp")
    signature = request.headers.get("X-Signature")
    
    if not api_key or not timestamp or not signature:
        audit_log(
            "sync.authenticate",
            target_type="sync",
            result="denied",
            reason="missing_authentication_headers",
            extra={"path": str(request.url.path), "status_code": 401},
        )
        raise HTTPException(status_code=401, detail="Missing authentication headers")
        
    
    if not constant_time_secret_equals(api_key, settings.sync_api_key):
        audit_log(
            "sync.authenticate",
            target_type="sync",
            result="denied",
            reason="invalid_api_key",
            extra={"path": str(request.url.path), "status_code": 401},
        )
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    # Check timestamp (max 5 minutes drift)
    try:
        ts = int(timestamp)
        now = int(time.time())
        if abs(now - ts) > 300:
            audit_log(
                "sync.authenticate",
                target_type="sync",
                result="denied",
                reason="expired_timestamp",
                extra={"path": str(request.url.path), "status_code": 401},
            )
            raise HTTPException(status_code=401, detail="Request expired")
    except ValueError:
        audit_log(
            "sync.authenticate",
            target_type="sync",
            result="denied",
            reason="invalid_timestamp",
            extra={"path": str(request.url.path), "status_code": 401},
        )
        raise HTTPException(status_code=401, detail="Invalid timestamp")
        
    # Verify signature
    body = await request.body()
    try:
        body_str = body.decode()
        message = f"{timestamp}:{body_str}"
        
        expected_signature = hmac.new(
            settings.sync_api_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            audit_log(
                "sync.authenticate",
                target_type="sync",
                result="denied",
                reason="invalid_signature",
                extra={"path": str(request.url.path), "status_code": 401},
            )
            raise HTTPException(status_code=401, detail="Invalid signature")
             
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Signature verification error",
            extra={
                "event": "sync.signature_verification_error",
                **_summarize_exception(e),
            },
        )
        audit_log(
            "sync.authenticate",
            target_type="sync",
            result="failure",
            reason="verification_failed",
            extra={"path": str(request.url.path), "status_code": 401, "error_type": type(e).__name__},
        )
        raise HTTPException(status_code=401, detail="Verification failed")

from sqlalchemy import case as sa_case
from sqlalchemy import insert, update, delete, literal as sa_literal, or_, select, text as sa_text
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from core.enums import ChatType
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.user import User
from models.user_counter_event_receipt import UserCounterEventReceipt
from models.user_notification_preference import UserNotificationPreference
from models.invitation import Invitation
from models.notification import Notification
from models.telegram_link_token import TelegramLinkToken
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.offer import Offer
from models.offer_request import OfferRequest
from models.offer_publication_state import OfferPublicationState
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryReceipt,
)
from models.telegram_admin_broadcast import (
    TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
)
from models.telegram_notification_outbox import (
    TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES,
    TelegramNotificationOutbox,
)
from models.sync_apply_watermark import SyncApplyWatermark
from models.commodity import Commodity, CommodityAlias
from models.chat import Chat
from models.chat_member import ChatMember
from models.market_runtime_state import MarketRuntimeState
from models.market_schedule_override import MarketScheduleOverride
from models.trading_setting import TradingSetting
from models.user_block import UserBlock
from core.services.chat_room_service import ensure_mandatory_channel_rollout

# Counter fields that should use MAX logic (greatest value wins) to avoid losing increments
USER_COUNTER_FIELDS = {"trades_count", "commodities_traded_count", "channel_messages_count"}

# Natural unique keys per table (used for fallback when ID-based upsert hits name conflict)
NATURAL_KEYS = {
    "accountant_relations": "invitation_token",
    "customer_relations": "invitation_token",
    "commodities": "name",
    "commodity_aliases": "alias",
    "users": "telegram_id",
    "telegram_link_tokens": "token_hash",
    "invitations": "token",
    "notifications": "dedupe_key",
    "user_notification_preferences": "user_id",
    "market_schedule_overrides": "date",
    "trades": "trade_number",
    "offer_publication_states": "dedupe_key",
    "trade_delivery_receipts": "dedupe_key",
    "telegram_admin_broadcast_receipts": "dedupe_key",
    "telegram_notification_outbox": "dedupe_key",
}

SAFE_NATURAL_VALUE_LOG_KEYS = {
    ("commodities", "name"),
    ("commodity_aliases", "alias"),
    ("market_schedule_overrides", "date"),
    ("trades", "trade_number"),
    ("offer_publication_states", "dedupe_key"),
    ("trade_delivery_receipts", "dedupe_key"),
    ("telegram_admin_broadcast_receipts", "dedupe_key"),
    ("telegram_notification_outbox", "dedupe_key"),
}

NATURAL_IDENTITY_DELETE_TABLES = {
    "accountant_relations",
    "commodities",
    "commodity_aliases",
    "customer_relations",
    "invitations",
    "market_schedule_overrides",
    "notifications",
    "offer_publication_states",
    "offer_requests",
    "offers",
    "telegram_link_tokens",
    "telegram_admin_broadcast_receipts",
    "telegram_notification_outbox",
    "trade_delivery_receipts",
    "trades",
    "user_blocks",
    "user_notification_preferences",
}

def _summarize_natural_key_context(table: str, natural_key: str, natural_value) -> dict[str, object]:
    context: dict[str, object] = {"natural_key": natural_key}
    if natural_value is None:
        return context
    if (table, natural_key) in SAFE_NATURAL_VALUE_LOG_KEYS:
        context["natural_value"] = natural_value
        return context
    value_bytes = str(natural_value).encode("utf-8", errors="replace")
    context["natural_value_hash"] = hashlib.sha256(value_bytes).hexdigest()[:16] if value_bytes else None
    return context

SEQUENCE_MAP = {
    "users": ("users_id_seq", "users"),
    "accountant_relations": ("accountant_relations_id_seq", "accountant_relations"),
    "customer_relations": ("customer_relations_id_seq", "customer_relations"),
    "chats": ("chats_id_seq", "chats"),
    "chat_members": ("chat_members_id_seq", "chat_members"),
    "offers": ("offers_id_seq", "offers"),
    "offer_publication_states": ("offer_publication_states_id_seq", "offer_publication_states"),
    "offer_requests": ("offer_requests_id_seq", "offer_requests"),
    "trades": ("trades_id_seq", "trades"),
    "trade_delivery_receipts": ("trade_delivery_receipts_id_seq", "trade_delivery_receipts"),
    "telegram_admin_broadcasts": ("telegram_admin_broadcasts_id_seq", "telegram_admin_broadcasts"),
    "telegram_admin_broadcast_receipts": (
        "telegram_admin_broadcast_receipts_id_seq",
        "telegram_admin_broadcast_receipts",
    ),
    "telegram_notification_outbox": ("telegram_notification_outbox_id_seq", "telegram_notification_outbox"),
    "telegram_link_tokens": ("telegram_link_tokens_id_seq", "telegram_link_tokens"),
    "invitations": ("invitations_id_seq", "invitations"),
    "user_notification_preferences": ("user_notification_preferences_id_seq", "user_notification_preferences"),
    "admin_market_messages": ("admin_market_messages_id_seq", "admin_market_messages"),
    "admin_broadcast_messages": ("admin_broadcast_messages_id_seq", "admin_broadcast_messages"),
    "notifications": ("notifications_id_seq", "notifications"),
    "commodities": ("commodities_id_seq", "commodities"),
    "commodity_aliases": ("commodity_aliases_id_seq", "commodity_aliases"),
    "market_schedule_overrides": ("market_schedule_overrides_id_seq", "market_schedule_overrides"),
    "market_runtime_state": ("market_runtime_state_id_seq", "market_runtime_state"),
    "user_blocks": ("user_blocks_id_seq", "user_blocks"),
}


def _sequence_partition_for_server(server_mode: str | None) -> tuple[int, int]:
    normalized = str(server_mode or "").strip().lower()
    if normalized == "iran":
        return 0, 2
    return 1, 1


def _partitioned_sequence_alignment_sql(seq_name: str, real_table: str, server_mode: str | None) -> tuple[str, str]:
    parity, minimum_value = _sequence_partition_for_server(server_mode)
    alter_sql = f"ALTER SEQUENCE {seq_name} INCREMENT BY 2"
    setval_sql = f"""
        SELECT setval(
            '{seq_name}',
            (
                WITH current_max AS (
                    SELECT COALESCE(MAX(id), 0)::bigint AS max_id FROM {real_table}
                )
                SELECT CASE
                    WHEN max_id < {minimum_value} THEN {minimum_value}
                    WHEN MOD(max_id, 2) = {parity} THEN max_id + 2
                    ELSE max_id + 1
                END
                FROM current_max
            ),
            false
        )
    """
    return alter_sql, setval_sql


def get_model_class(table_name: str):
    mapping = {
        "users": User,
        "accountant_relations": AccountantRelation,
        "customer_relations": CustomerRelation,
        "telegram_link_tokens": TelegramLinkToken,
        "chats": Chat,
        "chat_members": ChatMember,
        "invitations": Invitation,
        "admin_market_messages": AdminMarketMessage,
        "admin_broadcast_messages": AdminBroadcastMessage,
        "notifications": Notification,
        "user_notification_preferences": UserNotificationPreference,
        "offers": Offer,
        "offer_publication_states": OfferPublicationState,
        "offer_requests": OfferRequest,
        "trades": Trade,
        "trade_delivery_receipts": TradeDeliveryReceipt,
        "telegram_admin_broadcasts": TelegramAdminBroadcast,
        "telegram_admin_broadcast_receipts": TelegramAdminBroadcastReceipt,
        "telegram_notification_outbox": TelegramNotificationOutbox,
        "commodities": Commodity,
        "commodity_aliases": CommodityAlias,
        "market_schedule_overrides": MarketScheduleOverride,
        "market_runtime_state": MarketRuntimeState,
        "trading_settings": TradingSetting,
        "user_blocks": UserBlock
    }
    return mapping.get(table_name)


def _normalized_sync_text(value: object) -> str:
    raw_value = getattr(value, "value", value)
    return str(raw_value or "").strip().lower()


def _is_mandatory_channel_record(data: dict) -> bool:
    return (
        _normalized_sync_text(data.get("type")) == "channel"
        and bool(data.get("is_system"))
        and bool(data.get("is_mandatory"))
    )


def _is_mandatory_chat_member_record(data: dict) -> bool:
    return (
        _normalized_sync_text(data.get("chat_type")) == "channel"
        and bool(data.get("chat_is_system"))
        and bool(data.get("chat_is_mandatory"))
    )


def _sync_item_data_for_policy(item: dict) -> dict:
    data = item.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}
    return sanitize_sync_payload(str(item.get("table") or ""), data)


def _is_transitional_mandatory_messenger_sync(table: str, data: dict) -> bool:
    """Temporary compatibility path for mandatory-channel system projections only.

    Messenger-owned tables stay no-sync. The only exception is the mandatory
    system channel projection used during account/channel rollout; arbitrary
    messenger chats, members, messages, and files must still be rejected.
    """
    if table == "chats":
        return _is_mandatory_channel_record(data)
    if table == "chat_members":
        return _is_mandatory_chat_member_record(data)
    return False


def _sync_item_policy_rejection_reason(item: dict) -> str | None:
    table = item.get("table")
    if not isinstance(table, str) or not table:
        return "missing_table"

    try:
        registry_entry = get_sync_registry_entry(table)
    except KeyError:
        return "unregistered_table"

    if registry_entry.planned:
        return "planned_table_not_enabled"

    if registry_entry.policy == SyncPolicy.SYNC:
        return None

    if _is_transitional_mandatory_messenger_sync(table, _sync_item_data_for_policy(item)):
        return None

    return f"policy_forbidden:{registry_entry.policy.value}"


def _sync_item_authority_rejection_reason(item: dict, table: str) -> str | None:
    if table not in IRAN_AUTHORITATIVE_SYNC_TABLES:
        return None
    source_server = _sync_item_source_server(item)
    if source_server is None:
        logger.warning(
            "Sync item for Iran-authoritative table has no source server metadata; applying in compatibility mode",
            extra={
                "event": "sync.authority_compatibility_apply",
                "table": table,
            },
        )
        return None
    if source_server != SERVER_IRAN:
        return f"source_authority_forbidden:{source_server}"
    return None


def _sync_error_detail(item: dict, reason: str) -> dict[str, object]:
    return {
        "table": item.get("table"),
        "record_id": item.get("id"),
        "reason": reason,
    }


def _sync_protocol_error_detail(item: dict, validation) -> dict[str, object]:
    detail = _sync_error_detail(item, validation.reason or "unsupported_sync_protocol")
    for key, value in getattr(validation, "details", {}).items():
        if value is not None:
            detail[key] = value
    return detail


async def _resolve_existing_mandatory_chat_id(db: AsyncSession) -> int | None:
    stmt = select(Chat.id).where(
        Chat.type == ChatType.CHANNEL,
        Chat.is_system.is_(True),
        Chat.is_mandatory.is_(True),
        Chat.is_deleted.is_(False),
    ).order_by(Chat.id.asc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _resolve_existing_chat_member_id(db: AsyncSession, *, chat_id: int | None, user_id: int | None) -> int | None:
    if not chat_id or not user_id:
        return None
    stmt = (
        select(ChatMember.id)
        .where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
        .order_by(ChatMember.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _nullable_greatest(current_value, incoming_value):
    return sa_case(
        (current_value.is_(None), incoming_value),
        (incoming_value.is_(None), current_value),
        else_=sa_func.greatest(current_value, incoming_value),
    )


def _nullable_least(current_value, incoming_value):
    return sa_case(
        (current_value.is_(None), incoming_value),
        (incoming_value.is_(None), current_value),
        else_=sa_func.least(current_value, incoming_value),
    )


def _user_upsert_set_dict(model, stmt, data: dict) -> dict:
    set_dict = {}
    incoming_is_deleted = stmt.excluded["is_deleted"] if "is_deleted" in data else None

    for key in data:
        if key == "id":
            set_dict[key] = stmt.excluded[key]
        elif key in USER_COUNTER_FIELDS:
            set_dict[key] = sa_func.greatest(
                sa_func.coalesce(getattr(model, key), 0),
                sa_func.coalesce(stmt.excluded[key], 0),
            )
        elif key == "is_deleted":
            set_dict[key] = sa_func.coalesce(getattr(model, key), False) | sa_func.coalesce(stmt.excluded[key], False)
        elif key == "deleted_at":
            set_dict[key] = sa_func.coalesce(stmt.excluded[key], getattr(model, key))
        elif key == "created_at":
            set_dict[key] = sa_func.coalesce(getattr(model, key), stmt.excluded[key])
        elif key == "last_seen_at":
            set_dict[key] = _nullable_greatest(getattr(model, key), stmt.excluded[key])
        elif key == "telegram_id":
            cases = []
            if incoming_is_deleted is not None:
                cases.append((incoming_is_deleted.is_(True), None))
            cases.append((stmt.excluded[key].isnot(None), stmt.excluded[key]))
            set_dict[key] = sa_case(*cases, else_=getattr(model, key))
        else:
            if incoming_is_deleted is not None:
                set_dict[key] = sa_case(
                    (
                        sa_func.coalesce(getattr(model, "is_deleted"), False)
                        & ~sa_func.coalesce(incoming_is_deleted, False),
                        getattr(model, key),
                    ),
                    else_=stmt.excluded[key],
                )
            else:
                set_dict[key] = stmt.excluded[key]

    return set_dict


def _user_upsert_where_clause(model, stmt, data: dict):
    where_clause = None

    if "updated_at" in data:
        recency_clause = (
            model.updated_at.is_(None)
            | stmt.excluded["updated_at"].is_(None)
            | (model.updated_at <= stmt.excluded["updated_at"])
        )
        if "is_deleted" in data:
            recency_clause = recency_clause | stmt.excluded["is_deleted"].is_(True)
        where_clause = recency_clause

    if "is_deleted" in data:
        deletion_clause = (
            ~sa_func.coalesce(model.is_deleted, False)
            | sa_func.coalesce(stmt.excluded["is_deleted"], False)
        )
        where_clause = deletion_clause if where_clause is None else where_clause & deletion_clause

    return where_clause


def _notification_upsert_set_dict(model, stmt, data: dict) -> dict:
    set_dict = {}
    for key in data:
        if key in {"id", "dedupe_key"}:
            continue
        if key == "is_read":
            set_dict[key] = sa_func.coalesce(getattr(model, key), False) | sa_func.coalesce(stmt.excluded[key], False)
        else:
            set_dict[key] = stmt.excluded[key]
    return set_dict


def _invitation_upsert_set_dict(model, stmt, data: dict) -> dict:
    set_dict = {}
    for key in data:
        if key in {"id", "token"}:
            continue
        if key == "is_used":
            set_dict[key] = sa_func.coalesce(getattr(model, key), False) | sa_func.coalesce(stmt.excluded[key], False)
        elif key == "expires_at":
            set_dict[key] = _nullable_least(getattr(model, key), stmt.excluded[key])
        else:
            set_dict[key] = stmt.excluded[key]
    return set_dict


def _telegram_link_token_upsert_where_clause(model, stmt, data: dict):
    if "status" not in data:
        return None
    try:
        incoming_status = stmt.excluded["status"]
    except (AttributeError, KeyError):
        return None
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None
    return (~current_status.in_(list(TERMINAL_TELEGRAM_LINK_TOKEN_STATUSES))) | (current_status == incoming_status)


def _market_runtime_state_upsert_where_clause(model, stmt, data: dict):
    if "last_transition_at" not in data:
        return None
    current_transition = getattr(model, "last_transition_at", None)
    if current_transition is None:
        return None
    try:
        incoming_transition = stmt.excluded["last_transition_at"]
    except (AttributeError, KeyError):
        return None
    return current_transition.is_(None) | incoming_transition.is_(None) | (current_transition <= incoming_transition)


def _updated_at_recency_where_clause(model, stmt, data: dict):
    if "updated_at" not in data:
        return None
    current_updated_at = getattr(model, "updated_at", None)
    if current_updated_at is None:
        return None
    try:
        incoming_updated_at = stmt.excluded["updated_at"]
    except (AttributeError, KeyError):
        return None
    return current_updated_at.is_(None) | incoming_updated_at.is_(None) | (current_updated_at <= incoming_updated_at)


def _strict_updated_at_recency_where_clause(model, stmt, data: dict):
    if "updated_at" not in data:
        return None
    current_updated_at = getattr(model, "updated_at", None)
    if current_updated_at is None:
        return None
    try:
        incoming_updated_at = stmt.excluded["updated_at"]
    except (AttributeError, KeyError):
        return None
    return current_updated_at.is_(None) | (
        incoming_updated_at.isnot(None)
        & (current_updated_at <= incoming_updated_at)
    )


def _offer_publication_status_rank(expression):
    whens = [
        (expression == status, rank)
        for status, rank in OFFER_PUBLICATION_STATUS_PRECEDENCE.items()
    ]
    return sa_case(*whens, else_=0)


def _offer_publication_state_upsert_where_clause(model, stmt, data: dict):
    where_clause = None

    if "offer_version_id" in data:
        current_version = getattr(model, "offer_version_id", None)
        if current_version is not None:
            try:
                incoming_version = stmt.excluded["offer_version_id"]
            except (AttributeError, KeyError):
                incoming_version = None
            if incoming_version is not None:
                where_clause = current_version.is_(None) | incoming_version.is_(None) | (current_version <= incoming_version)

    if "status" in data:
        current_status = getattr(model, "status", None)
        if current_status is not None:
            try:
                incoming_status = stmt.excluded["status"]
            except (AttributeError, KeyError):
                incoming_status = None
            if incoming_status is not None:
                current_rank = _offer_publication_status_rank(current_status)
                incoming_rank = _offer_publication_status_rank(incoming_status)
                precedence_clause = incoming_rank >= current_rank

                if "offer_version_id" in data and getattr(model, "offer_version_id", None) is not None:
                    incoming_version = stmt.excluded["offer_version_id"]
                    version_clause = (
                        model.offer_version_id.is_(None)
                        | (model.offer_version_id < incoming_version)
                        | (
                            (incoming_version.is_(None) | (model.offer_version_id == incoming_version))
                            & precedence_clause
                        )
                    )
                    where_clause = version_clause if where_clause is None else where_clause & version_clause
                else:
                    where_clause = precedence_clause if where_clause is None else where_clause & precedence_clause

    return where_clause


def _build_upsert_stmt(model, table, data):
    """Build the INSERT ON CONFLICT statement for a given model and data."""
    stmt = pg_insert(model).values(**data)

    registration_v2 = bool(getattr(settings, "registration_sync_v2_enabled", False))
    has_sync_version = data.get("sync_version") is not None

    if registration_v2 and has_sync_version and table == "users":
        set_dict = {key: stmt.excluded[key] for key in data if key != "id"}
        if "last_seen_at" in data:
            set_dict["last_seen_at"] = _nullable_greatest(
                model.last_seen_at,
                stmt.excluded["last_seen_at"],
            )
        return stmt.on_conflict_do_update(
            index_elements=['id'],
            set_=set_dict,
            where=model.sync_version < stmt.excluded["sync_version"],
        )
    if registration_v2 and has_sync_version and table == "invitations" and data.get("token"):
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "token"}}
        return stmt.on_conflict_do_update(
            index_elements=['token'],
            set_=set_dict,
            where=model.sync_version < stmt.excluded["sync_version"],
        )
    if registration_v2 and has_sync_version and table in RELATION_LINK_FIELDS and data.get("invitation_token"):
        set_dict = {
            key: stmt.excluded[key]
            for key in data
            if key not in {"id", "invitation_token"}
        }
        return stmt.on_conflict_do_update(
            index_elements=['invitation_token'],
            set_=set_dict,
            where=model.sync_version < stmt.excluded["sync_version"],
        )

    if table == "users":
        set_dict = _user_upsert_set_dict(model, stmt, data)
        where_clause = _user_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['id'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['id'], set_=set_dict, where=where_clause)
    elif table == "offers":
        if data.get("offer_public_id"):
            set_dict = {key: value for key, value in data.items() if key not in {"id", "offer_public_id"}}
            where_clause = _offer_upsert_where_clause(model, stmt, data)
            if where_clause is None:
                return stmt.on_conflict_do_update(index_elements=['offer_public_id'], set_=set_dict)
            return stmt.on_conflict_do_update(index_elements=['offer_public_id'], set_=set_dict, where=where_clause)
        where_clause = _offer_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['id'], set_=data)
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data, where=where_clause)
    elif table == "trades" and data.get("trade_number") not in (None, ""):
        set_dict = {key: value for key, value in data.items() if key not in {"id", "trade_number"}}
        where_clause = _trade_completed_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['trade_number'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['trade_number'], set_=set_dict, where=where_clause)
    elif table == "offer_publication_states" and data.get("dedupe_key"):
        set_dict = {key: value for key, value in data.items() if key not in {"id", "dedupe_key"}}
        where_clause = _offer_publication_state_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict, where=where_clause)
    elif table == "trade_delivery_receipts" and data.get("dedupe_key"):
        immutable_fields = {
            "id",
            "dedupe_key",
            "event_type",
            "trade_number",
            "recipient_user_id",
            "channel",
            "destination_server",
            "worker_id",
            "lease_until",
        }
        set_dict = {key: value for key, value in data.items() if key not in immutable_fields}
        where_clause = _trade_delivery_receipt_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict, where=where_clause)
    elif table == "telegram_admin_broadcast_receipts" and data.get("dedupe_key"):
        immutable_fields = {
            "id",
            "dedupe_key",
            "broadcast_id",
            "recipient_user_id",
            "worker_id",
            "lease_until",
        }
        set_dict = {key: value for key, value in data.items() if key not in immutable_fields}
        where_clause = _telegram_admin_broadcast_receipt_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict, where=where_clause)
    elif table == "telegram_notification_outbox" and data.get("dedupe_key"):
        immutable_fields = {
            "id",
            "dedupe_key",
            "source_type",
            "source_id",
            "recipient_user_id",
            "worker_id",
            "lease_until",
        }
        set_dict = {key: value for key, value in data.items() if key not in immutable_fields}
        where_clause = _telegram_notification_outbox_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict, where=where_clause)
    elif table == "user_notification_preferences" and data.get("user_id") is not None:
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "user_id"}}
        if "updated_at" in data:
            where_clause = _strict_updated_at_recency_where_clause(model, stmt, data)
            if where_clause is None:
                return stmt.on_conflict_do_update(index_elements=['user_id'], set_=set_dict)
            return stmt.on_conflict_do_update(
                index_elements=['user_id'],
                set_=set_dict,
                where=where_clause,
            )
        return stmt.on_conflict_do_update(index_elements=['user_id'], set_=set_dict)
    elif (
        table == "offer_requests"
        and data.get("request_home_server")
        and data.get("idempotency_key")
    ):
        set_dict = {
            key: value
            for key, value in data.items()
            if key not in {"id", "request_home_server", "idempotency_key"}
        }
        where_clause = _offer_request_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(
                index_elements=['request_home_server', 'idempotency_key'],
                index_where=model.idempotency_key.isnot(None),
                set_=set_dict,
            )
        return stmt.on_conflict_do_update(
            index_elements=['request_home_server', 'idempotency_key'],
            index_where=model.idempotency_key.isnot(None),
            set_=set_dict,
            where=where_clause,
        )
    elif table == "notifications" and data.get("dedupe_key"):
        set_dict = _notification_upsert_set_dict(model, stmt, data)
        return stmt.on_conflict_do_update(
            index_elements=['dedupe_key'],
            index_where=model.dedupe_key.isnot(None),
            set_=set_dict,
        )
    elif table == "invitations" and data.get("token"):
        set_dict = _invitation_upsert_set_dict(model, stmt, data)
        return stmt.on_conflict_do_update(index_elements=['token'], set_=set_dict)
    elif table == "telegram_link_tokens" and data.get("token_hash"):
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "token_hash"}}
        where_clause = _telegram_link_token_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['token_hash'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['token_hash'], set_=set_dict, where=where_clause)
    elif table in RELATION_LINK_FIELDS and data.get("invitation_token"):
        set_dict = {
            key: stmt.excluded[key]
            for key in data
            if key not in {"id", "invitation_token"}
        }
        where_clause = _linked_relation_upsert_where_clause(model, stmt, data, RELATION_LINK_FIELDS[table])
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['invitation_token'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['invitation_token'], set_=set_dict, where=where_clause)
    elif table == "commodities" and data.get("name"):
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "name"}}
        if not set_dict:
            return stmt.on_conflict_do_nothing(index_elements=['name'])
        return stmt.on_conflict_do_update(index_elements=['name'], set_=set_dict)
    elif table == "commodity_aliases" and data.get("alias"):
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "alias"}}
        if not set_dict:
            return stmt.on_conflict_do_nothing(index_elements=['alias'])
        return stmt.on_conflict_do_update(index_elements=['alias'], set_=set_dict)
    elif table == "market_schedule_overrides" and data.get("date"):
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "date"}}
        where_clause = _updated_at_recency_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['date'], set_=set_dict)
        return stmt.on_conflict_do_update(index_elements=['date'], set_=set_dict, where=where_clause)
    elif table == "admin_market_messages":
        where_clause = _updated_at_recency_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['id'], set_=data)
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data, where=where_clause)
    elif table == "market_runtime_state":
        where_clause = _market_runtime_state_upsert_where_clause(model, stmt, data)
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['id'], set_=data)
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data, where=where_clause)
    elif table == "user_blocks" and data.get("blocker_id") is not None and data.get("blocked_id") is not None:
        set_dict = {key: stmt.excluded[key] for key in data if key not in {"id", "blocker_id", "blocked_id"}}
        return stmt.on_conflict_do_update(index_elements=['blocker_id', 'blocked_id'], set_=set_dict)
    elif table in RELATION_LINK_FIELDS:
        where_clause = _linked_relation_upsert_where_clause(model, stmt, data, RELATION_LINK_FIELDS[table])
        if where_clause is None:
            return stmt.on_conflict_do_update(index_elements=['id'], set_=data)
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data, where=where_clause)
    else:
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data)


def _filter_model_columns(model, data: dict) -> dict:
    """Drop sync payload aliases that are not persisted DB columns."""
    table = getattr(model, "__table__", None)
    columns = getattr(table, "columns", None)
    if columns is None:
        return data
    column_names = set(columns.keys())
    return {key: value for key, value in data.items() if key in column_names}


def _result_scalar_first(result):
    try:
        return result.scalars().first()
    except AttributeError:
        return None


def _result_first(result):
    try:
        return result.first()
    except AttributeError:
        pass
    return _result_scalar_first(result)


def _result_scalar_one_or_none(result):
    try:
        return result.scalar_one_or_none()
    except AttributeError:
        pass
    return _result_scalar_first(result)


def _result_scalars_all(result) -> list:
    try:
        return list(result.scalars().all())
    except AttributeError:
        return []


def _sync_truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _row_value(row, key: str, index: int):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    if hasattr(row, key):
        return getattr(row, key)
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return None


async def _synced_deleted_user_telegram_effect(
    db: AsyncSession,
    *,
    table: str,
    operation: str,
    record_id,
    data: dict,
) -> tuple[int, int] | None:
    """Capture Telegram cleanup data before a synced user delete clears telegram_id."""
    if current_server() != SERVER_FOREIGN:
        return None
    if table != "users" or operation not in {"INSERT", "UPDATE"}:
        return None
    if not _sync_truthy(data.get("is_deleted")):
        return None

    user_id = coerce_positive_int(record_id or data.get("id"))
    if user_id is None:
        return None

    try:
        result = await db.execute(
            select(User.is_deleted, User.telegram_id)
            .where(User.id == user_id)
            .limit(1)
        )
        row = _result_first(result)
    except Exception as exc:
        logger.warning(
            "Could not inspect local user before synced deletion cleanup",
            extra={
                "event": "sync.deleted_user_telegram_cleanup_probe_failed",
                "user_id": user_id,
                **_summarize_exception(exc),
            },
        )
        return None

    if row is not None and _sync_truthy(_row_value(row, "is_deleted", 0)):
        return None

    telegram_id = _row_value(row, "telegram_id", 1) if row is not None else data.get("telegram_id")
    telegram_id = coerce_positive_int(telegram_id)
    if telegram_id is None:
        return None
    return user_id, telegram_id


async def _run_synced_deleted_user_telegram_effects(effects: list[tuple[int, int]]) -> None:
    if current_server() != SERVER_FOREIGN or not effects:
        return

    from bot.utils.redis_helpers import mark_deleted_telegram_user
    from core.services.user_deletion_service import REMOVAL_TELEGRAM_MESSAGE, remove_user_from_telegram_channel
    from core.utils import send_telegram_notification

    seen_telegram_ids: set[int] = set()
    for user_id, telegram_id in effects:
        if telegram_id in seen_telegram_ids:
            continue
        seen_telegram_ids.add(telegram_id)

        try:
            await mark_deleted_telegram_user(telegram_id)
        except Exception as exc:
            logger.warning(
                "Could not mark synced deleted Telegram user",
                extra={
                    "event": "sync.deleted_user_telegram_mark_failed",
                    "user_id": user_id,
                    **_summarize_exception(exc),
                },
            )

        try:
            await send_telegram_notification(telegram_id, REMOVAL_TELEGRAM_MESSAGE)
        except Exception as exc:
            logger.warning(
                "Could not notify synced deleted Telegram user",
                extra={
                    "event": "sync.deleted_user_telegram_notify_failed",
                    "user_id": user_id,
                    **_summarize_exception(exc),
                },
            )

        try:
            await remove_user_from_telegram_channel(telegram_id)
        except Exception as exc:
            logger.warning(
                "Could not remove synced deleted Telegram user from channel",
                extra={
                    "event": "sync.deleted_user_telegram_channel_remove_failed",
                    "user_id": user_id,
                    **_summarize_exception(exc),
                },
            )


def _nonempty_text(value) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


async def _resolve_offer_id_by_public_id(db: AsyncSession, offer_public_id: str | None) -> int | None:
    public_id = _nonempty_text(offer_public_id)
    if not public_id:
        return None
    result = await db.execute(select(Offer.id).where(Offer.offer_public_id == public_id))
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_trade_id_by_trade_number(db: AsyncSession, trade_number) -> int | None:
    if trade_number is None or trade_number == "":
        return None
    result = await db.execute(select(Trade.id).where(Trade.trade_number == trade_number))
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_publication_state_id_by_dedupe_key(db: AsyncSession, dedupe_key: str | None) -> int | None:
    dedupe = _nonempty_text(dedupe_key)
    if not dedupe:
        return None
    result = await db.execute(select(OfferPublicationState.id).where(OfferPublicationState.dedupe_key == dedupe))
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_trade_delivery_receipt_id_by_dedupe_key(db: AsyncSession, dedupe_key: str | None) -> int | None:
    dedupe = _nonempty_text(dedupe_key)
    if not dedupe:
        return None
    result = await db.execute(select(TradeDeliveryReceipt.id).where(TradeDeliveryReceipt.dedupe_key == dedupe))
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_offer_request_id_by_idempotency(db: AsyncSession, data: dict) -> int | None:
    request_home_server = _nonempty_text(data.get("request_home_server"))
    idempotency_key = _nonempty_text(data.get("idempotency_key"))
    if not request_home_server or not idempotency_key:
        return None
    result = await db.execute(
        select(OfferRequest.id).where(
            OfferRequest.request_home_server == request_home_server,
            OfferRequest.idempotency_key == idempotency_key,
        )
    )
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_user_notification_preference_id_by_user_id(db: AsyncSession, data: dict) -> int | None:
    user_id = coerce_positive_int(data.get("user_id"))
    if user_id is None:
        return None
    result = await db.execute(
        select(UserNotificationPreference.id).where(UserNotificationPreference.user_id == user_id)
    )
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_user_block_id_by_pair(db: AsyncSession, data: dict) -> int | None:
    blocker_id = coerce_positive_int(data.get("blocker_id"))
    blocked_id = coerce_positive_int(data.get("blocked_id"))
    if blocker_id is None or blocked_id is None:
        return None
    result = await db.execute(
        select(UserBlock.id).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id,
        )
    )
    value = _result_scalar_one_or_none(result)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_model_id_by_natural_key(db: AsyncSession, model, key: str, value) -> int | None:
    if value is None or value == "":
        return None
    column = getattr(model, key, None)
    if column is None:
        return None
    result = await db.execute(select(model.id).where(column == value).limit(1))
    resolved = _result_scalar_one_or_none(result)
    try:
        return int(resolved) if resolved is not None else None
    except (TypeError, ValueError):
        return None


async def _resolve_local_record_id_by_public_identity(
    db: AsyncSession,
    table: str,
    data: dict,
) -> int | None:
    model = get_model_class(table)
    if model is not None:
        natural_key = NATURAL_KEYS.get(table)
        if natural_key and table != "users":
            resolved_id = await _resolve_model_id_by_natural_key(db, model, natural_key, data.get(natural_key))
            if resolved_id is not None:
                return resolved_id

    if table == "offers":
        return await _resolve_offer_id_by_public_id(db, data.get("offer_public_id"))
    if table == "trades":
        return await _resolve_trade_id_by_trade_number(db, data.get("trade_number"))
    if table == "offer_publication_states":
        return await _resolve_publication_state_id_by_dedupe_key(db, data.get("dedupe_key"))
    if table == "trade_delivery_receipts":
        return await _resolve_trade_delivery_receipt_id_by_dedupe_key(db, data.get("dedupe_key"))
    if table == "offer_requests":
        return await _resolve_offer_request_id_by_idempotency(db, data)
    if table == "user_notification_preferences":
        return await _resolve_user_notification_preference_id_by_user_id(db, data)
    if table == "user_blocks":
        return await _resolve_user_block_id_by_pair(db, data)
    return None


def _sync_table_has_natural_delete_identity(table: str, data: dict) -> bool:
    if table in {
        "offers",
        "trades",
        "offer_publication_states",
        "trade_delivery_receipts",
        "telegram_admin_broadcast_receipts",
        "telegram_notification_outbox",
        "offer_requests",
        "user_blocks",
    }:
        return _sync_table_has_public_identity(table, data)
    natural_key = NATURAL_KEYS.get(table)
    if not natural_key or table == "users":
        return False
    return data.get(natural_key) not in (None, "")


def _sync_table_has_public_identity(table: str, data: dict) -> bool:
    if table == "offers":
        return bool(_nonempty_text(data.get("offer_public_id")))
    if table == "trades":
        return data.get("trade_number") not in (None, "")
    if table == "offer_publication_states":
        return bool(_nonempty_text(data.get("dedupe_key")))
    if table == "trade_delivery_receipts":
        return bool(_nonempty_text(data.get("dedupe_key")))
    if table == "telegram_admin_broadcast_receipts":
        return bool(_nonempty_text(data.get("dedupe_key")))
    if table == "telegram_notification_outbox":
        return bool(_nonempty_text(data.get("dedupe_key")))
    if table == "offer_requests":
        return bool(_nonempty_text(data.get("request_home_server")) and _nonempty_text(data.get("idempotency_key")))
    if table == "user_notification_preferences":
        return coerce_positive_int(data.get("user_id")) is not None
    if table == "user_blocks":
        return (
            coerce_positive_int(data.get("blocker_id")) is not None
            and coerce_positive_int(data.get("blocked_id")) is not None
        )
    natural_key = NATURAL_KEYS.get(table)
    if natural_key and table != "users":
        return data.get(natural_key) not in (None, "")
    return False


async def _localize_offer_reference_by_public_id(db: AsyncSession, table: str, data: dict) -> bool:
    offer_public_id = _nonempty_text(data.get("offer_public_id"))
    if not offer_public_id or table not in {"trades", "offer_publication_states", "offer_requests"}:
        return True
    local_offer_id = await _resolve_offer_id_by_public_id(db, offer_public_id)
    if local_offer_id is None:
        if table == "offer_requests":
            data["local_offer_id"] = None
            return True
        return False
    if table == "offer_requests":
        data["local_offer_id"] = local_offer_id
    else:
        data["offer_id"] = local_offer_id
    return True


async def _localize_commodity_reference_by_name(db: AsyncSession, table: str, data: dict) -> bool:
    if table not in {"commodity_aliases", "offers", "trades"}:
        return True
    commodity_name = _nonempty_text(data.get("commodity_name"))
    if not commodity_name:
        return True
    local_commodity_id = await _resolve_model_id_by_natural_key(db, Commodity, "name", commodity_name)
    if local_commodity_id is None:
        return False
    data["commodity_id"] = local_commodity_id
    return True


async def _localize_republished_offer_reference(db: AsyncSession, data: dict) -> bool:
    republished_public_id = _nonempty_text(data.pop("republished_offer_public_id", None))
    if not republished_public_id:
        return True
    local_offer_id = await _resolve_offer_id_by_public_id(db, republished_public_id)
    if local_offer_id is None:
        data["republished_offer_id"] = None
        return False
    data["republished_offer_id"] = local_offer_id
    return True


async def _localize_offer_request_customer_relation_reference(db: AsyncSession, data: dict) -> bool:
    invitation_token = _nonempty_text(data.pop("customer_relation_invitation_token", None))
    if not invitation_token:
        return True
    local_relation_id = await _resolve_model_id_by_natural_key(
        db,
        CustomerRelation,
        "invitation_token",
        invitation_token,
    )
    if local_relation_id is None:
        data["customer_relation_id"] = None
        return False
    data["customer_relation_id"] = local_relation_id
    return True


async def _localize_registration_user_reference(
    db: AsyncSession,
    table: str,
    data: dict,
) -> bool:
    """Translate Iran User FKs to the already-localized foreign User row."""

    if not bool(getattr(settings, "registration_sync_v2_enabled", False)):
        return True
    if table == "invitations" and data.get("registered_user_id") is not None:
        mobile = normalize_mobile_number(data.get("mobile_number"))
        account = normalize_account_name(data.get("account_name"))
        if not mobile or not account:
            return False
        users = list(
            (
                await db.execute(
                    select(User).where(
                        User.normalized_mobile_number == mobile,
                        User.normalized_account_name == account,
                    )
                )
            ).scalars().all()
        )
        if len(users) != 1:
            return False
        data["registered_user_id"] = int(users[0].id)
        return True
    if table in RELATION_LINK_FIELDS and data.get(RELATION_LINK_FIELDS[table]) is not None:
        invitation_token = _nonempty_text(data.get("invitation_token"))
        if not invitation_token:
            return False
        invitation = (
            await db.execute(
                select(Invitation).where(Invitation.token == invitation_token)
            )
        ).scalar_one_or_none()
        if invitation is None or invitation.registered_user_id is None:
            return False
        data[RELATION_LINK_FIELDS[table]] = int(invitation.registered_user_id)
    return True


async def _localize_trade_delivery_receipt_references(db: AsyncSession, data: dict) -> bool:
    trade_number = data.get("trade_number")
    if trade_number in (None, ""):
        data["trade_id"] = None
        data["offer_id"] = None
        return True

    result = await db.execute(
        select(Trade.id, Trade.offer_id)
        .where(Trade.trade_number == trade_number)
        .limit(1)
    )
    row = result.first()
    if row is None:
        return False
    data["trade_id"] = row[0]
    data["offer_id"] = row[1]
    return True


async def _localize_offer_request_resulting_trade_reference(db: AsyncSession, data: dict) -> bool:
    trade_number = data.pop("resulting_trade_number", None)
    if trade_number in (None, ""):
        return True
    local_trade_id = await _resolve_trade_id_by_trade_number(db, trade_number)
    if local_trade_id is None:
        data["resulting_trade_id"] = None
        return False
    data["resulting_trade_id"] = local_trade_id
    return True


def _normalize_completed_trade_field(field: str, value):
    if field in {
        "offer_id",
        "offer_user_id",
        "responder_user_id",
        "actor_user_id",
        "commodity_id",
        "quantity",
        "price",
        "trade_number",
    }:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field in {"status", "trade_type"}:
        return _enum_value(value)
    if field == "archived":
        return bool(value)
    return value


async def _load_trade_for_sync_guard(db: AsyncSession, record_id, data: dict):
    trade_number = data.get("trade_number")
    if trade_number is not None and trade_number != "":
        result = await db.execute(select(Trade).where(Trade.trade_number == trade_number))
        existing = _result_scalar_first(result)
        if existing is not None:
            return existing

    candidate_id = coerce_positive_int(data.get("id")) or coerce_positive_int(record_id)
    if candidate_id is None:
        return None
    result = await db.execute(select(Trade).where(Trade.id == candidate_id))
    return _result_scalar_first(result)


def _trade_sync_guard_reason_for_existing(existing, operation: str, data: dict) -> str | None:
    if existing is None:
        return None

    current_status = _enum_value(getattr(existing, "status", None))
    if current_status != COMPLETED_TRADE_STATUS:
        return None

    if operation == "DELETE":
        return "completed_trade_delete"

    if operation not in {"INSERT", "UPDATE"}:
        return None

    incoming_status = _enum_value(data.get("status"))
    if not incoming_status:
        return "incomplete_destructive_payload"
    if incoming_status != COMPLETED_TRADE_STATUS:
        return "completed_to_non_completed_update"

    missing_fields = [field for field in COMPLETED_TRADE_PROTECTED_FIELDS if field not in data]
    if missing_fields:
        return "incomplete_destructive_payload"

    for field in COMPLETED_TRADE_PROTECTED_FIELDS:
        current_value = _normalize_completed_trade_field(field, getattr(existing, field, None))
        incoming_value = _normalize_completed_trade_field(field, data.get(field))
        if current_value != incoming_value:
            return "protected_business_field_mismatch"

    for field in COMPLETED_TRADE_VISIBILITY_FIELDS:
        if field not in data:
            continue
        current_value = _normalize_completed_trade_field(field, getattr(existing, field, None))
        incoming_value = _normalize_completed_trade_field(field, data.get(field))
        if current_value != incoming_value:
            return "protected_business_field_mismatch"

    return None


async def _trade_sync_guard_reason(db: AsyncSession, operation: str, record_id, data: dict) -> str | None:
    existing = await _load_trade_for_sync_guard(db, record_id, data)
    return _trade_sync_guard_reason_for_existing(existing, operation, data)


def _log_trade_sync_guard_ignored(record_id, operation: str, data: dict, reason: str) -> None:
    metadata = build_sync_metadata("trades", record_id, operation, data)
    record_sync_conflict(server_mode=settings.server_mode, table="trades", reason=reason)
    logger.warning(
        "Ignored destructive synced trade event",
        extra={
            "event": "sync.trade_guard_ignored",
            "table": "trades",
            "record_id": record_id,
            "reason": reason,
            "incoming_status": _enum_value(data.get("status")),
            "trade_number": data.get("trade_number"),
            "sync_meta": metadata,
        },
    )


def _offer_payload_needs_ordering_check(data: dict) -> bool:
    incoming_status = _enum_value(data.get("status"))
    return bool(incoming_status and coerce_positive_int(data.get("version_id")) is not None)


def _offer_request_payload_needs_ordering_check(data: dict) -> bool:
    incoming_status = _enum_value(data.get("result_status"))
    return bool(incoming_status and coerce_positive_int(data.get("version_id")) is not None)


def _offer_upsert_where_clause(model, stmt, data: dict):
    if not _offer_payload_needs_ordering_check(data):
        return None
    current_status = getattr(model, "status", None)
    current_version = getattr(model, "version_id", None)
    if current_status is None or current_version is None:
        return None
    try:
        incoming_status = stmt.excluded["status"]
        incoming_version = stmt.excluded["version_id"]
    except (AttributeError, KeyError):
        return None

    current_terminal = current_status.in_(list(TERMINAL_OFFER_STATUSES))
    incoming_terminal = incoming_status.in_(list(TERMINAL_OFFER_STATUSES))
    same_version_terminal_conflict = (
        current_terminal
        & incoming_terminal
        & (current_version == incoming_version)
        & (current_status != incoming_status)
    )
    terminal_reactivation = current_terminal & ~incoming_terminal

    return (current_version <= incoming_version) & ~terminal_reactivation & ~same_version_terminal_conflict


def _offer_request_upsert_where_clause(model, stmt, data: dict):
    if not _offer_request_payload_needs_ordering_check(data):
        return None
    current_status = getattr(model, "result_status", None)
    current_version = getattr(model, "version_id", None)
    if current_status is None or current_version is None:
        return None
    try:
        incoming_status = stmt.excluded["result_status"]
        incoming_version = stmt.excluded["version_id"]
    except (AttributeError, KeyError):
        return None

    current_terminal = current_status.in_(list(TERMINAL_OFFER_REQUEST_STATUSES))
    incoming_terminal = incoming_status.in_(list(TERMINAL_OFFER_REQUEST_STATUSES))
    same_version_terminal_conflict = (
        current_terminal
        & incoming_terminal
        & (current_version == incoming_version)
        & (current_status != incoming_status)
    )
    terminal_reactivation = current_terminal & ~incoming_terminal

    return (current_version <= incoming_version) & ~terminal_reactivation & ~same_version_terminal_conflict


def _linked_relation_upsert_where_clause(model, stmt, data: dict, link_field: str):
    if link_field not in data:
        return None
    current_link = getattr(model, link_field, None)
    if current_link is None:
        return None
    try:
        incoming_link = stmt.excluded[link_field]
    except (AttributeError, KeyError):
        return None

    # Relation registration can emit an intermediate non-terminal row with a
    # NULL linked-user id. If that stale payload arrives after the final linked
    # payload, it must not erase the peer's already-resolved customer/accountant.
    allow_update = current_link.is_(None) | incoming_link.isnot(None)

    if "deleted_at" in data and getattr(model, "deleted_at", None) is not None:
        try:
            allow_update = allow_update | stmt.excluded["deleted_at"].isnot(None)
        except (AttributeError, KeyError):
            pass

    if "status" in data and getattr(model, "status", None) is not None:
        try:
            allow_update = allow_update | ~stmt.excluded["status"].in_(list(NON_TERMINAL_RELATION_STATUSES))
        except (AttributeError, KeyError):
            pass

    return allow_update


def _linked_relation_payload_can_clear_active_link(table: str, data: dict) -> bool:
    link_field = RELATION_LINK_FIELDS.get(table)
    if not link_field or link_field not in data or data.get(link_field) is not None:
        return False
    if data.get("deleted_at") is not None:
        return False
    incoming_status = _enum_value(data.get("status"))
    return not incoming_status or incoming_status in NON_TERMINAL_RELATION_STATUSES


def _log_stale_linked_relation_sync_ignored(table: str, record_id, operation: str, data: dict) -> None:
    metadata = build_sync_metadata(table, record_id, operation, data)
    record_sync_conflict(server_mode=settings.server_mode, table=table, reason="stale_null_relation_link")
    logger.warning(
        "Ignored stale linked relation event",
        extra={
            "event": "sync.stale_linked_relation_ignored",
            "table": table,
            "record_id": record_id,
            "reason": "stale_null_relation_link",
            "incoming_status": _enum_value(data.get("status")),
            "sync_meta": metadata,
        },
    )


def _log_unsafe_id_only_delete_ignored(table: str, record_id, data: dict, reason: str) -> None:
    metadata = build_sync_metadata(table, record_id, "DELETE", data)
    record_sync_conflict(server_mode=settings.server_mode, table=table, reason=reason)
    logger.warning(
        "Ignored synced delete without resolvable natural identity",
        extra={
            "event": "sync.unsafe_id_only_delete_ignored",
            "table": table,
            "record_id": record_id,
            "reason": reason,
            "sync_meta": metadata,
        },
    )


def _trade_delivery_receipt_upsert_where_clause(model, stmt, data: dict):
    if not _enum_value(data.get("status")):
        return None
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None
    try:
        incoming_status = stmt.excluded["status"]
    except (AttributeError, KeyError):
        return None

    terminal_statuses = list(TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES)
    current_terminal = current_status.in_(terminal_statuses)
    incoming_terminal = incoming_status.in_(terminal_statuses)
    same_terminal_state = current_terminal & incoming_terminal & (current_status == incoming_status)

    return (~current_terminal) | same_terminal_state


def _telegram_admin_broadcast_receipt_upsert_where_clause(model, stmt, data: dict):
    if not _enum_value(data.get("status")):
        return None
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None
    try:
        incoming_status = stmt.excluded["status"]
    except (AttributeError, KeyError):
        return None

    terminal_statuses = list(TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES)
    current_terminal = current_status.in_(terminal_statuses)
    incoming_terminal = incoming_status.in_(terminal_statuses)
    same_terminal_state = current_terminal & incoming_terminal & (current_status == incoming_status)

    return (~current_terminal) | same_terminal_state


def _telegram_notification_outbox_upsert_where_clause(model, stmt, data: dict):
    if not _enum_value(data.get("status")):
        return None
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None
    try:
        incoming_status = stmt.excluded["status"]
    except (AttributeError, KeyError):
        return None

    terminal_statuses = list(TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES)
    current_terminal = current_status.in_(terminal_statuses)
    incoming_terminal = incoming_status.in_(terminal_statuses)
    same_terminal_state = current_terminal & incoming_terminal & (current_status == incoming_status)

    return (~current_terminal) | same_terminal_state


def _trade_payload_uses_completed_guard(data: dict) -> bool:
    return data.get("trade_number") not in (None, "")


def _trade_sql_value_for_field(field: str, value):
    if field == "status":
        enum_value = _enum_value(value)
        for candidate in TradeStatus:
            if candidate.value == enum_value:
                return candidate
    if field == "trade_type":
        enum_value = _enum_value(value)
        for candidate in TradeType:
            if candidate.value == enum_value:
                return candidate
    return value


def _sql_null_safe_equal(left, right):
    comparator = getattr(left, "is_not_distinct_from", None) or getattr(left, "isnot_distinct_from", None)
    if callable(comparator):
        return comparator(right)
    return left == right


def _trade_completed_upsert_where_clause(model, stmt, data: dict):
    if not _trade_payload_uses_completed_guard(data):
        return None
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None

    current_not_completed = current_status != TradeStatus.COMPLETED
    if _enum_value(data.get("status")) != COMPLETED_TRADE_STATUS:
        return current_not_completed
    if any(field not in data for field in COMPLETED_TRADE_PROTECTED_FIELDS):
        return current_not_completed

    try:
        same_completed_business_fields = stmt.excluded["status"] == TradeStatus.COMPLETED
    except (AttributeError, KeyError):
        return current_not_completed

    for field in COMPLETED_TRADE_PROTECTED_FIELDS:
        current_column = getattr(model, field, None)
        if current_column is None:
            return current_not_completed
        try:
            incoming_column = stmt.excluded[field]
        except (AttributeError, KeyError):
            return current_not_completed
        same_completed_business_fields = same_completed_business_fields & _sql_null_safe_equal(
            current_column,
            incoming_column,
        )

    for field in COMPLETED_TRADE_VISIBILITY_FIELDS:
        if field not in data:
            continue
        current_column = getattr(model, field, None)
        if current_column is None:
            return current_not_completed
        try:
            incoming_column = stmt.excluded[field]
        except (AttributeError, KeyError):
            return current_not_completed
        same_completed_business_fields = same_completed_business_fields & _sql_null_safe_equal(
            current_column,
            incoming_column,
        )

    return current_not_completed | same_completed_business_fields


def _trade_completed_update_where_clause(model, data: dict):
    current_status = getattr(model, "status", None)
    if current_status is None:
        return None

    current_not_completed = current_status != TradeStatus.COMPLETED
    if _enum_value(data.get("status")) != COMPLETED_TRADE_STATUS:
        return current_not_completed
    if any(field not in data for field in COMPLETED_TRADE_PROTECTED_FIELDS):
        return current_not_completed

    same_completed_business_fields = current_status == TradeStatus.COMPLETED
    for field in COMPLETED_TRADE_PROTECTED_FIELDS:
        current_column = getattr(model, field, None)
        if current_column is None:
            return current_not_completed
        same_completed_business_fields = same_completed_business_fields & (
            _sql_null_safe_equal(current_column, _trade_sql_value_for_field(field, data.get(field)))
        )

    for field in COMPLETED_TRADE_VISIBILITY_FIELDS:
        if field not in data:
            continue
        current_column = getattr(model, field, None)
        if current_column is None:
            return current_not_completed
        same_completed_business_fields = same_completed_business_fields & _sql_null_safe_equal(
            current_column,
            data.get(field),
        )

    return current_not_completed | same_completed_business_fields


async def _stale_offer_sync_reason(db: AsyncSession, record_id, data: dict) -> str | None:
    if not _offer_payload_needs_ordering_check(data):
        return None

    offer_public_id = _nonempty_text(data.get("offer_public_id"))
    if offer_public_id:
        result = await db.execute(select(Offer).where(Offer.offer_public_id == offer_public_id))
    else:
        result = await db.execute(select(Offer).where(Offer.id == record_id))
    existing = _result_scalar_first(result)
    if existing is None:
        return None

    incoming_status = _enum_value(data.get("status"))
    current_status = _enum_value(getattr(existing, "status", None))
    incoming_version = coerce_positive_int(data.get("version_id"))
    current_version = coerce_positive_int(getattr(existing, "version_id", None))

    if current_version is not None and incoming_version is not None and incoming_version < current_version:
        return "older_authoritative_version"

    if current_status in TERMINAL_OFFER_STATUSES and incoming_status not in TERMINAL_OFFER_STATUSES:
        return "terminal_state_protected"

    if (
        current_status in TERMINAL_OFFER_STATUSES
        and incoming_status in TERMINAL_OFFER_STATUSES
        and current_version is not None
        and incoming_version == current_version
        and incoming_status != current_status
    ):
        return "same_version_terminal_conflict"

    return None


def _log_stale_offer_sync_ignored(record_id, operation: str, data: dict, reason: str) -> None:
    metadata = build_sync_metadata("offers", record_id, operation, data)
    record_sync_conflict(server_mode=settings.server_mode, table="offers", reason=reason)
    logger.warning(
        "Ignored stale synced offer event",
        extra={
            "event": "sync.stale_offer_ignored",
            "table": "offers",
            "record_id": record_id,
            "reason": reason,
            "incoming_status": _enum_value(data.get("status")),
            "incoming_version": coerce_positive_int(data.get("version_id")),
            "sync_meta": metadata,
        },
    )


def _log_stale_offer_request_sync_ignored(record_id, operation: str, data: dict, reason: str) -> None:
    metadata = build_sync_metadata("offer_requests", record_id, operation, data)
    record_sync_conflict(server_mode=settings.server_mode, table="offer_requests", reason=reason)
    logger.warning(
        "Ignored stale synced offer request event",
        extra={
            "event": "sync.stale_offer_request_ignored",
            "table": "offer_requests",
            "record_id": record_id,
            "reason": reason,
            "incoming_result_status": _enum_value(data.get("result_status")),
            "incoming_version": coerce_positive_int(data.get("version_id")),
            "sync_meta": metadata,
        },
    )


async def _apply_versioned_user_patch(
    db: AsyncSession,
    *,
    record_id,
    data: dict,
    source_server: str | None,
) -> str:
    source = str(source_server or "").strip().lower()
    if source not in {SERVER_IRAN, SERVER_FOREIGN}:
        return "error"
    resolution, target_user_id = await _resolve_user_sync_target(
        db,
        record_id=record_id,
        identity=data.get(USER_SYNC_IDENTITY_FIELD),
        lock=True,
    )
    if resolution == "conflict":
        return "error"
    if resolution == "missing":
        return "deferred"
    if target_user_id is None:
        return "error"

    values: dict[str, object] = {}
    where_clause = User.id == target_user_id

    if source == SERVER_FOREIGN:
        for field_name in ("bot_onboarding_required_step", "bot_onboarding_completed_step"):
            if field_name in data:
                values[field_name] = sa_func.greatest(
                    sa_func.coalesce(getattr(User, field_name), 0),
                    sa_func.coalesce(data[field_name], 0),
                )
        for field_name in ("bot_onboarding_completed_at", "last_seen_at"):
            if field_name in data:
                values[field_name] = _nullable_greatest(
                    getattr(User, field_name),
                    sa_literal(data[field_name]),
                )
        if not values:
            return "ignored"
        # SQLAlchemy Column.onupdate would otherwise advance this shared row
        # clock for a foreign-only onboarding patch.
        values["updated_at"] = User.updated_at
    elif source == SERVER_IRAN:
        values = {
            key: value
            for key, value in data.items()
            if key not in {"id", "created_at", "updated_at", USER_SYNC_IDENTITY_FIELD}
        }
        if "last_seen_at" in data:
            values["last_seen_at"] = _nullable_greatest(
                User.last_seen_at,
                sa_literal(data["last_seen_at"]),
            )
        values["updated_at"] = data.get("updated_at", User.updated_at)
        incoming_version = data.get("sync_version")
        if incoming_version is not None:
            where_clause = where_clause & (User.sync_version < int(incoming_version))
    if not values:
        return 'ignored'

    stmt = update(User).where(where_clause).values(**values)
    async with db.begin_nested():
        result = await db.execute(stmt, execution_options={"is_sync": True})
    if getattr(result, "rowcount", None):
        return 'ok'

    existing_result = await db.execute(
        select(User.id, User.sync_version).where(User.id == target_user_id)
    )
    existing = _result_first(existing_result)
    if existing is None:
        return 'deferred'
    return 'ignored'


async def _apply_versioned_user_insert(
    db: AsyncSession,
    *,
    record_id,
    data: dict,
    source_server: str | None,
) -> str | None:
    if str(source_server or "").strip().lower() != SERVER_IRAN:
        return "error"
    identity = data.get(USER_SYNC_IDENTITY_FIELD)
    if not _user_sync_identity_conditions(identity):
        # During the mixed-version window, an older Iran sender has neither
        # sync_version nor the v2 natural-identity envelope. Preserve the
        # existing ID/natural-key upsert path when compatibility is enabled by
        # the caller. A versioned event without identity is always malformed.
        if data.get("sync_version") is None:
            return None
        return "error"

    resolution, target_user_id = await _resolve_user_sync_target(
        db,
        record_id=record_id,
        identity=identity,
        lock=True,
    )
    if resolution == "conflict":
        return "error"
    if resolution == "identity":
        return await _apply_versioned_user_patch(
            db,
            record_id=target_user_id,
            data=data,
            source_server=source_server,
        )

    existing_id_result = await db.execute(
        select(User).where(User.id == record_id).with_for_update()
    )
    if _result_scalar_first(existing_id_result) is not None:
        logger.error(
            "Versioned User insert found a conflicting local numeric id",
            extra={
                "event": "sync.user_insert_id_conflict",
                "record_id": record_id,
            },
        )
        return "error"
    return None


def _user_sync_identity_conditions(identity: object) -> list:
    if not isinstance(identity, dict):
        return []
    values_by_field: dict[str, set[object]] = {
        "account_name": set(),
        "mobile_number": set(),
        "telegram_id": set(),
    }
    for section_name in ("current", "previous"):
        section = identity.get(section_name)
        if not isinstance(section, dict):
            continue
        for field_name in values_by_field:
            value = section.get(field_name)
            if value is None or value == "":
                continue
            values_by_field[field_name].add(value)
    conditions = []
    normalized_account_values = {
        normalize_account_name(value)
        for value in values_by_field["account_name"]
        if normalize_account_name(value)
    }
    normalized_mobile_values = {
        normalize_mobile_number(value)
        for value in values_by_field["mobile_number"]
        if normalize_mobile_number(value)
    }
    conditions.extend(
        User.normalized_account_name == value
        for value in normalized_account_values
    )
    conditions.extend(
        User.normalized_mobile_number == value
        for value in normalized_mobile_values
    )
    conditions.extend(
        User.telegram_id == value
        for value in values_by_field["telegram_id"]
    )
    return conditions


async def _resolve_user_sync_target(
    db: AsyncSession,
    *,
    record_id,
    identity: object,
    lock: bool,
) -> tuple[str, int | None]:
    conditions = _user_sync_identity_conditions(identity)
    if not conditions:
        try:
            return "legacy_id", int(record_id)
        except (TypeError, ValueError):
            return "missing", None

    stmt = select(User).where(or_(*conditions))
    if lock:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    users = _result_scalars_all(result)
    distinct_users = {
        int(user.id): user
        for user in users
        if getattr(user, "id", None) is not None
    }
    if len(distinct_users) > 1:
        logger.error(
            "User sync identity resolved to multiple local rows",
            extra={
                "event": "sync.user_identity_conflict",
                "record_id": record_id,
                "matched_user_count": len(distinct_users),
                "identity_fields": sorted(
                    {
                        field_name
                        for section_name in ("current", "previous")
                        for field_name in (
                            identity.get(section_name, {}).keys()
                            if isinstance(identity.get(section_name), dict)
                            else ()
                        )
                    }
                ),
            },
        )
        return "conflict", None
    if not distinct_users:
        return "missing", None
    return "identity", next(iter(distinct_users))


async def _apply_user_counter_event(
    db: AsyncSession,
    *,
    record_id,
    data: dict,
    source_server: str | None,
) -> str:
    source = str(source_server or "").strip().lower()
    if source not in {SERVER_IRAN, SERVER_FOREIGN}:
        return "error"
    if (
        data.get(USER_COUNTER_EVENT_KIND_FIELD) == "reset"
        and source != SERVER_IRAN
    ):
        return "error"
    resolution, target_user_id = await _resolve_user_sync_target(
        db,
        record_id=record_id,
        identity=data.get(USER_SYNC_IDENTITY_FIELD),
        lock=True,
    )
    if resolution == "conflict":
        return "error"
    if resolution == "missing" or target_user_id is None:
        return "deferred"

    user_result = await db.execute(
        select(User).where(User.id == target_user_id).with_for_update()
    )
    user = _result_scalar_first(user_result)
    if user is None:
        return "deferred"

    occurred_at = normalize_counter_event_occurred_at(data[USER_COUNTER_EVENT_OCCURRED_AT_FIELD])
    event_kind = data[USER_COUNTER_EVENT_KIND_FIELD]
    incoming_epoch = int(data[USER_COUNTER_EVENT_EPOCH_FIELD])
    deltas = data[USER_COUNTER_EVENT_DELTAS_FIELD]
    if (
        event_kind not in {"increment", "reset"}
        or incoming_epoch < 1
        or incoming_epoch > USER_COUNTER_MAX_EPOCH
    ):
        return "error"
    event_hash = user_counter_event_content_hash(
        source_server=source,
        event_id=data[USER_COUNTER_EVENT_ID_FIELD],
        kind=event_kind,
        epoch=incoming_epoch,
        deltas=deltas,
        occurred_at=occurred_at,
    )
    async def existing_receipt_decision() -> str | None:
        existing_result = await db.execute(
            select(UserCounterEventReceipt).where(
                UserCounterEventReceipt.event_id == data[USER_COUNTER_EVENT_ID_FIELD]
            )
        )
        existing = _result_scalar_first(existing_result)
        if existing is None:
            return None
        if (
            existing.source_server == source
            and int(existing.user_id) == target_user_id
            and existing.event_hash == event_hash
        ):
            return "ignored"
        logger.error(
            "Counter event UUID was replayed with conflicting content",
            extra={
                "event": "sync.user_counter_event_conflict",
                "record_id": record_id,
                "event_id_hash": hashlib.sha256(
                    str(data[USER_COUNTER_EVENT_ID_FIELD]).encode("utf-8")
                ).hexdigest()[:16],
            },
        )
        return "error"

    replay_decision = await existing_receipt_decision()
    if replay_decision is not None:
        return replay_decision

    async def insert_receipt(outcome: str) -> str | None:
        receipt_stmt = (
            pg_insert(UserCounterEventReceipt)
            .values(
                event_id=data[USER_COUNTER_EVENT_ID_FIELD],
                source_server=source,
                user_id=target_user_id,
                event_hash=event_hash,
                event_kind=event_kind,
                event_epoch=incoming_epoch,
                occurred_at=occurred_at,
                deltas=deltas,
                outcome=outcome,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(UserCounterEventReceipt.event_id)
        )
        result = await db.execute(receipt_stmt, execution_options={"is_sync": True})
        if _result_scalar_one_or_none(result) is not None:
            return None
        return await existing_receipt_decision() or "error"

    current_epoch = int(getattr(user, "counter_epoch", 1) or 1)
    latest_reset_result = await db.execute(
        select(
            UserCounterEventReceipt.event_epoch,
            UserCounterEventReceipt.occurred_at,
        )
        .where(
            UserCounterEventReceipt.user_id == target_user_id,
            UserCounterEventReceipt.event_kind == "reset",
        )
        .order_by(UserCounterEventReceipt.event_epoch.desc())
        .limit(1)
    )
    latest_reset = _result_first(latest_reset_result)
    reset_boundary = None
    if current_epoch == 1:
        if latest_reset is not None:
            return "error"
    else:
        if latest_reset is None or int(latest_reset[0]) != current_epoch:
            return "error"
        reset_boundary = normalize_counter_event_occurred_at(latest_reset[1])

    if event_kind == "increment":
        if incoming_epoch > current_epoch + 1:
            return "deferred"
        # v2 assigns equality to the new period. Reset boundaries themselves
        # must advance strictly; increments at the exact boundary are included.
        if reset_boundary is not None and occurred_at < reset_boundary:
            async with db.begin_nested():
                insert_decision = await insert_receipt("excluded_pre_boundary")
            return insert_decision or "ignored"

        values: dict[str, int] = {}
        for field_name in USER_COUNTER_FIELDS:
            delta = int(deltas.get(field_name, 0) or 0)
            if not delta:
                continue
            current_value = int(getattr(user, field_name, 0) or 0)
            next_value = current_value + delta
            if (
                delta < 0
                or delta > USER_COUNTER_MAX_VALUE
                or current_value < 0
                or next_value > USER_COUNTER_MAX_VALUE
            ):
                return "error"
            values[field_name] = next_value
        async with db.begin_nested():
            insert_decision = await insert_receipt("applied")
            if insert_decision is not None:
                return insert_decision
            await db.execute(
                update(User).where(User.id == target_user_id).values(**values),
                execution_options={"is_sync": True},
            )
        return "ok"

    if incoming_epoch <= current_epoch:
        return "error"
    if incoming_epoch > current_epoch + 1:
        return "deferred"
    if reset_boundary is not None and occurred_at <= reset_boundary:
        return "error"

    receipt_rows = list(
        (
            await db.execute(
                select(UserCounterEventReceipt.deltas).where(
                    UserCounterEventReceipt.user_id == target_user_id,
                    UserCounterEventReceipt.event_kind == "increment",
                    UserCounterEventReceipt.occurred_at >= occurred_at,
                )
            )
        ).scalars().all()
    )
    rebuilt = {field_name: 0 for field_name in USER_COUNTER_FIELDS}
    for receipt_deltas in receipt_rows:
        for field_name in USER_COUNTER_FIELDS:
            rebuilt[field_name] += int((receipt_deltas or {}).get(field_name, 0) or 0)
            if rebuilt[field_name] > USER_COUNTER_MAX_VALUE:
                return "error"
    async with db.begin_nested():
        insert_decision = await insert_receipt("applied")
        if insert_decision is not None:
            return insert_decision
        await db.execute(
            update(User)
            .where(User.id == target_user_id)
            .values(counter_epoch=incoming_epoch, **rebuilt),
            execution_options={"is_sync": True},
        )
    return "ok"


async def _apply_item(
    db: AsyncSession,
    table: str,
    operation: str,
    record_id,
    data: dict,
    model,
    new_offers: list,
    terminal_offers: list | None = None,
    source_server: str | None = None,
):
    """
    Apply a single sync item using SAVEPOINT so failures don't kill the transaction.
    Handles:
      1. Normal upsert by ID
      2. UniqueViolation fallback → update by natural key
      3. ForeignKeyViolation → returns 'deferred' for retry
    Returns: 'ok', 'ignored', 'deferred', or 'error'
    """
    if table == "trading_settings":
        setting_key = data.get('key')
        if not setting_key:
            logger.warning(
                "Skipping trading_setting sync without key",
                extra={
                    "event": "sync.trading_setting_missing_key",
                    "table": table,
                    "record_id": record_id,
                    **_summarize_payload(data),
                },
            )
            return 'error'
        data = _filter_model_columns(model, data)
        stmt = pg_insert(model).values(**data)
        set_dict = {key: value for key, value in data.items() if key != "key"}
        where_clause = _updated_at_recency_where_clause(model, stmt, data)
        if where_clause is None:
            stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=set_dict)
        else:
            stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=set_dict, where=where_clause)
        async with db.begin_nested():
            await db.execute(stmt, execution_options={"is_sync": True})
        return 'ok'

    if operation in ("INSERT", "UPDATE"):
        if (
            table == "users"
            and operation == "INSERT"
            and bool(getattr(settings, "registration_sync_v2_enabled", False))
        ):
            insert_decision = await _apply_versioned_user_insert(
                db,
                record_id=record_id,
                data=data,
                source_server=source_server,
            )
            if insert_decision is not None:
                return insert_decision
        if (
            table == "users"
            and operation == "UPDATE"
            and bool(getattr(settings, "registration_sync_v2_enabled", False))
        ):
            if is_user_counter_event_payload(data):
                return await _apply_user_counter_event(
                    db,
                    record_id=record_id,
                    data=data,
                    source_server=source_server,
                )
            return await _apply_versioned_user_patch(
                db,
                record_id=record_id,
                data=data,
                source_server=source_server,
            )
        if table == "chats" and _is_mandatory_channel_record(data):
            existing_mandatory_chat_id = await _resolve_existing_mandatory_chat_id(db)
            if existing_mandatory_chat_id is not None:
                record_id = existing_mandatory_chat_id

        if table == "chat_members":
            if _is_mandatory_chat_member_record(data):
                existing_mandatory_chat_id = await _resolve_existing_mandatory_chat_id(db)
                if existing_mandatory_chat_id is not None:
                    data["chat_id"] = existing_mandatory_chat_id

            existing_chat_member_id = await _resolve_existing_chat_member_id(
                db,
                chat_id=data.get("chat_id"),
                user_id=data.get("user_id"),
            )
            if existing_chat_member_id is not None:
                record_id = existing_chat_member_id

            data.pop("chat_type", None)
            data.pop("chat_is_system", None)
            data.pop("chat_is_mandatory", None)

        if not await _localize_offer_reference_by_public_id(db, table, data):
            logger.warning(
                "Synced row references offer_public_id that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.offer_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                    "offer_public_id": data.get("offer_public_id"),
                },
            )
            return 'deferred'

        if not await _localize_commodity_reference_by_name(db, table, data):
            logger.warning(
                "Synced row references a commodity name that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.commodity_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                },
            )
            return 'deferred'

        if table == "offers" and not await _localize_republished_offer_reference(db, data):
            logger.warning(
                "Synced offer references a replacement offer that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.republished_offer_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                },
            )
            return 'deferred'

        if table == "trade_delivery_receipts" and not await _localize_trade_delivery_receipt_references(db, data):
            logger.warning(
                "Synced trade delivery receipt references a trade_number that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.trade_receipt_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                    "trade_number": data.get("trade_number"),
                },
            )
            return 'deferred'

        if table == "offer_requests" and not await _localize_offer_request_resulting_trade_reference(db, data):
            logger.warning(
                "Synced offer request references a trade_number that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.offer_request_trade_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                },
            )
            return 'deferred'

        if table == "offer_requests" and not await _localize_offer_request_customer_relation_reference(db, data):
            logger.warning(
                "Synced offer request references a customer relation that is not available locally; deferring",
                extra={
                    "event": "sync.public_identity.offer_request_customer_relation_deferred",
                    "table": table,
                    "record_id": record_id,
                },
            )
            return 'deferred'

        if not await _localize_registration_user_reference(db, table, data):
            logger.warning(
                "Synced registration row references a User not yet localized",
                extra={
                    "event": "sync.registration_user_reference_deferred",
                    "table": table,
                    "record_id": record_id,
                },
            )
            return 'deferred'

        uses_public_identity = _sync_table_has_public_identity(table, data)
        if not uses_public_identity:
            data['id'] = record_id
        else:
            data.pop("id", None)

        # Never overwrite channel_message_id from sync — it's set locally by channel-send
        track_new_offer = False
        track_offer_realtime_or_terminal = False
        if table == "offers":
            if terminal_offers is None:
                terminal_offers = []
            status_value = str(data.get("status") or "").lower()
            stale_reason = await _stale_offer_sync_reason(db, record_id, data)
            if stale_reason:
                _log_stale_offer_sync_ignored(record_id, operation, data, stale_reason)
                return 'ok'
            data.pop("channel_message_id", None)
            if operation == "INSERT":
                track_new_offer = True
            if operation in ("INSERT", "UPDATE"):
                if settings.server_mode == "iran":
                    if operation == "UPDATE" or status_value in TERMINAL_OFFER_STATUSES:
                        track_offer_realtime_or_terminal = True
                elif status_value in TERMINAL_OFFER_STATUSES:
                    track_offer_realtime_or_terminal = True

        if table == "trades":
            trade_guard_reason = await _trade_sync_guard_reason(db, operation, record_id, data)
            if trade_guard_reason:
                _log_trade_sync_guard_ignored(record_id, operation, data, trade_guard_reason)
                return 'ignored'

        persist_data = _filter_model_columns(model, data)
        stmt = _build_upsert_stmt(model, table, persist_data)

        try:
            trade_atomic_guard_noop = False
            async with db.begin_nested():
                execute_result = await db.execute(stmt, execution_options={"is_sync": True})
                if (
                    table in REGISTRATION_VERSIONED_TABLES
                    and bool(getattr(settings, "registration_sync_v2_enabled", False))
                    and persist_data.get("sync_version") is not None
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    logger.info(
                        "Ignored stale registration sync event",
                        extra={
                            "event": "sync.registration_stale_version_ignored",
                            "table": table,
                            "record_id": record_id,
                            "incoming_sync_version": persist_data.get("sync_version"),
                        },
                    )
                    return 'ignored'
                if (
                    table == "offers"
                    and _offer_payload_needs_ordering_check(persist_data)
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    _log_stale_offer_sync_ignored(record_id, operation, persist_data, "atomic_upsert_guard_noop")
                if (
                    table == "offer_requests"
                    and _offer_request_payload_needs_ordering_check(persist_data)
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    _log_stale_offer_request_sync_ignored(record_id, operation, persist_data, "atomic_upsert_guard_noop")
                if (
                    table == "trades"
                    and _trade_payload_uses_completed_guard(persist_data)
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    _log_trade_sync_guard_ignored(record_id, operation, persist_data, "atomic_upsert_guard_noop")
                    trade_atomic_guard_noop = True
                if (
                    table == "market_runtime_state"
                    and "last_transition_at" in persist_data
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    logger.info(
                        "Ignored stale synced market runtime state event",
                        extra={
                            "event": "sync.stale_market_runtime_state_ignored",
                            "table": table,
                            "record_id": record_id,
                            "operation": operation,
                            "incoming_last_transition_at": persist_data.get("last_transition_at"),
                        },
                    )
                    return 'ignored'
                if (
                    table in RELATION_LINK_FIELDS
                    and _linked_relation_payload_can_clear_active_link(table, persist_data)
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    _log_stale_linked_relation_sync_ignored(table, record_id, operation, persist_data)
                    return 'ignored'
            if trade_atomic_guard_noop:
                return 'ignored'
            if table == "offers" and (track_new_offer or track_offer_realtime_or_terminal):
                applied_offer_id = await _resolve_local_record_id_by_public_identity(db, table, persist_data)
                if applied_offer_id is None and not uses_public_identity:
                    try:
                        applied_offer_id = int(record_id)
                    except (TypeError, ValueError):
                        applied_offer_id = None
                if applied_offer_id is not None:
                    if track_new_offer:
                        new_offers.append(applied_offer_id)
                    if track_offer_realtime_or_terminal:
                        terminal_offers.append(applied_offer_id)
            return 'ok'
        except IntegrityError as e:
            err_str = str(e).lower()

            # --- Case A: Unique violation on natural key (e.g. same name, different ID) ---
            if "unique" in err_str or "duplicate key" in err_str:
                natural_key = NATURAL_KEYS.get(table)
                natural_value = persist_data.get(natural_key) if natural_key else None

                if natural_value is not None:
                    try:
                        if table == "trades":
                            trade_guard_reason = await _trade_sync_guard_reason(db, "UPDATE", record_id, persist_data)
                            if trade_guard_reason:
                                _log_trade_sync_guard_ignored(record_id, "UPDATE", persist_data, trade_guard_reason)
                                return 'ignored'
                        # Update existing record by natural key
                        natural_col = getattr(model, natural_key)
                        update_data = {k: v for k, v in persist_data.items() if k != 'id' and k != natural_key}
                        if update_data:
                            async with db.begin_nested():
                                stmt_update = (
                                    update(model)
                                    .where(natural_col == natural_value)
                                    .values(**update_data)
                                )
                                if (
                                    table in REGISTRATION_VERSIONED_TABLES
                                    and bool(getattr(settings, "registration_sync_v2_enabled", False))
                                    and persist_data.get("sync_version") is not None
                                ):
                                    if table == "users" and "last_seen_at" in update_data:
                                        stmt_update = stmt_update.values(
                                            last_seen_at=_nullable_greatest(
                                                User.last_seen_at,
                                                sa_literal(update_data["last_seen_at"]),
                                            )
                                        )
                                    stmt_update = stmt_update.where(
                                        model.sync_version < int(persist_data["sync_version"])
                                    )
                                if table == "trades":
                                    where_clause = _trade_completed_update_where_clause(model, persist_data)
                                    if where_clause is not None:
                                        stmt_update = stmt_update.where(where_clause)
                                update_result = await db.execute(stmt_update, execution_options={"is_sync": True})
                                if (
                                    table in REGISTRATION_VERSIONED_TABLES
                                    and bool(getattr(settings, "registration_sync_v2_enabled", False))
                                    and persist_data.get("sync_version") is not None
                                    and getattr(update_result, "rowcount", None) == 0
                                ):
                                    logger.info(
                                        "Ignored stale registration natural-key merge",
                                        extra={
                                            "event": "sync.registration_stale_version_ignored",
                                            "table": table,
                                            "record_id": record_id,
                                            "incoming_sync_version": persist_data.get("sync_version"),
                                        },
                                    )
                                    return 'ignored'
                                if (
                                    table == "trades"
                                    and getattr(update_result, "rowcount", None) == 0
                                ):
                                    _log_trade_sync_guard_ignored(
                                        record_id,
                                        "UPDATE",
                                        persist_data,
                                        "atomic_upsert_guard_noop",
                                    )
                                    return 'ignored'
                        logger.info(
                            "Sync merged by natural key fallback",
                            extra={
                                "event": "sync.integrity.merge_by_natural_key",
                                "table": table,
                                "record_id": record_id,
                                **_summarize_natural_key_context(table, natural_key, natural_value),
                            },
                        )
                        record_sync_conflict(server_mode=settings.server_mode, table=table, reason="natural_key_merge")
                        return 'ok'
                    except Exception as merge_err:
                        logger.error(
                            "Failed to merge synced row by natural key",
                            extra={
                                "event": "sync.integrity.merge_failed",
                                "table": table,
                                "record_id": record_id,
                                **_summarize_natural_key_context(table, natural_key, natural_value),
                                **_summarize_exception(merge_err),
                            },
                        )
                        return 'error'
                else:
                    logger.error(
                        "Unique violation on synced row without natural key fallback",
                        extra={
                            "event": "sync.integrity.unique_violation",
                            "table": table,
                            "record_id": record_id,
                            **_summarize_exception(e),
                        },
                    )
                    return 'error'

            # --- Case B: FK violation (parent not yet synced) → defer for retry ---
            elif "foreign key" in err_str:
                logger.warning(
                    "FK violation for synced row; deferring for retry",
                    extra={
                        "event": "sync.integrity.foreign_key_violation",
                        "table": table,
                        "record_id": record_id,
                    },
                )
                return 'deferred'

            else:
                logger.error(
                    "Integrity error on synced row",
                    extra={
                        "event": "sync.integrity.error",
                        "table": table,
                        "record_id": record_id,
                        **_summarize_exception(e),
                    },
                )
                return 'error'

    elif operation == "DELETE":
        try:
            async with db.begin_nested():
                if table == "chats" and _is_mandatory_channel_record(data):
                    existing_mandatory_chat_id = await _resolve_existing_mandatory_chat_id(db)
                    if existing_mandatory_chat_id is not None:
                        record_id = existing_mandatory_chat_id

                if table == "chat_members":
                    if _is_mandatory_chat_member_record(data):
                        existing_mandatory_chat_id = await _resolve_existing_mandatory_chat_id(db)
                        if existing_mandatory_chat_id is not None:
                            data["chat_id"] = existing_mandatory_chat_id

                    chat_member_id = await _resolve_existing_chat_member_id(
                        db,
                        chat_id=data.get("chat_id"),
                        user_id=data.get("user_id"),
                    )
                    if chat_member_id is not None:
                        record_id = chat_member_id

                local_record_id = await _resolve_local_record_id_by_public_identity(db, table, data)
                if local_record_id is not None:
                    record_id = local_record_id
                elif table in NATURAL_IDENTITY_DELETE_TABLES:
                    if _sync_table_has_natural_delete_identity(table, data):
                        _log_unsafe_id_only_delete_ignored(table, record_id, data, "natural_identity_not_found")
                    else:
                        _log_unsafe_id_only_delete_ignored(table, record_id, data, "missing_natural_identity")
                    return 'ignored'

                local_trade_for_delete_guard = None
                if table == "trades":
                    local_trade_for_delete_guard = await _load_trade_for_sync_guard(db, record_id, data)
                    trade_guard_reason = _trade_sync_guard_reason_for_existing(
                        local_trade_for_delete_guard,
                        operation,
                        data,
                    )
                    if trade_guard_reason:
                        _log_trade_sync_guard_ignored(record_id, operation, data, trade_guard_reason)
                        return 'ignored'

                stmt = delete(model).where(model.id == record_id)
                if table == "trades":
                    stmt = stmt.where(model.status != TradeStatus.COMPLETED)
                delete_result = await db.execute(stmt, execution_options={"is_sync": True})
                if (
                    table == "trades"
                    and local_trade_for_delete_guard is not None
                    and getattr(delete_result, "rowcount", None) == 0
                ):
                    _log_trade_sync_guard_ignored(record_id, operation, data, "atomic_delete_guard_noop")
                    return 'ignored'
            return 'ok'
        except IntegrityError as e:
            logger.error(
                "Cannot delete synced row because of FK dependency",
                extra={
                    "event": "sync.delete_fk_dependency",
                    "table": table,
                    "record_id": record_id,
                    **_summarize_exception(e),
                },
            )
            return 'error'

    return 'error'


def _notification_user_ids_from_items(items: list[dict]) -> set[int]:
    user_ids: set[int] = set()
    for item in items:
        if item.get("table") != "notifications":
            continue
        data = item.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        user_id = data.get("user_id") if isinstance(data, dict) else None
        if user_id is not None:
            try:
                user_ids.add(int(user_id))
            except (TypeError, ValueError):
                pass
    return user_ids


async def _refresh_notification_unread_counts(db: AsyncSession, user_ids: set[int]) -> None:
    if not user_ids:
        return
    try:
        from core.redis import get_redis_client
        redis_client = get_redis_client()
    except Exception as exc:
        logger.warning(
            "Could not refresh notification unread counts after sync",
            extra={
                "event": "sync.notification_unread_counts_refresh_failed",
                **_summarize_exception(exc),
            },
        )
        return

    for user_id in user_ids:
        try:
            count_stmt = select(sa_func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.is_read == False,
            )
            result = await db.execute(count_stmt)
            unread_count = int(result.scalar() or 0)
            await redis_client.set(f"user:{user_id}:unread_count", unread_count)
        except Exception as exc:
            logger.warning(
                "Could not refresh notification unread count for user",
                extra={
                    "event": "sync.notification_unread_count_refresh_failed",
                    "user_id": user_id,
                    **_summarize_exception(exc),
                },
            )


def _parse_item(item: dict):
    """Parse a sync item: extract table, operation, model, data, record_id."""
    table = item.get('table')
    operation = item.get('operation')
    data = item.get('data')
    record_id = item.get('id')

    model = get_model_class(table)
    if not model:
        return None

    if isinstance(data, str):
        data = json.loads(data)
    data = sanitize_sync_payload(str(table or ""), data)
    if not isinstance(data, dict):
        return None

    # Parse datetime fields
    for key, value in list(data.items()):
        if not isinstance(value, str):
            continue
        if key.endswith('_at'):
            try:
                data[key] = datetime.fromisoformat(value)
            except ValueError:
                pass
            continue
        if key == 'date':
            try:
                data[key] = date_cls.fromisoformat(value)
            except ValueError:
                pass
            continue
        if key.endswith('_time_local'):
            try:
                data[key] = time_cls.fromisoformat(value)
            except ValueError:
                pass

    return table, operation, model, data, record_id


def _sync_item_mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _sync_item_source_server(item: dict) -> str | None:
    sync_meta = _sync_item_mapping(item.get("sync_meta"))
    source_server = sync_meta.get("source_server")
    if not source_server:
        protocol = _sync_item_mapping(item.get("sync_protocol"))
        producer = _sync_item_mapping(protocol.get("producer"))
        source_server = producer.get("server_mode")
    normalized = normalize_server(source_server, default="")
    return normalized if normalized in SYNC_WATERMARK_KNOWN_SOURCES else None


def _sync_item_source_sequence(item: dict) -> int | None:
    sync_meta = _sync_item_mapping(item.get("sync_meta"))
    for value in (
        sync_meta.get("source_sequence"),
        sync_meta.get("outbox_id"),
        item.get("change_log_id"),
    ):
        sequence = coerce_positive_int(value)
        if sequence is not None:
            return sequence
    return None


def _sync_item_aggregate_key(table: str, operation: str, record_id, data: dict, item: dict) -> tuple[str, str] | None:
    sync_meta = _sync_item_mapping(item.get("sync_meta"))
    aggregate_table = str(sync_meta.get("aggregate_table") or table or "").strip()
    aggregate_key = str(sync_meta.get("aggregate_id") or "").strip()
    if aggregate_table and aggregate_key:
        return aggregate_table, aggregate_key

    fallback_meta = build_sync_metadata(table, record_id, operation, data)
    aggregate_table = str(fallback_meta.get("aggregate_table") or "").strip()
    aggregate_key = str(fallback_meta.get("aggregate_id") or "").strip()
    if aggregate_table and aggregate_key:
        return aggregate_table, aggregate_key
    return None


def _sync_item_payload_hash(table: str, operation: str, record_id, data: dict) -> str:
    canonical = {
        "table": table,
        "operation": operation,
        "record_id": record_id,
        "data": data,
    }
    encoded = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sync_watermark_context_from_item(
    item: dict,
    *,
    table: str,
    operation: str,
    record_id,
    data: dict,
) -> SyncWatermarkContext | None:
    source_server = _sync_item_source_server(item)
    source_sequence = _sync_item_source_sequence(item)
    aggregate_identity = _sync_item_aggregate_key(table, operation, record_id, data, item)
    if source_server is None or source_sequence is None or aggregate_identity is None:
        return None

    aggregate_table, aggregate_key = aggregate_identity
    return SyncWatermarkContext(
        source_server=source_server,
        aggregate_table=aggregate_table,
        aggregate_key=aggregate_key,
        source_sequence=source_sequence,
        payload_hash=_sync_item_payload_hash(table, operation, record_id, data),
        operation=str(operation or ""),
        record_id=str(record_id) if record_id is not None else None,
    )


def _sync_watermark_lock_key(context: SyncWatermarkContext) -> str:
    return f"{context.source_server}:{context.aggregate_table}:{context.aggregate_key}"


async def _lock_sync_watermark_context(db: AsyncSession, context: SyncWatermarkContext) -> None:
    await db.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": _sync_watermark_lock_key(context)},
    )


async def _evaluate_sync_watermark(db: AsyncSession, context: SyncWatermarkContext | None) -> SyncWatermarkDecision:
    if context is None:
        return SyncWatermarkDecision("apply")

    try:
        await _lock_sync_watermark_context(db, context)
        result = await db.execute(
            select(SyncApplyWatermark)
            .where(
                SyncApplyWatermark.source_server == context.source_server,
                SyncApplyWatermark.aggregate_table == context.aggregate_table,
                SyncApplyWatermark.aggregate_key == context.aggregate_key,
            )
            .with_for_update()
        )
        watermark = _result_scalar_first(result)
    except Exception as exc:
        if getattr(settings, "sync_watermark_strict_mode", False):
            raise
        logger.warning(
            "Sync watermark unavailable; applying in compatibility mode",
            extra={
                "event": "sync.watermark_compatibility_apply",
                "source_server": context.source_server,
                "aggregate_table": context.aggregate_table,
                "aggregate_key_hash": hashlib.sha256(context.aggregate_key.encode()).hexdigest()[:16],
                **_summarize_exception(exc),
            },
        )
        return SyncWatermarkDecision("apply", "watermark_unavailable_compatibility")

    if watermark is None:
        return SyncWatermarkDecision("apply")

    current_sequence = coerce_positive_int(getattr(watermark, "last_source_sequence", None)) or 0
    current_hash = str(getattr(watermark, "last_payload_hash", "") or "")
    if context.source_sequence > current_sequence:
        return SyncWatermarkDecision("apply")
    if context.source_sequence < current_sequence:
        return SyncWatermarkDecision("stale", "older_source_sequence")
    if context.payload_hash == current_hash:
        return SyncWatermarkDecision("duplicate", "same_source_sequence_same_payload")
    return SyncWatermarkDecision("conflict", "same_source_sequence_different_payload")


async def _record_sync_watermark_applied(db: AsyncSession, context: SyncWatermarkContext | None) -> None:
    if context is None:
        return

    stmt = pg_insert(SyncApplyWatermark).values(
        source_server=context.source_server,
        aggregate_table=context.aggregate_table,
        aggregate_key=context.aggregate_key,
        last_source_sequence=context.source_sequence,
        last_payload_hash=context.payload_hash,
        last_operation=context.operation,
        last_record_id=context.record_id,
    )
    update_values = {
        "last_source_sequence": stmt.excluded.last_source_sequence,
        "last_payload_hash": stmt.excluded.last_payload_hash,
        "last_operation": stmt.excluded.last_operation,
        "last_record_id": stmt.excluded.last_record_id,
        "updated_at": sa_func.now(),
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_server", "aggregate_table", "aggregate_key"],
        set_=update_values,
        where=SyncApplyWatermark.last_source_sequence <= stmt.excluded.last_source_sequence,
    )

    try:
        await db.execute(stmt, execution_options={"is_sync": True})
    except Exception as exc:
        if getattr(settings, "sync_watermark_strict_mode", False):
            raise
        logger.warning(
            "Could not persist sync watermark; continuing in compatibility mode",
            extra={
                "event": "sync.watermark_persist_failed",
                "source_server": context.source_server,
                "aggregate_table": context.aggregate_table,
                "aggregate_key_hash": hashlib.sha256(context.aggregate_key.encode()).hexdigest()[:16],
                "source_sequence": context.source_sequence,
                **_summarize_exception(exc),
            },
        )


def _log_sync_watermark_decision(context: SyncWatermarkContext, decision: SyncWatermarkDecision) -> None:
    record_sync_watermark_decision(
        server_mode=settings.server_mode,
        table=context.aggregate_table,
        decision=decision.action,
        reason=decision.reason,
    )
    record_sync_conflict(
        server_mode=settings.server_mode,
        table=context.aggregate_table,
        reason=decision.reason or decision.action,
    )
    logger.warning(
        "Sync item blocked by source-sequence watermark",
        extra={
            "event": "sync.watermark_item_blocked",
            "source_server": context.source_server,
            "aggregate_table": context.aggregate_table,
            "aggregate_key_hash": hashlib.sha256(context.aggregate_key.encode()).hexdigest()[:16],
            "source_sequence": context.source_sequence,
            "operation": context.operation,
            "reason": decision.reason,
            "decision": decision.action,
        },
    )


def _sync_watermark_error_detail(context: SyncWatermarkContext, decision: SyncWatermarkDecision) -> dict[str, object]:
    return {
        "table": context.aggregate_table,
        "record_id": context.record_id,
        "reason": decision.reason or decision.action,
        "source_server": context.source_server,
        "source_sequence": context.source_sequence,
    }


@router.post("/receive")
async def receive_sync_data(
    items: list[dict], 
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_signature)
):
    """Receive sync data from other server"""
    logger.info(
        "Received sync batch",
        extra={
            "event": "sync.receive_batch",
            "item_count": len(items),
        },
    )
    
    # Sort items by dependency order: users first, then commodities, then offers, then trades
    sorted_items = sorted(items, key=lambda x: TABLE_ORDER.get(x.get('table', ''), 99))
    
    processed_count = 0
    errors = []
    error_details = []
    deferred_items = []
    new_offers = []
    terminal_offers = []
    completed_trade_offer_ids = []
    synced_deleted_user_telegram_effects: list[tuple[int, int]] = []
    user_changes_applied = False
    market_runtime_state_changed = False
    notification_user_ids = _notification_user_ids_from_items(sorted_items)

    try:
        # --- Pass 1: Process all items ---
        for item in sorted_items:
            # Handle Notification Relay
            if item.get("type") == "notification":
                try:
                    from core.notifications import send_telegram_message
                    chat_id = item.get("chat_id")
                    text = item.get("text")
                    parse_mode = item.get("parse_mode", "Markdown")
                    if chat_id and text:
                        await send_telegram_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                        processed_count += 1
                        logger.info(f"✅ Notification relayed to {chat_id}")
                except Exception as e:
                    logger.error(
                        "Failed to relay synced notification",
                        extra={
                            "event": "sync.notification_relay_failed",
                            **_summarize_exception(e),
                        },
                    )
                continue

            protocol_validation = validate_sync_protocol_metadata(item.get("sync_protocol"))
            if not protocol_validation.ok:
                error_detail = _sync_protocol_error_detail(item, protocol_validation)
                errors.append(error_detail)
                error_details.append(error_detail)
                logger.warning(
                    "Rejected sync item by protocol compatibility",
                    extra={
                        "event": "sync.protocol_rejected",
                        **error_detail,
                    },
                )
                continue

            rejection_reason = _sync_item_policy_rejection_reason(item)
            if rejection_reason:
                error_detail = _sync_error_detail(item, rejection_reason)
                errors.append(error_detail)
                error_details.append(error_detail)
                logger.warning(
                    "Rejected sync item by table policy",
                    extra={
                        "event": "sync.table_policy_rejected",
                        **error_detail,
                    },
                )
                continue

            parsed = _parse_item(item)
            if not parsed:
                error_detail = _sync_error_detail(item, "receiver_model_not_registered")
                errors.append(error_detail)
                error_details.append(error_detail)
                logger.warning(
                    "Rejected sync item without receiver model",
                    extra={
                        "event": "sync.receiver_model_not_registered",
                        **error_detail,
                    },
                )
                continue

            table, operation, model, data, record_id = parsed
            source_server = _sync_item_source_server(item)
            authority_rejection_reason = _sync_item_authority_rejection_reason(item, table)
            if authority_rejection_reason:
                error_detail = _sync_error_detail(item, authority_rejection_reason)
                errors.append(error_detail)
                error_details.append(error_detail)
                record_sync_source_authority_rejection(
                    server_mode=settings.server_mode,
                    table=table,
                    reason=authority_rejection_reason,
                )
                logger.warning(
                    "Rejected sync item by source authority policy",
                    extra={
                        "event": "sync.source_authority_rejected",
                        **error_detail,
                    },
                )
                continue

            registration_sync_decision = sanitize_registration_sync_payload(
                table=table,
                operation=operation,
                data=data,
                source_server=source_server,
                v2_enabled=bool(getattr(settings, "registration_sync_v2_enabled", False)),
                accept_unversioned=bool(
                    getattr(settings, "registration_sync_accept_unversioned", True)
                ),
            )
            if not registration_sync_decision.accepted:
                error_detail = _sync_error_detail(
                    item,
                    registration_sync_decision.reason or "registration_sync_policy_rejected",
                )
                errors.append(error_detail)
                error_details.append(error_detail)
                logger.warning(
                    "Rejected registration sync item",
                    extra={
                        "event": "sync.registration_policy_rejected",
                        **error_detail,
                    },
                )
                continue
            if registration_sync_decision.dropped_fields:
                logger.warning(
                    "Dropped unauthorized registration sync fields",
                    extra={
                        "event": "sync.registration_fields_dropped",
                        "table": table,
                        "record_id": record_id,
                        "source_server": source_server,
                        "dropped_fields": list(registration_sync_decision.dropped_fields),
                    },
                )
            data = registration_sync_decision.data

            try:
                watermark_context = _sync_watermark_context_from_item(
                    item,
                    table=table,
                    operation=operation,
                    record_id=record_id,
                    data=data,
                )
                watermark_decision = await _evaluate_sync_watermark(db, watermark_context)
                if watermark_context is not None and watermark_decision.action == "stale":
                    _log_sync_watermark_decision(watermark_context, watermark_decision)
                    processed_count += 1
                    continue
                if watermark_context is not None and watermark_decision.action == "duplicate":
                    processed_count += 1
                    record_sync_watermark_decision(
                        server_mode=settings.server_mode,
                        table=watermark_context.aggregate_table,
                        decision=watermark_decision.action,
                        reason=watermark_decision.reason,
                    )
                    logger.info(
                        "Duplicate sync item ignored by source-sequence watermark",
                        extra={
                            "event": "sync.watermark_duplicate_ignored",
                            "source_server": watermark_context.source_server,
                            "aggregate_table": watermark_context.aggregate_table,
                            "source_sequence": watermark_context.source_sequence,
                        },
                    )
                    continue
                if watermark_context is not None and watermark_decision.action == "conflict":
                    _log_sync_watermark_decision(watermark_context, watermark_decision)
                    error_detail = _sync_watermark_error_detail(watermark_context, watermark_decision)
                    errors.append(error_detail)
                    error_details.append(error_detail)
                    continue

                deleted_user_telegram_effect = await _synced_deleted_user_telegram_effect(
                    db,
                    table=table,
                    operation=operation,
                    record_id=record_id,
                    data=data,
                )
                apply_args = (
                    {"source_server": source_server}
                    if bool(getattr(settings, "registration_sync_v2_enabled", False))
                    else {}
                )
                result = await _apply_item(
                    db,
                    table,
                    operation,
                    record_id,
                    data,
                    model,
                    new_offers,
                    terminal_offers,
                    **apply_args,
                )
                if result in {'ok', 'ignored'}:
                    await _record_sync_watermark_applied(db, watermark_context)
                    processed_count += 1
                    if table == "users":
                        user_changes_applied = True
                    if result == 'ok':
                        if deleted_user_telegram_effect is not None:
                            synced_deleted_user_telegram_effects.append(deleted_user_telegram_effect)
                        if table == "market_runtime_state" and operation in {"INSERT", "UPDATE"}:
                            market_runtime_state_changed = True
                        completed_trade_offer_id = _completed_trade_offer_id_from_sync(table, data)
                        if completed_trade_offer_id:
                            completed_trade_offer_ids.append(completed_trade_offer_id)
                        logger.info(f"✅ Sync Item Applied: {table}:{record_id} ({operation})")
                    else:
                        logger.info(
                            "Sync item ignored by guard",
                            extra={
                                "event": "sync.item_ignored",
                                "table": table,
                                "record_id": record_id,
                                "operation": operation,
                            },
                        )
                elif result == 'deferred':
                    deferred_items.append((item, table, operation, model, data, record_id, watermark_context))
                else:
                    error_detail = _sync_error_detail(item, "apply_failed")
                    errors.append(error_detail)
                    error_details.append(error_detail)
            except Exception as e:
                logger.error(
                    "Unexpected sync application error",
                    extra={
                        "event": "sync.apply_unexpected_error",
                        "table": table,
                        "record_id": record_id,
                        **_summarize_exception(e),
                    },
                )
                error_detail = _sync_error_detail(item, "apply_exception")
                errors.append(error_detail)
                error_details.append(error_detail)

        # --- Pass 2: Retry deferred items (FK violations) ---
        if deferred_items:
            logger.info(f"🔄 Retrying {len(deferred_items)} deferred items...")
            for item, table, operation, model, data, record_id, watermark_context in deferred_items:
                try:
                    watermark_decision = await _evaluate_sync_watermark(db, watermark_context)
                    if watermark_context is not None and watermark_decision.action == "stale":
                        _log_sync_watermark_decision(watermark_context, watermark_decision)
                        processed_count += 1
                        continue
                    if watermark_context is not None and watermark_decision.action == "duplicate":
                        processed_count += 1
                        record_sync_watermark_decision(
                            server_mode=settings.server_mode,
                            table=watermark_context.aggregate_table,
                            decision=watermark_decision.action,
                            reason=watermark_decision.reason,
                        )
                        logger.info(
                            "Deferred duplicate sync item ignored by source-sequence watermark",
                            extra={
                                "event": "sync.watermark_deferred_duplicate_ignored",
                                "source_server": watermark_context.source_server,
                                "aggregate_table": watermark_context.aggregate_table,
                                "source_sequence": watermark_context.source_sequence,
                            },
                        )
                        continue
                    if watermark_context is not None and watermark_decision.action == "conflict":
                        _log_sync_watermark_decision(watermark_context, watermark_decision)
                        error_detail = _sync_watermark_error_detail(watermark_context, watermark_decision)
                        errors.append(error_detail)
                        error_details.append(error_detail)
                        continue

                    deleted_user_telegram_effect = await _synced_deleted_user_telegram_effect(
                        db,
                        table=table,
                        operation=operation,
                        record_id=record_id,
                        data=data,
                    )
                    apply_args = (
                        {"source_server": _sync_item_source_server(item)}
                        if bool(getattr(settings, "registration_sync_v2_enabled", False))
                        else {}
                    )
                    result = await _apply_item(
                        db,
                        table,
                        operation,
                        record_id,
                        data,
                        model,
                        new_offers,
                        terminal_offers,
                        **apply_args,
                    )
                    if result in {'ok', 'ignored'}:
                        await _record_sync_watermark_applied(db, watermark_context)
                        processed_count += 1
                        if table == "users":
                            user_changes_applied = True
                        if result == 'ok':
                            if deleted_user_telegram_effect is not None:
                                synced_deleted_user_telegram_effects.append(deleted_user_telegram_effect)
                            if table == "market_runtime_state" and operation in {"INSERT", "UPDATE"}:
                                market_runtime_state_changed = True
                            completed_trade_offer_id = _completed_trade_offer_id_from_sync(table, data)
                            if completed_trade_offer_id:
                                completed_trade_offer_ids.append(completed_trade_offer_id)
                            logger.info(f"✅ Deferred item applied: {table}:{record_id}")
                        else:
                            logger.info(
                                "Deferred sync item ignored by guard",
                                extra={
                                    "event": "sync.deferred_item_ignored",
                                    "table": table,
                                    "record_id": record_id,
                                    "operation": operation,
                                },
                            )
                    else:
                        error_detail = _sync_error_detail(item, "deferred_foreign_key_dependency_missing")
                        errors.append(error_detail)
                        error_details.append(error_detail)
                        logger.error(
                            "Deferred item still failed",
                            extra={
                                "event": "sync.deferred_item_still_failed",
                                "table": table,
                                "record_id": record_id,
                            },
                        )
                except Exception as e:
                    logger.error(
                        "Deferred retry error",
                        extra={
                            "event": "sync.deferred_retry_error",
                            "table": table,
                            "record_id": record_id,
                            **_summarize_exception(e),
                        },
                    )
                    error_detail = _sync_error_detail(item, "deferred_retry_exception")
                    errors.append(error_detail)
                    error_details.append(error_detail)

        if user_changes_applied:
            try:
                if _sync_batch_has_production_full_matrix_users(sorted_items):
                    logger.info(
                        "Skipping mandatory channel rollout for production full-matrix synthetic users",
                        extra={
                            "event": "sync.mandatory_channel_rollout_skipped_for_full_matrix",
                        },
                    )
                else:
                    await ensure_mandatory_channel_rollout(db)
            except Exception as exc:
                logger.error(
                    "Failed to refresh mandatory channel rollout after sync",
                    extra={
                        "event": "sync.mandatory_channel_rollout_refresh_failed",
                        **_summarize_exception(exc),
                    },
                )

        await db.commit()

        # --- Fix sequences after sync to avoid ID collision ---
        # Synced records use explicit IDs which don't advance PostgreSQL sequences.
        # Without this fix, next local INSERT may try an ID that already exists.
        items_tables = {i.get('table') for i in sorted_items}
        for tbl_name in items_tables:
            seq_info = SEQUENCE_MAP.get(tbl_name)
            if seq_info:
                seq_name, real_table = seq_info
                try:
                    for sequence_sql in _partitioned_sequence_alignment_sql(seq_name, real_table, settings.server_mode):
                        await db.execute(sa_text(sequence_sql))
                    logger.info(
                        "🔢 Sequence %s aligned to %s partition",
                        seq_name,
                        settings.server_mode,
                    )
                except Exception as seq_err:
                    logger.warning(
                        "Failed to fix sequence after sync",
                        extra={
                            "event": "sync.sequence_fix_failed",
                            "sequence_name": seq_name,
                            **_summarize_exception(seq_err),
                        },
                    )
        await db.commit()

        if market_runtime_state_changed:
            try:
                await reconcile_market_runtime_side_effects_for_current_state(db, source="sync_receive")
            except Exception as exc:
                logger.error(
                    "Failed to reconcile market runtime side effects after sync",
                    extra={
                        "event": "sync.market_runtime_side_effects_reconcile_failed",
                        **_summarize_exception(exc),
                    },
                )

        await _run_synced_deleted_user_telegram_effects(synced_deleted_user_telegram_effects)

        # Refresh caches for affected tables

        if "trading_settings" in items_tables:
            try:
                from core.trading_settings import refresh_settings_cache_async
                await refresh_settings_cache_async()
                logger.info("🔄 Trading settings cache refreshed")
            except Exception as e:
                 logger.error(
                     "Failed to refresh settings cache",
                     extra={
                         "event": "sync.settings_cache_refresh_failed",
                         **_summarize_exception(e),
                     },
                 )

        if "notifications" in items_tables:
            await _refresh_notification_unread_counts(db, notification_user_ids)

        if items_tables & {"commodities", "commodity_aliases"}:
            try:
                from core.cache import invalidate_commodities_cache
                await invalidate_commodities_cache()
                logger.info("🔄 Commodities cache invalidated after sync")
            except Exception as e:
                logger.error(
                    "Failed to invalidate commodities cache",
                    extra={
                        "event": "sync.commodities_cache_invalidation_failed",
                        **_summarize_exception(e),
                    },
                )
            # Also invalidate bot's commodity cache
            try:
                from bot.utils.redis_helpers import invalidate_commodity_cache
                await invalidate_commodity_cache()
            except Exception:
                pass

        if "admin_market_messages" in items_tables:
            try:
                from core.cache import invalidate_admin_market_current_cache
                await invalidate_admin_market_current_cache()
                logger.info("🔄 Admin market current cache invalidated after sync")
            except Exception as e:
                logger.error(
                    "Failed to invalidate admin market current cache",
                    extra={
                        "event": "sync.admin_market_current_cache_invalidation_failed",
                        **_summarize_exception(e),
                    },
                )

        if settings.server_mode == "iran" and new_offers:
            try:
                if await _active_publication_gated_for_sync_receive("webapp_market"):
                    logger.info(
                        "Synced active offer realtime publication is gated",
                        extra={
                            "event": "sync.created_offer_realtime_gated",
                            "offer_count": len(set(new_offers)),
                        },
                    )
                else:
                    await _publish_synced_offer_created_realtime_after_sync(db, new_offers)
            except Exception as e:
                logger.error(
                    "Error publishing synced created offer realtime events",
                    extra={
                        "event": "sync.created_offer_realtime_publish_batch_failed",
                        **_summarize_exception(e),
                    },
                )

        terminal_realtime_offer_ids = [*terminal_offers, *completed_trade_offer_ids]
        if settings.server_mode == "iran" and terminal_realtime_offer_ids:
            try:
                await _publish_terminal_offer_realtime_after_sync(db, terminal_realtime_offer_ids)
            except Exception as e:
                logger.error(
                    "Error publishing synced terminal offer realtime events",
                    extra={
                        "event": "sync.terminal_offer_realtime_publish_batch_failed",
                        **_summarize_exception(e),
                    },
                )
        
        # --- Handle Offer Publishing on Foreign Server ---
        # Uses SELECT FOR UPDATE SKIP LOCKED to prevent duplicate sends
        # (same sync item may arrive via both direct-push and sync_worker)
        if settings.server_mode != "iran" and new_offers:
            try:
                telegram_publication_gated = await _active_publication_gated_for_sync_receive("telegram_channel")
                if telegram_publication_gated:
                    logger.info(
                        "Synced active offer Telegram publication is gated",
                        extra={
                            "event": "sync.synced_offer_telegram_publication_gated",
                            "offer_count": len(set(new_offers)),
                        },
                    )
                else:
                    from sqlalchemy.orm import selectinload
                    from models.offer import OfferStatus
                    from api.routers.offers import send_offer_to_channel
                    from core.services.telegram_offer_publication_service import publish_offer_to_telegram_channel_once

                    unique_offer_ids = list(set(new_offers))
                    logger.info(f"📋 Channel publish candidates: {unique_offer_ids}")

                    for oid in unique_offer_ids:
                        try:
                            async with db.begin_nested():
                                stmt = select(Offer).options(
                                    selectinload(Offer.user),
                                    selectinload(Offer.commodity),
                                ).where(
                                    Offer.id == oid,
                                    Offer.status == OfferStatus.ACTIVE,
                                ).with_for_update(skip_locked=True)
                                result = await db.execute(stmt)
                                offer = result.scalars().first()

                                if offer:
                                    publication_result = await publish_offer_to_telegram_channel_once(
                                        db,
                                        offer,
                                        offer.user,
                                        send_offer_to_channel=send_offer_to_channel,
                                    )
                                    if publication_result.message_id:
                                        logger.info(
                                            "📣 Published synced offer %s to Telegram. MsgID: %s",
                                            offer.id,
                                            publication_result.message_id,
                                        )
                                    else:
                                        logger.warning(
                                            "offer Telegram publication returned no message id",
                                            extra={
                                                "event": "sync.synced_offer_publish_empty_result",
                                                "offer_id": oid,
                                                "publication_error_code": publication_result.error_code,
                                                "publication_status": getattr(publication_result.status, "value", publication_result.status),
                                            },
                                        )
                                else:
                                    logger.info(f"⏭️ Offer {oid} already published or locked by another request")
                        except Exception as e:
                            logger.error(
                                "Failed to publish synced offer",
                                extra={
                                    "event": "sync.synced_offer_publish_failed",
                                    "offer_id": oid,
                                    **_summarize_exception(e),
                                },
                            )

                    await db.commit()
            except ImportError:
                logger.error("Could not import send_offer_to_channel")
            except Exception as e:
                logger.error(
                    "Error publishing synced offers",
                    extra={
                        "event": "sync.synced_offer_publish_batch_failed",
                        **_summarize_exception(e),
                    },
                )

        # --- Handle Terminal Offer Telegram State on Foreign Server ---
        # Terminal sync can arrive through direct-push and worker replay. The
        # helper treats Telegram "message is not modified" as success, so replay
        # is safe and does not create duplicate visible tags.
        terminal_telegram_offer_ids = [*terminal_offers, *completed_trade_offer_ids]
        if settings.server_mode != "iran" and terminal_telegram_offer_ids:
            try:
                from sqlalchemy.orm import selectinload
                from core.services.telegram_offer_channel_service import apply_offer_channel_state
                from core.services.telegram_offer_publication_service import load_telegram_publication_state_for_update

                unique_offer_ids = list(set(terminal_telegram_offer_ids))
                stmt = (
                    select(Offer)
                    .options(selectinload(Offer.commodity))
                    .where(Offer.id.in_(unique_offer_ids))
                )
                result = await db.execute(stmt)
                terminal_offer_rows = result.scalars().all()
                for offer in terminal_offer_rows:
                    try:
                        publication_state = await load_telegram_publication_state_for_update(db, offer)
                        await apply_offer_channel_state(
                            offer,
                            publication_state=publication_state,
                            reason="sync_terminal_offer",
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to apply synced terminal offer Telegram state",
                            extra={
                                "event": "sync.terminal_offer_telegram_state_failed",
                                "offer_id": getattr(offer, "id", None),
                                **_summarize_exception(e),
                            },
                        )
            except Exception as e:
                logger.error(
                    "Error applying synced terminal offer Telegram states",
                    extra={
                        "event": "sync.terminal_offer_telegram_state_batch_failed",
                        **_summarize_exception(e),
                    },
                )
        
        if errors:
            response = {"status": "partial", "processed": processed_count, "errors": len(errors)}
            if error_details:
                response["error_items"] = error_details
            return response
        return {"status": "success", "processed": processed_count}
        
    except Exception as e:
        await db.rollback()
        logger.error(
            "Error processing sync batch",
            extra={
                "event": "sync.receive_batch_error",
                **_summarize_exception(e),
            },
        )
        raise HTTPException(status_code=500, detail="Sync batch processing failed")


@router.post("/resync")
async def resync_from_changelog(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
    table_filter: str = None,
    target_server: str = None,
):
    """
    Resync unsynced change_log entries to the target server.
    Requires Dev API Key (X-Dev-Api-Key header).
    Optional: ?table_filter=users&limit=200
    """
    _require_dev_key(request)

    target_url = peer_server_url_for(target_server) if target_server else default_peer_server_url()
    api_key = getattr(settings, "sync_api_key", None)

    if not target_url or not api_key:
        raise HTTPException(status_code=500, detail="Sync not configured (peer server URL or SYNC_API_KEY missing)")

    if target_url.endswith("/"):
        target_url = target_url[:-1]

    # Read unsynced change_log entries
    query = select(ChangeLog).where(ChangeLog.synced == False).order_by(ChangeLog.id)
    if table_filter:
        query = query.where(ChangeLog.table_name == table_filter)
    query = query.limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    if not entries:
        return {"status": "ok", "message": "No unsynced entries found", "processed": 0}

    import httpx as httpx_mod

    processed = 0
    errors = 0
    batch_size = 50  # Send items in batches for efficiency

    assert_runtime_sync_transport_allowed()
    async with httpx_mod.AsyncClient(timeout=30.0, verify=runtime_sync_tls_verify_setting()) as client:
        # Group entries into batches
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            items = []
            for entry in batch:
                try:
                    data = json.loads(entry.data) if isinstance(entry.data, str) else entry.data
                    data = sanitize_sync_payload(entry.table_name, data)
                    item_payload = {
                        "type": "db_change",
                        "operation": entry.operation,
                        "table": entry.table_name,
                        "id": entry.record_id,
                        "data": data,
                        "hash": entry.hash,
                        "timestamp": entry.timestamp.timestamp() if entry.timestamp else time.time(),
                        "change_log_id": entry.id,
                        "sync_protocol": build_sync_protocol_metadata(),
                        "sync_meta": build_sync_metadata(
                            entry.table_name,
                            entry.record_id,
                            entry.operation,
                            data,
                            change_log_id=entry.id,
                            source_server=current_server(),
                        ),
                    }
                    public_identity = build_sync_public_identity(entry.table_name, entry.record_id, data)
                    if public_identity is not None:
                        item_payload["public_identity"] = public_identity
                    items.append(item_payload)
                except Exception as e:
                    logger.error(
                        "Resync parse error",
                        extra={
                            "event": "sync.resync.parse_error",
                            "table_name": entry.table_name,
                            "record_id": entry.record_id,
                            **_summarize_exception(e),
                        },
                    )
                    errors += 1

            if not items:
                continue

            try:
                json_body = json.dumps(items, sort_keys=True, default=str)
                ts = int(time.time())
                message = f"{ts}:{json_body}"
                signature = hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()

                response = await client.post(
                    f"{target_url}/api/sync/receive",
                    content=json_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": api_key,
                        "X-Timestamp": str(ts),
                        "X-Signature": signature
                    }
                )

                response_payload = {}
                try:
                    response_payload = response.json()
                except ValueError:
                    response_payload = {}

                response_errors = int(response_payload.get("errors") or 0) if isinstance(response_payload, dict) else 0
                response_status = response_payload.get("status") if isinstance(response_payload, dict) else None

                if response.status_code == 200 and response_errors == 0 and response_status in {"success", "ok"}:
                    for entry in batch:
                        entry.synced = True
                    processed += len(batch)
                else:
                    logger.warning(
                        "Resync batch failed",
                        extra={
                            "event": "sync.resync.batch_failed",
                            "batch_size": len(batch),
                            "peer_sync_status": response_status,
                            "peer_sync_errors": response_errors,
                            **_summarize_peer_response(response),
                        },
                    )
                    errors += len(batch)

            except Exception as e:
                logger.error(
                    "Resync batch error",
                    extra={
                        "event": "sync.resync.batch_exception",
                        "batch_size": len(batch),
                        **_summarize_exception(e),
                    },
                )
                errors += len(batch)

    await db.commit()
    return {"status": "ok", "processed": processed, "errors": errors, "total_entries": len(entries)}


def _coerce_parity_status_max_age_seconds() -> int:
    try:
        return max(int(getattr(settings, "sync_parity_status_max_age_seconds", 900) or 900), 1)
    except (TypeError, ValueError):
        return 900


async def _load_latest_parity_summary(redis_client) -> dict[str, object] | None:
    if redis_client is None:
        return None
    raw = await redis_client.get(SYNC_PARITY_STATUS_REDIS_KEY)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if "status" not in payload or "observed_at" not in payload:
        return None
    return payload


def _parity_status_payload(latest_summary: dict[str, object] | None = None) -> dict[str, object]:
    quick_tables = synced_parity_table_names("quick")
    deep_tables = synced_parity_table_names("deep")
    max_age_seconds = _coerce_parity_status_max_age_seconds()
    if latest_summary:
        latest_summary = summarize_parity_comparison(
            latest_summary,
            mode=str(latest_summary.get("mode") or "unknown"),
            observed_at=str(latest_summary.get("observed_at") or ""),
            max_age_seconds=max_age_seconds,
        )
        comparison_status = str(latest_summary.get("status") or "unknown") if latest_summary.get("fresh") else "stale"
    else:
        comparison_status = "missing"
    return {
        "status": "available",
        "comparison_status": comparison_status,
        "fresh": bool(latest_summary and latest_summary.get("fresh")),
        "latest_comparison": latest_summary,
        "freshness_required_seconds": max_age_seconds,
        "snapshot_endpoint": "/api/sync/parity/snapshot",
        "status_endpoint": "/api/sync/parity/status",
        "quick_table_count": len(quick_tables),
        "deep_table_count": len(deep_tables),
    }


@router.get("/parity/snapshot")
async def get_sync_parity_snapshot(
    request: Request,
    mode: str = "quick",
    max_rows_per_table: int = 5000,
    db: AsyncSession = Depends(get_db),
):
    """Return a redacted local parity snapshot for operator comparison."""
    _require_observability_key(request)
    normalized_mode = str(mode or "quick").strip().lower()
    if normalized_mode not in {"quick", "deep"}:
        raise HTTPException(status_code=400, detail="mode must be quick or deep")
    if max_rows_per_table < 1 or max_rows_per_table > 50000:
        raise HTTPException(status_code=400, detail="max_rows_per_table must be between 1 and 50000")

    try:
        snapshot = await build_database_parity_snapshot(
            db,
            mode=normalized_mode,
            max_rows_per_table=max_rows_per_table,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot["server_mode"] = settings.server_mode
    return snapshot


@router.post("/parity/status")
async def record_sync_parity_status(
    request: Request,
    comparison: dict = Body(...),
):
    """Store the latest operator-produced parity comparison summary."""
    _require_observability_key(request)
    summary = summarize_parity_comparison(
        comparison,
        mode=str(comparison.get("mode") or "unknown"),
        observed_at=str(comparison.get("compared_at") or comparison.get("observed_at") or ""),
        max_age_seconds=_coerce_parity_status_max_age_seconds(),
    )
    summary["server_mode"] = settings.server_mode
    summary["stored_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        redis_client = get_redis_client()
        await redis_client.set(
            SYNC_PARITY_STATUS_REDIS_KEY,
            json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ex=max(_coerce_parity_status_max_age_seconds() * 96, 3600),
        )
    except Exception as exc:
        logger.warning(
            "Could not store sync parity status",
            extra={
                "event": "sync.parity_status.store_failed",
                "log_class": "integration",
                "server_mode": settings.server_mode,
                **_summarize_exception(exc),
            },
        )
        raise HTTPException(status_code=503, detail="Could not store sync parity status") from exc

    record_sync_parity_summary(
        server_mode=settings.server_mode,
        status=str(summary.get("status") or "unknown"),
        fresh=bool(summary.get("fresh")),
        business_drift_count=int(summary.get("business_drift_count") or 0),
        critical_drift_count=int(summary.get("critical_drift_count") or 0),
        incomplete_count=int(summary.get("incomplete_count") or 0),
    )
    logger.info(
        "Sync parity status stored",
        extra={
            "event": "sync.parity_status.stored",
            "log_class": "integration",
            "server_mode": settings.server_mode,
            "parity_status": summary.get("status"),
            "parity_fresh": summary.get("fresh"),
            "business_drift_count": summary.get("business_drift_count"),
            "critical_drift_count": summary.get("critical_drift_count"),
        },
    )
    return {"status": "ok", "parity_status": summary}


@router.get("/health")
async def get_sync_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return local cross-server sync backlog and lag state for operators."""
    _require_observability_key(request)

    summary_stmt = select(
        sa_func.count(ChangeLog.id),
        sa_func.min(ChangeLog.created_at),
    ).where(ChangeLog.synced == False)
    result = await db.execute(summary_stmt)
    unsynced_count, oldest_unsynced_at = result.one()

    table_stmt = (
        select(ChangeLog.table_name, sa_func.count(ChangeLog.id))
        .where(ChangeLog.synced == False)
        .group_by(ChangeLog.table_name)
        .order_by(ChangeLog.table_name)
    )
    table_rows = await db.execute(table_stmt)
    unsynced_by_table = {table: int(count or 0) for table, count in table_rows.all()}

    outbound_queue = 0
    retry_queue = 0
    redis_ok = True
    redis_client = None
    try:
        redis_client = get_redis_client()
        outbound_queue = int(await redis_client.llen("sync:outbound") or 0)
        retry_queue = int(await redis_client.llen("sync:retry") or 0)
        latest_parity_summary = await _load_latest_parity_summary(redis_client)
    except Exception as exc:
        redis_ok = False
        latest_parity_summary = None
        logger.warning(
            "Could not read sync Redis queues",
            extra={
                "event": "sync.health.redis_error",
                "log_class": "integration",
                "server_mode": settings.server_mode,
                "error_type": type(exc).__name__,
            },
        )

    if not redis_ok:
        active_publication_gate = {"enabled": False, "status": "redis_unavailable"}
    else:
        try:
            active_publication_gate = await load_active_publication_gate(redis_client)
        except Exception as exc:
            active_publication_gate = {
                "enabled": False,
                "status": "error",
                "error_type": type(exc).__name__,
            }
            logger.warning(
                "Could not read active publication gate for sync health",
                extra={
                    "event": "sync.health.active_publication_gate_error",
                    "log_class": "integration",
                    "server_mode": settings.server_mode,
                    **_summarize_exception(exc),
                },
            )

    oldest_age = _age_seconds(oldest_unsynced_at)
    record_sync_health(
        server_mode=settings.server_mode,
        unsynced_count=int(unsynced_count or 0),
        oldest_unsynced_age_seconds=oldest_age,
        outbound_queue=outbound_queue,
        retry_queue=retry_queue,
    )
    try:
        publication_reconciliation = await publication_observability_summary(
            db,
            server_mode=settings.server_mode,
            unsynced_by_table=unsynced_by_table,
        )
        record_offer_publication_health(
            server_mode=settings.server_mode,
            state_counts=publication_reconciliation.get("state_counts"),
            finding_counts=publication_reconciliation.get("finding_counts"),
        )
    except Exception as exc:
        publication_reconciliation = {
            "status": "error",
            "error_type": type(exc).__name__,
        }
        logger.warning(
            "Could not collect publication reconciliation health",
            extra={
                "event": "sync.health.publication_reconciliation_error",
                "log_class": "integration",
                "server_mode": settings.server_mode,
                **_summarize_exception(exc),
            },
        )
    payload = {
        "status": "ok",
        "server_mode": settings.server_mode,
        "peer_server_url_configured": bool(default_peer_server_url()),
        "redis_ok": redis_ok,
        "unsynced_change_log_count": int(unsynced_count or 0),
        "oldest_unsynced_age_seconds": round(oldest_age, 3),
        "unsynced_by_table": unsynced_by_table,
        "redis_queues": {
            "sync:outbound": outbound_queue,
            "sync:retry": retry_queue,
        },
        "active_publication_gate": active_publication_gate,
        "publication_reconciliation": publication_reconciliation,
        "parity_status": _parity_status_payload(latest_parity_summary),
        "registration_sync": registration_sync_capabilities(settings),
    }
    latest_parity = payload["parity_status"].get("latest_comparison") if isinstance(payload.get("parity_status"), dict) else None
    if isinstance(latest_parity, dict):
        record_sync_parity_summary(
            server_mode=settings.server_mode,
            status=str(latest_parity.get("status") or "unknown"),
            fresh=bool(latest_parity.get("fresh")),
            business_drift_count=int(latest_parity.get("business_drift_count") or 0),
            critical_drift_count=int(latest_parity.get("critical_drift_count") or 0),
            incomplete_count=int(latest_parity.get("incomplete_count") or 0),
        )
    logger.info(
        "Sync health sampled",
        extra={
            "event": "sync.health",
            "log_class": "integration",
            "server_mode": payload["server_mode"],
            "redis_ok": payload["redis_ok"],
            "unsynced_change_log_count": payload["unsynced_change_log_count"],
            "oldest_unsynced_age_seconds": payload["oldest_unsynced_age_seconds"],
            "sync_outbound_queue_length": outbound_queue,
            "sync_retry_queue_length": retry_queue,
            "active_publication_gate_enabled": active_publication_gate.get("enabled"),
            "publication_reconciliation_status": publication_reconciliation.get("status"),
            "publication_reconciliation_findings": publication_reconciliation.get("finding_counts"),
        },
    )
    return payload
