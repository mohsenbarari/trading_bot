from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from models.change_log import ChangeLog
from core.config import settings
from core.audit_logger import audit_log
from core.metrics import record_offer_publication_health, record_sync_conflict, record_sync_health
from core.redis import get_redis_client
from core.server_routing import default_peer_server_url, peer_server_url_for
from core.sync_field_policy import sanitize_sync_payload
from core.sync_metadata import build_sync_metadata, build_sync_public_identity, coerce_positive_int
from core.sync_protocol import build_sync_protocol_metadata, validate_sync_protocol_metadata
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.services.offer_publication_reconciliation_service import publication_observability_summary
import hmac
import hashlib
import ipaddress
import time
import json
import logging
from datetime import date as date_cls, datetime, time as time_cls, timezone

router = APIRouter()
logger = logging.getLogger(__name__)
OBSERVABILITY_API_KEY_HEADER = "X-Observability-Api-Key"


def _require_dev_key(request: Request) -> None:
    dev_key = request.headers.get("X-Dev-Api-Key")
    if not settings.dev_api_key or dev_key != settings.dev_api_key:
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
    if configured_key and supplied_key == configured_key:
        return
    if _is_loopback_sync_request(request):
        return
    if not configured_key:
        raise HTTPException(status_code=503, detail="Observability API key is not configured for remote sync health access")
    if supplied_key != configured_key:
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


def _summarize_payload(data) -> dict[str, object]:
    if not isinstance(data, dict):
        return {"data_kind": type(data).__name__}
    return {
        "data_kind": "dict",
        "data_key_count": len(data),
    }


def _enum_value(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


TERMINAL_OFFER_STATUSES = {"completed", "expired", "cancelled"}


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
    "chats": 3,
    "chat_members": 4,
    "invitations": 5,
    "admin_market_messages": 6,
    "admin_broadcast_messages": 7,
    "notifications": 8,
    "user_blocks": 9,
    "commodities": 10,
    "commodity_aliases": 11,
    "trading_settings": 12,
    "market_schedule_overrides": 13,
    "market_runtime_state": 14,
    "offers": 15,
    "offer_publication_states": 16,
    "offer_requests": 17,
    "trades": 18,
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
        
    
    if api_key != settings.sync_api_key:
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
        
        if signature != expected_signature:
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

from sqlalchemy import insert, update, delete, select, text as sa_text
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from core.enums import ChatType
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.user import User
from models.invitation import Invitation
from models.notification import Notification
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.offer import Offer
from models.offer_request import OfferRequest
from models.offer_publication_state import OfferPublicationState
from models.trade import Trade
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
    "commodities": "name",
    "commodity_aliases": "alias",
    "users": "telegram_id",
    "invitations": "token",
    "market_schedule_overrides": "date",
    "trades": "trade_number",
    "offer_publication_states": "dedupe_key",
}

SAFE_NATURAL_VALUE_LOG_KEYS = {
    ("commodities", "name"),
    ("commodity_aliases", "alias"),
    ("market_schedule_overrides", "date"),
    ("trades", "trade_number"),
    ("offer_publication_states", "dedupe_key"),
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
    "invitations": ("invitations_id_seq", "invitations"),
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
        "chats": Chat,
        "chat_members": ChatMember,
        "invitations": Invitation,
        "admin_market_messages": AdminMarketMessage,
        "admin_broadcast_messages": AdminBroadcastMessage,
        "notifications": Notification,
        "offers": Offer,
        "offer_publication_states": OfferPublicationState,
        "offer_requests": OfferRequest,
        "trades": Trade,
        "commodities": Commodity,
        "commodity_aliases": CommodityAlias,
        "market_schedule_overrides": MarketScheduleOverride,
        "market_runtime_state": MarketRuntimeState,
        "trading_settings": TradingSetting,
        "user_blocks": UserBlock
    }
    return mapping.get(table_name)


def _is_mandatory_channel_record(data: dict) -> bool:
    return (
        data.get("type") == "channel"
        and bool(data.get("is_system"))
        and bool(data.get("is_mandatory"))
    )


def _is_mandatory_chat_member_record(data: dict) -> bool:
    return (
        data.get("chat_type") == "channel"
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


def _build_upsert_stmt(model, table, data):
    """Build the INSERT ON CONFLICT statement for a given model and data."""
    stmt = pg_insert(model).values(**data)

    if table == "users":
        set_dict = {}
        for k in data:
            if k in USER_COUNTER_FIELDS:
                set_dict[k] = sa_func.greatest(getattr(model, k), stmt.excluded[k])
            else:
                set_dict[k] = stmt.excluded[k]
        return stmt.on_conflict_do_update(index_elements=['id'], set_=set_dict)
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
        return stmt.on_conflict_do_update(index_elements=['trade_number'], set_=set_dict)
    elif table == "offer_publication_states" and data.get("dedupe_key"):
        set_dict = {key: value for key, value in data.items() if key not in {"id", "dedupe_key"}}
        return stmt.on_conflict_do_update(index_elements=['dedupe_key'], set_=set_dict)
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
        return stmt.on_conflict_do_update(
            index_elements=['request_home_server', 'idempotency_key'],
            index_where=model.idempotency_key.isnot(None),
            set_=set_dict,
        )
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


def _result_scalar_one_or_none(result):
    try:
        return result.scalar_one_or_none()
    except AttributeError:
        pass
    return _result_scalar_first(result)


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


async def _resolve_local_record_id_by_public_identity(
    db: AsyncSession,
    table: str,
    data: dict,
) -> int | None:
    if table == "offers":
        return await _resolve_offer_id_by_public_id(db, data.get("offer_public_id"))
    if table == "trades":
        return await _resolve_trade_id_by_trade_number(db, data.get("trade_number"))
    if table == "offer_publication_states":
        return await _resolve_publication_state_id_by_dedupe_key(db, data.get("dedupe_key"))
    if table == "offer_requests":
        return await _resolve_offer_request_id_by_idempotency(db, data)
    return None


def _sync_table_has_public_identity(table: str, data: dict) -> bool:
    if table == "offers":
        return bool(_nonempty_text(data.get("offer_public_id")))
    if table == "trades":
        return data.get("trade_number") not in (None, "")
    if table == "offer_publication_states":
        return bool(_nonempty_text(data.get("dedupe_key")))
    if table == "offer_requests":
        return bool(_nonempty_text(data.get("request_home_server")) and _nonempty_text(data.get("idempotency_key")))
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


def _offer_payload_needs_ordering_check(data: dict) -> bool:
    incoming_status = _enum_value(data.get("status"))
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


async def _apply_item(
    db: AsyncSession,
    table: str,
    operation: str,
    record_id,
    data: dict,
    model,
    new_offers: list,
    terminal_offers: list | None = None,
):
    """
    Apply a single sync item using SAVEPOINT so failures don't kill the transaction.
    Handles:
      1. Normal upsert by ID
      2. UniqueViolation fallback → update by natural key
      3. ForeignKeyViolation → returns 'deferred' for retry
    Returns: 'ok', 'deferred', or 'error'
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
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=data)
        async with db.begin_nested():
            await db.execute(stmt, execution_options={"is_sync": True})
        return 'ok'

    if operation in ("INSERT", "UPDATE"):
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

        uses_public_identity = _sync_table_has_public_identity(table, data)
        if not uses_public_identity:
            data['id'] = record_id

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

        data = _filter_model_columns(model, data)
        stmt = _build_upsert_stmt(model, table, data)

        try:
            async with db.begin_nested():
                execute_result = await db.execute(stmt, execution_options={"is_sync": True})
                if (
                    table == "offers"
                    and _offer_payload_needs_ordering_check(data)
                    and getattr(execute_result, "rowcount", None) == 0
                ):
                    _log_stale_offer_sync_ignored(record_id, operation, data, "atomic_upsert_guard_noop")
            if table == "offers" and (track_new_offer or track_offer_realtime_or_terminal):
                applied_offer_id = await _resolve_local_record_id_by_public_identity(db, table, data)
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
                natural_value = data.get(natural_key) if natural_key else None

                if natural_value is not None:
                    try:
                        # Update existing record by natural key
                        natural_col = getattr(model, natural_key)
                        update_data = {k: v for k, v in data.items() if k != 'id' and k != natural_key}
                        if update_data:
                            async with db.begin_nested():
                                stmt_update = (
                                    update(model)
                                    .where(natural_col == natural_value)
                                    .values(**update_data)
                                )
                                await db.execute(stmt_update, execution_options={"is_sync": True})
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

                stmt = delete(model).where(model.id == record_id)
                await db.execute(stmt, execution_options={"is_sync": True})
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
    user_changes_applied = False
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

            try:
                result = await _apply_item(db, table, operation, record_id, data, model, new_offers, terminal_offers)
                if result == 'ok':
                    processed_count += 1
                    if table == "users":
                        user_changes_applied = True
                    completed_trade_offer_id = _completed_trade_offer_id_from_sync(table, data)
                    if completed_trade_offer_id:
                        completed_trade_offer_ids.append(completed_trade_offer_id)
                    logger.info(f"✅ Sync Item Applied: {table}:{record_id} ({operation})")
                elif result == 'deferred':
                    deferred_items.append((table, operation, model, data, record_id))
                else:
                    errors.append(f"{table}:{record_id}")
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
                errors.append(f"{table}:{record_id}")

        # --- Pass 2: Retry deferred items (FK violations) ---
        if deferred_items:
            logger.info(f"🔄 Retrying {len(deferred_items)} deferred items...")
            for table, operation, model, data, record_id in deferred_items:
                try:
                    result = await _apply_item(db, table, operation, record_id, data, model, new_offers, terminal_offers)
                    if result == 'ok':
                        processed_count += 1
                        if table == "users":
                            user_changes_applied = True
                        completed_trade_offer_id = _completed_trade_offer_id_from_sync(table, data)
                        if completed_trade_offer_id:
                            completed_trade_offer_ids.append(completed_trade_offer_id)
                        logger.info(f"✅ Deferred item applied: {table}:{record_id}")
                    else:
                        errors.append(f"{table}:{record_id} (deferred)")
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
                    errors.append(f"{table}:{record_id}")

        if user_changes_applied:
            try:
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
        if settings.server_mode != "iran" and terminal_offers:
            try:
                from sqlalchemy.orm import selectinload
                from core.services.telegram_offer_channel_service import apply_offer_channel_state
                from core.services.telegram_offer_publication_service import load_telegram_publication_state_for_update

                unique_offer_ids = list(set(terminal_offers))
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

    async with httpx_mod.AsyncClient(timeout=30.0, verify=False) as client:
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
    try:
        redis_client = get_redis_client()
        outbound_queue = int(await redis_client.llen("sync:outbound") or 0)
        retry_queue = int(await redis_client.llen("sync:retry") or 0)
    except Exception as exc:
        redis_ok = False
        logger.warning(
            "Could not read sync Redis queues",
            extra={
                "event": "sync.health.redis_error",
                "log_class": "integration",
                "server_mode": settings.server_mode,
                "error_type": type(exc).__name__,
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
        "publication_reconciliation": publication_reconciliation,
    }
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
            "publication_reconciliation_status": publication_reconciliation.get("status"),
            "publication_reconciliation_findings": publication_reconciliation.get("finding_counts"),
        },
    )
    return payload
