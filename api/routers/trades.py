# api/routers/trades.py
"""
API Router for Trade Management - MiniApp Integration
"""
import asyncio
import logging
import os
import hashlib
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional, Mapping
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.background import BackgroundTask

from core.db import get_db
from core.config import settings
from core.enums import NotificationLevel, NotificationCategory, SettlementType
from core.offer_settlement import settlement_type_value, trade_settlement_label
from core.utils import (
    check_user_limits, increment_user_counter, to_jalali_str,
    create_user_notification as _legacy_create_user_notification,
)
from core.services.accountant_chat_contract import AccountantChatIdentity, load_accountant_chat_identity_map
from core.services.accountant_relation_service import build_trade_notification_audience_user_ids
from core.services.market_transition_service import evaluate_current_market_schedule
from core.services.trade_history_export_service import (
    build_trade_history_date_range_label,
    build_trade_history_export_rows,
    generate_trade_history_excel_file,
    generate_trade_history_pdf_file,
)
from core.services.customer_relation_service import (
    apply_customer_commission,
    customer_management_name_for_user_id,
    get_active_customer_relation_for_customer,
)
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    validate_offer_trade_amount,
)
from core.services.block_service import is_trade_blocked_by_principals
from core.services.trade_contention_gate import (
    TradeContentionLease,
    trade_contention_lease_was_pre_gated,
    try_acquire_trade_contention_gate,
)
from core.services.trade_webapp_delivery_service import (
    deliver_webapp_trade_notification,
    repair_webapp_trade_delivery_for_trade,
)
from core.services.trade_telegram_delivery_service import repair_telegram_trade_delivery_for_trade
from core.services.offer_expiry_service import OfferExpiryReason
from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    apply_offer_request_decision,
    create_offer_request_ledger_entry,
    customer_relation_snapshot,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.services.user_account_status_service import is_user_trade_blocked
from core import telegram_gateway
from models.user import User, UserRole
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferType, OfferStatus
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity, CommodityAlias
from api.deps import EffectiveOwnerActor, get_current_user, get_effective_owner_actor_context
from core.server_routing import KNOWN_SERVERS, current_server, is_remote_home, normalize_server
from core.telegram_trade_callbacks import build_channel_trade_callback_data
from core.trade_forwarding import forward_trade_to_home_server, verify_internal_signature
from core.trading_observability import log_trading_event


logger = logging.getLogger(__name__)


MARKET_CLOSED_DETAIL = "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید."
ACCOUNTANT_MARKET_BLOCKED_DETAIL = "حسابدار دسترسی به بازار ندارد."
TRADE_UNAVAILABLE_DETAIL = "امکان انجام این معامله وجود ندارد."
TRADE_CONFLICT_DETAIL = "این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید."
TRADE_NUMBER_SEQUENCE_NAME = "trade_number_seq"
TRADE_IDEMPOTENCY_LOCK_NAMESPACE = 362_514
TRADE_OFFER_EXECUTION_LOCK_NAMESPACE = 362_515
TRADE_TRANSIENT_SQLSTATES = frozenset({"40P01", "40001"})
TRADE_TRANSIENT_RETRY_ATTEMPTS = 3
TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS = 0.05


router = APIRouter(
    tags=["Trades"],
)


def _ensure_accountant_market_access_allowed(context: EffectiveOwnerActor) -> None:
    if getattr(context, "is_accountant_context", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNTANT_MARKET_BLOCKED_DETAIL,
        )


class TradeExecutionPlanError(ValueError):
    pass


class TradeAtomicityError(ValueError):
    pass


class TradeIdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TradeExecutionNode:
    user_id: int
    user: object


@dataclass(frozen=True)
class TradeExecutionPlan:
    nodes: tuple[TradeExecutionNode, ...]

    @property
    def uses_customer_trade_chain(self) -> bool:
        return len(self.nodes) > 2


# --- Pydantic Schemas ---

class TradeCreate(BaseModel):
    """ایجاد معامله جدید"""
    offer_id: int = Field(..., gt=0)
    offer_public_id: Optional[str] = None
    quantity: int = Field(..., gt=0)
    idempotency_key: Optional[str] = None


async def _acquire_trade_contention_gate_dependency(trade_data: TradeCreate) -> TradeContentionLease:
    lease = await try_acquire_trade_contention_gate(
        offer_public_id=trade_data.offer_public_id,
        offer_id=trade_data.offer_id,
    )
    if not lease.acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=TRADE_CONFLICT_DETAIL)
    return lease


async def _release_trade_contention_lease(lease: object) -> None:
    release = getattr(lease, "release", None)
    if callable(release):
        await release()


class InternalTradeExecuteRequest(BaseModel):
    """درخواست داخلی اجرای معامله روی سرور مرجع آفر"""
    offer_id: int = Field(..., gt=0)
    offer_public_id: str = Field(..., min_length=1, max_length=40)
    quantity: int = Field(..., gt=0)
    responder_user_id: int = Field(..., gt=0)
    actor_user_id: Optional[int] = Field(None, gt=0)
    edge_received_at: datetime
    source_surface: str = OfferRequestSourceSurface.WEBAPP.value
    source_server: str
    idempotency_key: Optional[str] = None
    request_pre_gated: bool = False


class TradeResponse(BaseModel):
    """پاسخ معامله"""
    id: int
    trade_number: int
    offer_id: Optional[int]
    trade_type: str
    settlement_type: str
    commodity_id: int
    commodity_name: str
    quantity: int
    price: int
    status: str
    offer_user_id: Optional[int]
    offer_user_name: Optional[str]
    offer_user_profile_user_id: Optional[int] = None
    offer_user_profile_account_name: Optional[str] = None
    offer_user_resolved_from_accountant_id: Optional[int] = None
    offer_user_highlight_accountant_user_id: Optional[int] = None
    offer_user_highlight_accountant_relation_display_name: Optional[str] = None
    responder_user_id: Optional[int]
    responder_user_name: Optional[str]
    responder_user_profile_user_id: Optional[int] = None
    responder_user_profile_account_name: Optional[str] = None
    responder_user_resolved_from_accountant_id: Optional[int] = None
    responder_user_highlight_accountant_user_id: Optional[int] = None
    responder_user_highlight_accountant_relation_display_name: Optional[str] = None
    counterparty_user_id: Optional[int] = None
    counterparty_name: Optional[str] = None
    counterparty_profile_user_id: Optional[int] = None
    counterparty_profile_account_name: Optional[str] = None
    counterparty_highlight_accountant_user_id: Optional[int] = None
    counterparty_highlight_accountant_relation_display_name: Optional[str] = None
    customer_context_visible: bool = False
    customer_context_user_id: Optional[int] = None
    customer_context_management_name: Optional[str] = None
    customer_context_tier: Optional[str] = None
    trade_path_kind: Optional[str] = None
    trade_path_summary: Optional[str] = None
    offer_notes: Optional[str] = None
    created_at: str
    
    class Config:
        from_attributes = True


# --- Helper Functions ---

def _coerce_trade_user_id(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _build_trade_execution_node(user_id: object, user: object | None) -> TradeExecutionNode:
    normalized_user_id = _coerce_trade_user_id(user_id)
    if normalized_user_id is None or user is None:
        raise TradeExecutionPlanError(TRADE_UNAVAILABLE_DETAIL)
    return TradeExecutionNode(user_id=normalized_user_id, user=user)


def _append_trade_execution_node(
    nodes: list[TradeExecutionNode],
    *,
    user_id: object,
    user: object | None,
) -> None:
    normalized_user_id = _coerce_trade_user_id(user_id)
    if normalized_user_id is None:
        raise TradeExecutionPlanError(TRADE_UNAVAILABLE_DETAIL)
    if nodes and nodes[-1].user_id == normalized_user_id:
        return
    if user is None:
        raise TradeExecutionPlanError(TRADE_UNAVAILABLE_DETAIL)
    nodes.append(TradeExecutionNode(user_id=normalized_user_id, user=user))


def _build_trade_execution_plan(
    *,
    offer_user_id: object,
    offer_user: object | None,
    source_principal_user_id: object,
    source_principal_user: object | None,
    responder_principal_user_id: object,
    responder_principal_user: object | None,
    owner_user_id: object,
    owner_user: object | None,
) -> TradeExecutionPlan:
    nodes = [_build_trade_execution_node(offer_user_id, offer_user)]
    if source_principal_user_id != offer_user_id:
        _append_trade_execution_node(
            nodes,
            user_id=source_principal_user_id,
            user=source_principal_user,
        )
    _append_trade_execution_node(
        nodes,
        user_id=responder_principal_user_id,
        user=responder_principal_user,
    )
    _append_trade_execution_node(
        nodes,
        user_id=owner_user_id,
        user=owner_user,
    )
    return TradeExecutionPlan(nodes=tuple(nodes))


def _db_dialect_name(db: AsyncSession | object) -> str | None:
    get_bind = getattr(db, "get_bind", None)
    bind = None
    if callable(get_bind):
        try:
            bind = get_bind()
        except Exception:
            bind = None
    if bind is None:
        bind = getattr(db, "bind", None)
    dialect = getattr(bind, "dialect", None)
    dialect_name = getattr(dialect, "name", None)
    return str(dialect_name).lower() if dialect_name else None


def _stable_advisory_lock_id(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


async def _lock_trade_idempotency_key(db: AsyncSession, idempotency_key: str | None) -> bool:
    if not idempotency_key or _db_dialect_name(db) != "postgresql":
        return False
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                TRADE_IDEMPOTENCY_LOCK_NAMESPACE,
                _stable_advisory_lock_id(idempotency_key),
            )
        )
    )
    return True


async def _try_lock_trade_offer_execution(db: AsyncSession, offer_id: int, *, wait: bool = False) -> bool:
    if _db_dialect_name(db) != "postgresql":
        return True
    offer_lock_id = _stable_advisory_lock_id(f"offer:{int(offer_id)}")
    if wait:
        await db.execute(
            select(func.pg_advisory_xact_lock(TRADE_OFFER_EXECUTION_LOCK_NAMESPACE, offer_lock_id))
        )
        return True
    locked = await db.scalar(
        select(func.pg_try_advisory_xact_lock(TRADE_OFFER_EXECUTION_LOCK_NAMESPACE, offer_lock_id))
    )
    return bool(locked)


async def _allocate_next_trade_number(db: AsyncSession) -> int:
    if _db_dialect_name(db) == "postgresql":
        next_value = await db.scalar(text(f"SELECT nextval('{TRADE_NUMBER_SEQUENCE_NAME}')"))
        return int(next_value)

    max_trade_number = await db.scalar(select(func.max(Trade.trade_number)))
    return (max_trade_number or 9999) + 1


async def _allocate_trade_numbers(db: AsyncSession, count: int) -> list[int]:
    if count <= 0:
        return []
    if _db_dialect_name(db) == "postgresql":
        return [await _allocate_next_trade_number(db) for _ in range(count)]

    first_trade_number = await _allocate_next_trade_number(db)
    return list(range(first_trade_number, first_trade_number + count))


def _validate_idempotent_trade_replay(
    *,
    existing_trade: Trade | object,
    offer: Offer | object,
    owner_user: User | object,
    actor_user: User | object,
    trade_quantity: int,
    expected_price: int,
    uses_customer_trade_chain: bool,
) -> None:
    mismatches: list[str] = []

    if hasattr(existing_trade, "offer_id"):
        existing_offer_id = getattr(existing_trade, "offer_id", None)
        if not uses_customer_trade_chain and existing_offer_id != getattr(offer, "id", None):
            mismatches.append("offer_id")
        if uses_customer_trade_chain and existing_offer_id not in (None, getattr(offer, "id", None)):
            mismatches.append("chain_offer_id")

    if hasattr(existing_trade, "offer_user_id") and not uses_customer_trade_chain:
        if _coerce_trade_user_id(getattr(existing_trade, "offer_user_id", None)) != _coerce_trade_user_id(getattr(offer, "user_id", None)):
            mismatches.append("offer_user_id")
    if hasattr(existing_trade, "responder_user_id"):
        if _coerce_trade_user_id(getattr(existing_trade, "responder_user_id", None)) != _coerce_trade_user_id(getattr(owner_user, "id", None)):
            mismatches.append("responder_user_id")
    if hasattr(existing_trade, "actor_user_id"):
        existing_actor_user_id = _coerce_trade_user_id(getattr(existing_trade, "actor_user_id", None))
        expected_actor_user_id = _coerce_trade_user_id(getattr(actor_user, "id", None))
        if existing_actor_user_id is not None and existing_actor_user_id != expected_actor_user_id:
            mismatches.append("actor_user_id")
    if hasattr(existing_trade, "commodity_id") and getattr(existing_trade, "commodity_id", None) != getattr(offer, "commodity_id", None):
        mismatches.append("commodity_id")
    if hasattr(existing_trade, "quantity") and getattr(existing_trade, "quantity", None) != trade_quantity:
        mismatches.append("quantity")
    if hasattr(existing_trade, "price") and getattr(existing_trade, "price", None) != expected_price:
        mismatches.append("price")

    if mismatches:
        raise TradeIdempotencyConflictError("کلید تکرار این معامله با درخواست فعلی همخوانی ندارد.")


def _apply_offer_trade_mutation(offer: Offer | object, trade_quantity: int) -> bool:
    offer.remaining_quantity -= trade_quantity
    if offer.remaining_quantity < 0:
        raise TradeAtomicityError("موجودی این لفظ برای انجام معامله کافی نیست.")

    lot_sizes_modified = False
    if offer.remaining_quantity <= 0:
        if offer.lot_sizes is not None:
            offer.lot_sizes = None
            lot_sizes_modified = True
        offer.status = OfferStatus.COMPLETED
        return lot_sizes_modified

    if offer.lot_sizes:
        new_lot_sizes = list(offer.lot_sizes)
        if not getattr(offer, "is_wholesale", False) and trade_quantity not in new_lot_sizes:
            raise TradeAtomicityError("این لات دیگر موجود نیست.")
        if trade_quantity in new_lot_sizes:
            new_lot_sizes.remove(trade_quantity)
            offer.lot_sizes = new_lot_sizes if new_lot_sizes else None
            lot_sizes_modified = True
    return lot_sizes_modified


def _is_stale_trade_commit_error(exc: Exception) -> bool:
    return "StaleDataError" in str(type(exc).__name__) or "could not update" in str(exc).lower()


def _is_trade_unique_constraint_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "unique" in text or "duplicate key" in text or "uniqueviolation" in str(type(exc).__name__).lower()


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "orig", None) or current.__cause__ or current.__context__


def _extract_sqlstate(exc: BaseException) -> str | None:
    for item in _iter_exception_chain(exc):
        for attr in ("sqlstate", "pgcode"):
            value = getattr(item, attr, None)
            if value:
                return str(value)
        code_value = getattr(item, "code", None)
        if code_value and len(str(code_value)) == 5:
            return str(code_value)
        args = getattr(item, "args", ()) or ()
        for arg in args:
            for attr in ("sqlstate", "pgcode"):
                value = getattr(arg, attr, None)
                if value:
                    return str(value)
            code_value = getattr(arg, "code", None)
            if code_value and len(str(code_value)) == 5:
                return str(code_value)
    return None


def _is_retryable_trade_transient_error(exc: BaseException) -> bool:
    if not isinstance(exc, (DBAPIError, OperationalError)):
        return False
    sqlstate = _extract_sqlstate(exc)
    if sqlstate in TRADE_TRANSIENT_SQLSTATES:
        return True
    type_text = " ".join(type(item).__name__.lower() for item in _iter_exception_chain(exc))
    return "deadlockdetectederror" in type_text or "serializationerror" in type_text


async def _commit_trade_execution(db: AsyncSession) -> None:
    started_at = time_module.perf_counter()
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if _is_stale_trade_commit_error(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=TRADE_CONFLICT_DETAIL,
            ) from exc
        if _is_trade_unique_constraint_error(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="این معامله قبلاً ثبت شده یا شماره معامله تکراری است. لطفاً وضعیت معاملات را بروزرسانی کنید.",
            ) from exc
        raise
    finally:
        duration_ms = (time_module.perf_counter() - started_at) * 1000
        if duration_ms >= 500:
            log_trading_event(
                logger,
                "trade_commit.slow",
                level="warning",
                action="trade_commit",
                result="slow",
                total_duration_ms=round(duration_ms, 2),
            )


def _apply_trade_counter_increment(user: User | object, quantity: int) -> None:
    from core.user_counter_sync import increment_user_counters

    increment_user_counters(user, trades=1, commodities=quantity)


def _resolve_trade_participant_name(
    user: object | None,
    user_id: object,
    identity_map: Mapping[int, AccountantChatIdentity] | None,
) -> str | None:
    normalized_user_id = _coerce_trade_user_id(user_id)
    if normalized_user_id is not None and identity_map:
        identity = identity_map.get(normalized_user_id)
        if identity is not None:
            return identity.display_name
    return getattr(user, "account_name", None)


async def _load_trade_identity_map_for_user_ids(
    db: AsyncSession,
    user_ids: list[object] | tuple[object, ...],
) -> dict[int, AccountantChatIdentity]:
    participant_ids = sorted(
        {
            normalized_user_id
            for raw_user_id in user_ids
            for normalized_user_id in [_coerce_trade_user_id(raw_user_id)]
            if normalized_user_id is not None
        }
    )
    if not participant_ids:
        return {}
    return await load_accountant_chat_identity_map(db, participant_ids)


def _normalize_customer_tier_value(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = getattr(value, "value")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _normalize_trade_role_value(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = getattr(value, "value")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _is_super_admin_trade_history_viewer(context: EffectiveOwnerActor) -> bool:
    role_values = {
        _normalize_trade_role_value(getattr(getattr(context, "owner_user", None), "role", None)),
        _normalize_trade_role_value(getattr(getattr(context, "actor_user", None), "role", None)),
    }
    return UserRole.SUPER_ADMIN.value in role_values


def _viewer_can_access_customer_history_relation(
    *,
    relation: CustomerRelation | object | None,
    context: EffectiveOwnerActor,
) -> bool:
    if relation is None:
        return False
    relation_owner_user_id = _coerce_trade_user_id(getattr(relation, "owner_user_id", None))
    viewer_owner_user_id = _coerce_trade_user_id(getattr(getattr(context, "owner_user", None), "id", None))
    if relation_owner_user_id is None:
        return False
    return relation_owner_user_id == viewer_owner_user_id or _is_super_admin_trade_history_viewer(context)


async def _resolve_viewable_customer_history_relation(
    db: AsyncSession,
    *,
    customer_user_id: int,
    context: EffectiveOwnerActor,
) -> CustomerRelation | None:
    relation = await _get_customer_history_relation_for_customer(db, customer_user_id)
    if not _viewer_can_access_customer_history_relation(relation=relation, context=context):
        return None
    return relation


def _trade_customer_relation_sort_timestamp(relation: CustomerRelation | object) -> datetime:
    timestamp = (
        getattr(relation, "deleted_at", None)
        or getattr(relation, "updated_at", None)
        or getattr(relation, "expires_at", None)
        or getattr(relation, "activated_at", None)
        or getattr(relation, "created_at", None)
    )
    if timestamp is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _trade_customer_relation_sort_key(relation: CustomerRelation | object) -> tuple[int, datetime, datetime]:
    raw_status_value = getattr(relation, "status", None)
    status_value = getattr(raw_status_value, "value", raw_status_value)
    is_active = int(
        status_value == CustomerRelationStatus.ACTIVE.value
        and getattr(relation, "deleted_at", None) is None
    )
    created_at = getattr(relation, "created_at", None)
    if created_at is None:
        created_at = datetime.min.replace(tzinfo=timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return (is_active, _trade_customer_relation_sort_timestamp(relation), created_at)


def _normalize_trade_history_bound_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _customer_relation_history_bounds(relation: CustomerRelation | object | None) -> tuple[datetime | None, datetime | None]:
    if relation is None:
        return None, None

    start_at = _normalize_trade_history_bound_datetime(
        getattr(relation, "activated_at", None) or getattr(relation, "created_at", None)
    )
    raw_status_value = getattr(relation, "status", None)
    status_value = getattr(raw_status_value, "value", raw_status_value)
    end_at = _normalize_trade_history_bound_datetime(getattr(relation, "deleted_at", None))
    if end_at is None and status_value in {
        CustomerRelationStatus.EXPIRED.value,
        CustomerRelationStatus.REVOKED.value,
        CustomerRelationStatus.DELETED.value,
    }:
        end_at = _normalize_trade_history_bound_datetime(
            getattr(relation, "expires_at", None) or getattr(relation, "updated_at", None)
        )
    return start_at, end_at


async def _get_customer_history_relation_for_customer(
    db: AsyncSession,
    customer_user_id: object,
) -> CustomerRelation | None:
    normalized_customer_user_id = _coerce_trade_user_id(customer_user_id)
    if normalized_customer_user_id is None:
        return None

    active_relation = await get_active_customer_relation_for_customer(db, normalized_customer_user_id)
    if active_relation is not None:
        return active_relation

    result = await db.execute(
        select(CustomerRelation).where(
            CustomerRelation.customer_user_id == normalized_customer_user_id,
            CustomerRelation.status.in_(
                [
                    CustomerRelationStatus.ACTIVE,
                    CustomerRelationStatus.EXPIRED,
                    CustomerRelationStatus.REVOKED,
                    CustomerRelationStatus.DELETED,
                ]
            ),
        )
    )
    relations = list(result.scalars().all())
    if not relations:
        return None
    return sorted(relations, key=_trade_customer_relation_sort_key, reverse=True)[0]


def _normalize_history_commodity_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _validate_trade_history_date_range(from_date: date | None, to_date: date | None) -> None:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="بازه زمانی انتخاب‌شده معتبر نیست.",
        )


def _resolve_history_filter_arg(value):
    if getattr(value.__class__, "__module__", "") == "fastapi.params":
        return getattr(value, "default", None)
    return value


def _apply_trade_history_filters(
    query,
    *,
    from_date: date | None,
    to_date: date | None,
    commodity_id: int | None,
    commodity_query: str | None,
):
    from_date = _resolve_history_filter_arg(from_date)
    to_date = _resolve_history_filter_arg(to_date)
    commodity_id = _resolve_history_filter_arg(commodity_id)
    commodity_query = _resolve_history_filter_arg(commodity_query)

    _validate_trade_history_date_range(from_date, to_date)

    if from_date is not None:
        query = query.where(Trade.created_at >= datetime.combine(from_date, time.min))
    if to_date is not None:
        query = query.where(Trade.created_at < datetime.combine(to_date + timedelta(days=1), time.min))
    if commodity_id is not None:
        query = query.where(Trade.commodity_id == commodity_id)

    normalized_commodity_query = _normalize_history_commodity_query(commodity_query)
    if normalized_commodity_query:
        pattern = f"%{normalized_commodity_query}%"
        query = query.where(
            Trade.commodity.has(
                or_(
                    Commodity.name.ilike(pattern),
                    Commodity.aliases.any(CommodityAlias.alias.ilike(pattern)),
                )
            )
        )

    return query


def _build_trade_history_select():
    return select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity),
        selectinload(Trade.offer),
    )


def _build_my_trades_query(
    owner_user_id: int,
    *,
    from_date: date | None,
    to_date: date | None,
    commodity_id: int | None,
    commodity_query: str | None,
):
    query = _build_trade_history_select().where(
        or_(
            Trade.offer_user_id == owner_user_id,
            Trade.responder_user_id == owner_user_id,
        )
    )
    return _apply_trade_history_filters(
        query,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    )


async def _build_trades_with_user_query(
    db: AsyncSession,
    *,
    other_user_id: int,
    context: EffectiveOwnerActor,
    from_date: date | None,
    to_date: date | None,
    commodity_id: int | None,
    commodity_query: str | None,
):
    owner_user = context.owner_user
    target_customer_relation = await _resolve_viewable_customer_history_relation(
        db,
        customer_user_id=other_user_id,
        context=context,
    )

    query = _build_trade_history_select()
    if target_customer_relation is not None or _is_super_admin_trade_history_viewer(context):
        query = query.where(
            or_(
                Trade.offer_user_id == other_user_id,
                Trade.responder_user_id == other_user_id,
            )
        )
        if target_customer_relation is not None and not _is_super_admin_trade_history_viewer(context):
            relation_start_at, relation_end_at = _customer_relation_history_bounds(target_customer_relation)
            if relation_start_at is not None:
                query = query.where(Trade.created_at >= relation_start_at)
            if relation_end_at is not None:
                query = query.where(Trade.created_at <= relation_end_at)
    else:
        query = query.where(
            or_(
                and_(Trade.offer_user_id == owner_user.id, Trade.responder_user_id == other_user_id),
                and_(Trade.offer_user_id == other_user_id, Trade.responder_user_id == owner_user.id),
            )
        )

    query = _apply_trade_history_filters(
        query,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    )
    return query, target_customer_relation


def _build_trade_history_export_subject_name(*, current_user: object, target_user: object | None) -> str:
    if target_user is not None:
        return getattr(target_user, "account_name", None) or "history"
    return getattr(current_user, "account_name", None) or "history"


def _build_trade_history_download_name(subject_name: str, extension: str) -> str:
    safe_subject = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in subject_name.strip()) or "history"
    return f"trade_history_{safe_subject}.{extension}"


def _build_trade_history_file_response(*, path: str, media_type: str, filename: str):
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
        background=BackgroundTask(os.remove, path),
    )


async def _viewer_can_access_trade_history_row(
    db: AsyncSession,
    *,
    trade: Trade,
    context: EffectiveOwnerActor,
) -> bool:
    viewer_owner_user_id = _coerce_trade_user_id(getattr(getattr(context, "owner_user", None), "id", None))
    participant_user_ids = {
        normalized_user_id
        for raw_user_id in (getattr(trade, "offer_user_id", None), getattr(trade, "responder_user_id", None))
        for normalized_user_id in [_coerce_trade_user_id(raw_user_id)]
        if normalized_user_id is not None
    }
    if viewer_owner_user_id is not None and viewer_owner_user_id in participant_user_ids:
        return True

    for participant_user_id in participant_user_ids:
        relation = await _get_customer_history_relation_for_customer(db, participant_user_id)
        if _viewer_can_access_customer_history_relation(relation=relation, context=context):
            return True

    return False


async def _load_trade_customer_relation_map_for_user_ids(
    db: AsyncSession,
    user_ids: list[object] | tuple[object, ...],
    *,
    include_inactive_historical: bool = False,
) -> dict[int, CustomerRelation]:
    participant_ids = sorted(
        {
            normalized_user_id
            for raw_user_id in user_ids
            for normalized_user_id in [_coerce_trade_user_id(raw_user_id)]
            if normalized_user_id is not None
        }
    )
    if not participant_ids:
        return {}

    if include_inactive_historical:
        result = await db.execute(
            select(CustomerRelation).where(
                CustomerRelation.customer_user_id.in_(participant_ids),
                CustomerRelation.status.in_(
                    [
                        CustomerRelationStatus.ACTIVE,
                        CustomerRelationStatus.EXPIRED,
                        CustomerRelationStatus.REVOKED,
                        CustomerRelationStatus.DELETED,
                    ]
                ),
            )
        )
    else:
        result = await db.execute(
            select(CustomerRelation).where(
                CustomerRelation.customer_user_id.in_(participant_ids),
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                CustomerRelation.deleted_at.is_(None),
            )
        )
    relations = result.scalars().all()
    if not include_inactive_historical:
        return {
            relation.customer_user_id: relation
            for relation in relations
            if _coerce_trade_user_id(getattr(relation, "customer_user_id", None)) is not None
        }

    relation_map: dict[int, CustomerRelation] = {}
    for relation in sorted(relations, key=_trade_customer_relation_sort_key, reverse=True):
        customer_user_id = _coerce_trade_user_id(getattr(relation, "customer_user_id", None))
        if customer_user_id is None or customer_user_id in relation_map:
            continue
        relation_map[customer_user_id] = relation
    return relation_map


def _build_trade_path_payload(
    *,
    offer_user_id: object,
    responder_user_id: object,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> dict[str, str | None]:
    payload: dict[str, str | None] = {
        "trade_path_kind": None,
        "trade_path_summary": None,
    }
    if not customer_relation_map:
        return payload

    normalized_offer_user_id = _coerce_trade_user_id(offer_user_id)
    normalized_responder_user_id = _coerce_trade_user_id(responder_user_id)
    if normalized_offer_user_id is None or normalized_responder_user_id is None:
        return payload

    relation = customer_relation_map.get(normalized_offer_user_id)
    if relation is None or _coerce_trade_user_id(getattr(relation, "owner_user_id", None)) != normalized_responder_user_id:
        relation = customer_relation_map.get(normalized_responder_user_id)
        if relation is None or _coerce_trade_user_id(getattr(relation, "owner_user_id", None)) != normalized_offer_user_id:
            return payload

    customer_tier = _normalize_customer_tier_value(getattr(relation, "customer_tier", None))
    if customer_tier == CustomerTier.TIER_2.value:
        payload["trade_path_kind"] = "owner_customer_tier2"
        payload["trade_path_summary"] = "مالک ↔ مشتری سطح ۲"
        return payload
    if customer_tier == CustomerTier.TIER_1.value:
        payload["trade_path_kind"] = "owner_customer_tier1"
        payload["trade_path_summary"] = "مالک ↔ مشتری سطح ۱"
        return payload
    return payload


def _build_trade_participant_payload(
    field_prefix: str,
    *,
    user: object | None,
    user_id: object,
    identity_map: Mapping[int, AccountantChatIdentity] | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None = None,
) -> dict[str, object | None]:
    normalized_user_id = _coerce_trade_user_id(user_id)
    fallback_name = getattr(user, "account_name", None)
    customer_display_name = customer_management_name_for_user_id(normalized_user_id, customer_relation_map)

    payload: dict[str, object | None] = {
        f"{field_prefix}_id": normalized_user_id,
        f"{field_prefix}_name": customer_display_name or fallback_name,
        f"{field_prefix}_profile_user_id": normalized_user_id,
        f"{field_prefix}_profile_account_name": fallback_name,
        f"{field_prefix}_resolved_from_accountant_id": None,
        f"{field_prefix}_highlight_accountant_user_id": None,
        f"{field_prefix}_highlight_accountant_relation_display_name": None,
    }
    if normalized_user_id is None or not identity_map:
        return payload

    identity = identity_map.get(normalized_user_id)
    if identity is None:
        return payload

    payload[f"{field_prefix}_name"] = (
        customer_display_name
        or getattr(identity, "display_name", None)
        or fallback_name
    )
    payload[f"{field_prefix}_profile_user_id"] = (
        _coerce_trade_user_id(getattr(identity, "profile_user_id", None))
        or normalized_user_id
    )
    payload[f"{field_prefix}_profile_account_name"] = (
        getattr(identity, "profile_account_name", None)
        or fallback_name
    )
    payload[f"{field_prefix}_resolved_from_accountant_id"] = _coerce_trade_user_id(
        getattr(identity, "resolved_from_accountant_id", None)
    )
    payload[f"{field_prefix}_highlight_accountant_user_id"] = _coerce_trade_user_id(
        getattr(identity, "highlight_accountant_user_id", None)
    )
    payload[f"{field_prefix}_highlight_accountant_relation_display_name"] = getattr(
        identity,
        "highlight_accountant_relation_display_name",
        None,
    )
    return payload


async def _load_trade_identity_map(
    db: AsyncSession,
    trades: list[Trade],
) -> dict[int, AccountantChatIdentity]:
    return await _load_trade_identity_map_for_user_ids(
        db,
        [
            raw_user_id
            for trade in trades
            for raw_user_id in (getattr(trade, "offer_user_id", None), getattr(trade, "responder_user_id", None))
        ],
    )


def _build_trade_created_event_payload(
    *,
    trade_id: int | None,
    trade_number: int,
    offer_id: int | None,
    commodity_id: int | None,
    quantity: int,
    price: int,
    commodity_name: str | None,
    trade_type: str,
    status: str | None,
    created_at: str | None,
    offer_user: object | None,
    offer_user_id: object,
    responder_user: object | None,
    responder_user_id: object,
    actor_user_id: object | None = None,
    identity_map: Mapping[int, AccountantChatIdentity] | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None = None,
    viewer_context: EffectiveOwnerActor | None = None,
    history_target_user_id: int | None = None,
    audience_user_ids: list[int] | tuple[int, ...] | None = None,
    recipient_specific: bool = False,
    settlement_type: str = SettlementType.CASH.value,
) -> dict[str, object | None]:
    trade_like = SimpleNamespace(
        id=trade_id,
        trade_number=trade_number,
        offer_id=offer_id,
        trade_type=SimpleNamespace(value=trade_type),
        settlement_type=SimpleNamespace(value=settlement_type),
        commodity_id=commodity_id,
        commodity=SimpleNamespace(name=commodity_name) if commodity_name else None,
        quantity=quantity,
        price=price,
        status=SimpleNamespace(value=status) if status else None,
        offer_user_id=_coerce_trade_user_id(offer_user_id),
        offer_user=offer_user,
        responder_user_id=_coerce_trade_user_id(responder_user_id),
        responder_user=responder_user,
        actor_user_id=_coerce_trade_user_id(actor_user_id),
        created_at=None,
    )
    offer_user_payload = _build_trade_participant_payload(
        "offer_user",
        user=offer_user,
        user_id=offer_user_id,
        identity_map=identity_map,
        customer_relation_map=customer_relation_map,
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=responder_user,
        user_id=responder_user_id,
        identity_map=identity_map,
        customer_relation_map=customer_relation_map,
    )
    payload: dict[str, object | None] = {
        "id": trade_id,
        "trade_number": trade_number,
        "offer_id": offer_id,
        "trade_type": trade_type,
        "settlement_type": settlement_type_value(settlement_type),
        "commodity_id": commodity_id,
        "commodity_name": commodity_name or "نامشخص",
        "quantity": quantity,
        "price": price,
        "status": status,
        "created_at": created_at or "",
        **offer_user_payload,
        **responder_user_payload,
        **_build_trade_counterparty_projection_payload(
            trade=trade_like,
            offer_user_payload=offer_user_payload,
            responder_user_payload=responder_user_payload,
            history_target_user_id=history_target_user_id,
            customer_relation_map=customer_relation_map,
        ),
        **_build_trade_customer_context_payload(
            trade=trade_like,
            viewer_context=viewer_context,
            history_target_user_id=history_target_user_id,
            customer_relation_map=customer_relation_map,
        ),
        **_build_trade_path_payload(
            offer_user_id=offer_user_id,
            responder_user_id=responder_user_id,
            customer_relation_map=customer_relation_map,
        ),
    }
    if audience_user_ids is not None:
        payload["audience_user_ids"] = sorted(
            {
                normalized_user_id
                for raw_user_id in audience_user_ids
                for normalized_user_id in [_coerce_trade_user_id(raw_user_id)]
                if normalized_user_id is not None
            }
        )
    if recipient_specific:
        payload["recipient_specific"] = True
    return payload


def _build_trade_history_viewer_context(user: object | None) -> EffectiveOwnerActor | None:
    if _coerce_trade_user_id(getattr(user, "id", None)) is None:
        return None
    return EffectiveOwnerActor(
        owner_user=user,
        actor_user=user,
        relation=None,
        is_accountant_context=False,
    )


async def _publish_trade_created_realtime(
    *,
    trade: Trade | object,
    fallback_trade_id: int | None,
    fallback_trade_number: int,
    fallback_offer_id: int | None,
    fallback_commodity_id: int | None,
    fallback_quantity: int,
    fallback_price: int,
    fallback_status: str | None,
    fallback_created_at: str | None,
    fallback_offer_user_id: int | None,
    fallback_responder_user_id: int | None,
    commodity_name: str | None,
    fallback_trade_type: str,
    offer_user: object | None,
    responder_user: object | None,
    identity_map: Mapping[int, AccountantChatIdentity] | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
    responder_audience_user_ids: list[int] | tuple[int, ...] | None,
    offer_owner_audience_user_ids: list[int] | tuple[int, ...] | None,
) -> None:
    from .realtime import publish_user_event

    trade_type_value = getattr(getattr(trade, "trade_type", None), "value", None) or fallback_trade_type
    common_payload = {
        "trade_id": getattr(trade, "id", None) or fallback_trade_id,
        "trade_number": getattr(trade, "trade_number", None) or fallback_trade_number,
        "offer_id": getattr(trade, "offer_id", None) or fallback_offer_id,
        "commodity_id": getattr(trade, "commodity_id", None) or fallback_commodity_id,
        "quantity": getattr(trade, "quantity", None) or fallback_quantity,
        "price": getattr(trade, "price", None) or fallback_price,
        "commodity_name": commodity_name,
        "trade_type": trade_type_value,
        "settlement_type": settlement_type_value(getattr(trade, "settlement_type", None)),
        "status": getattr(getattr(trade, "status", None), "value", None) or fallback_status,
        "created_at": to_jalali_str(getattr(trade, "created_at", None)) or fallback_created_at or "",
        "offer_user": offer_user,
        "offer_user_id": getattr(trade, "offer_user_id", None) or fallback_offer_user_id,
        "responder_user": responder_user,
        "responder_user_id": getattr(trade, "responder_user_id", None) or fallback_responder_user_id,
        "actor_user_id": getattr(trade, "actor_user_id", None),
        "identity_map": identity_map,
        "customer_relation_map": customer_relation_map,
    }

    realtime_audiences = [
        (responder_audience_user_ids or [], responder_user),
        (offer_owner_audience_user_ids or [], offer_user),
    ]
    for audience_user_ids, principal_user in realtime_audiences:
        principal_user_id = _coerce_trade_user_id(getattr(principal_user, "id", None))
        if not audience_user_ids or principal_user_id is None:
            continue

        recipient_payload = _build_trade_created_event_payload(
            **common_payload,
            viewer_context=_build_trade_history_viewer_context(principal_user),
            history_target_user_id=principal_user_id,
            recipient_specific=True,
        )
        for audience_user_id in audience_user_ids:
            try:
                await publish_user_event(audience_user_id, "trade:created", recipient_payload)
            except Exception as exc:
                log_trading_event(
                    logger,
                    "trade_realtime_publish_failed",
                    level="warning",
                    action="trading_side_effect",
                    result="failure",
                    side_effect="realtime_publish",
                    offer_id=common_payload.get("offer_id"),
                    trade_id=common_payload.get("trade_id"),
                    trade_number=common_payload.get("trade_number"),
                    error_class=type(exc).__name__,
                )

def _build_trade_profile_route_from_payload(
    field_prefix: str,
    participant_payload: Mapping[str, object | None],
) -> str | None:
    profile_user_id = _coerce_trade_user_id(participant_payload.get(f"{field_prefix}_profile_user_id"))
    if profile_user_id is None:
        return None

    query_params: dict[str, str] = {}
    profile_account_name = participant_payload.get(f"{field_prefix}_profile_account_name")
    if isinstance(profile_account_name, str) and profile_account_name.strip():
        query_params["account_name"] = profile_account_name

    highlight_accountant_user_id = _coerce_trade_user_id(
        participant_payload.get(f"{field_prefix}_highlight_accountant_user_id")
    )
    if highlight_accountant_user_id is not None:
        query_params["highlight_accountant_user_id"] = str(highlight_accountant_user_id)

    highlight_relation_display_name = participant_payload.get(
        f"{field_prefix}_highlight_accountant_relation_display_name"
    )
    if isinstance(highlight_relation_display_name, str) and highlight_relation_display_name.strip():
        query_params["highlight_accountant_relation_display_name"] = highlight_relation_display_name

    query_string = urlencode(query_params)
    if query_string:
        return f"/users/{profile_user_id}?{query_string}"
    return f"/users/{profile_user_id}"


def _build_trade_notification_extra_payload(
    field_prefix: str,
    participant_payload: Mapping[str, object | None],
    *,
    trade_number: int,
    settlement_type: object = SettlementType.CASH,
) -> dict[str, object | None]:
    return {
        "route": _build_trade_profile_route_from_payload(field_prefix, participant_payload),
        "trade_number": trade_number,
        "settlement_type": settlement_type_value(settlement_type),
        "counterparty_profile_user_id": _coerce_trade_user_id(
            participant_payload.get(f"{field_prefix}_profile_user_id")
        ),
        "counterparty_profile_account_name": participant_payload.get(f"{field_prefix}_profile_account_name"),
        "highlight_accountant_user_id": _coerce_trade_user_id(
            participant_payload.get(f"{field_prefix}_highlight_accountant_user_id")
        ),
        "highlight_accountant_relation_display_name": participant_payload.get(
            f"{field_prefix}_highlight_accountant_relation_display_name"
        ),
    }


def _recipient_is_customer(
    audience_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> bool:
    if audience_user_id is None or not customer_relation_map:
        return False
    relation = customer_relation_map.get(audience_user_id)
    return _normalize_customer_tier_value(getattr(relation, "customer_tier", None)) in {
        CustomerTier.TIER_1.value,
        CustomerTier.TIER_2.value,
    }


def _recipient_customer_owner_user_id(
    audience_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> int | None:
    if audience_user_id is None or not customer_relation_map:
        return None
    relation = customer_relation_map.get(audience_user_id)
    if relation is None:
        return None
    if not _recipient_is_customer(audience_user_id, customer_relation_map):
        return None
    return _coerce_trade_user_id(getattr(relation, "owner_user_id", None))


def _should_hide_counterparty_for_recipient(
    *,
    audience_user_id: int | None,
    counterparty_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> bool:
    owner_user_id = _recipient_customer_owner_user_id(audience_user_id, customer_relation_map)
    if owner_user_id is None:
        return False
    normalized_counterparty_user_id = _coerce_trade_user_id(counterparty_user_id)
    if normalized_counterparty_user_id is None:
        return True
    return normalized_counterparty_user_id != owner_user_id


def _normalize_offer_notes_for_notification(offer_notes: str | None) -> str | None:
    normalized = " ".join(str(offer_notes or "").split())
    return normalized or None


def _build_trade_notification_message(
    *,
    trade_emoji: str,
    trade_type_label: str,
    trade_price: int,
    trade_quantity: int,
    commodity_name: str,
    trade_number: int,
    trade_datetime: str,
    counterparty_name: str | None,
    audience_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
    counterparty_user_id: int | None = None,
    trade_path_summary: str | None = None,
    offer_notes: str | None = None,
    settlement_type: object = SettlementType.CASH,
) -> str:
    lines = [
        f"{trade_emoji} {trade_type_label}",
        f"💰 فی: {trade_price:,}",
        f"📦 تعداد: {trade_quantity}",
        f"🏷️ کالا: {commodity_name}",
        f"🗓️ تسویه: {trade_settlement_label(settlement_type)}",
    ]
    if counterparty_name and not _should_hide_counterparty_for_recipient(
        audience_user_id=audience_user_id,
        counterparty_user_id=counterparty_user_id,
        customer_relation_map=customer_relation_map,
    ):
        lines.append(f"👤 طرف معامله: {counterparty_name}")
    lines.append(f"🔢 شماره معامله: {trade_number}")
    lines.append(f"🕐 زمان معامله: {trade_datetime}")
    if trade_path_summary:
        lines.append(f"🧭 مسیر: {trade_path_summary}")
    normalized_notes = _normalize_offer_notes_for_notification(offer_notes)
    if normalized_notes:
        lines.append(f"📝 توضیحات: {normalized_notes}")
    return "\n".join(lines)


def _build_trade_message_bundle(
    *,
    responder_trade_emoji: str,
    responder_trade_label: str,
    offer_trade_emoji: str,
    offer_trade_label: str,
    trade_price: int,
    trade_quantity: int,
    commodity_name: str,
    trade_number: int,
    trade_datetime: str,
    offer_user_name: str,
    responder_user_name: str,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
    trade_path_summary: str | None = None,
    offer_notes: str | None = None,
    settlement_type: object = SettlementType.CASH,
) -> tuple[str, str, str, str]:
    trade_path_line = f"\n🧭 مسیر: {trade_path_summary}" if trade_path_summary else ""
    normalized_offer_notes = _normalize_offer_notes_for_notification(offer_notes)
    offer_notes_line = f"\n📝 توضیحات: {normalized_offer_notes}" if normalized_offer_notes else ""
    settlement_line = f"🗓️ تسویه: {trade_settlement_label(settlement_type)}\n"
    responder_msg = (
        f"{responder_trade_emoji} <b>{responder_trade_label}</b>\n\n"
        f"💰 فی: {trade_price:,}\n"
        f"📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {commodity_name}\n"
        f"{settlement_line}"
        f"👤 طرف معامله: {offer_user_name}\n"
        f"🔢 شماره معامله: {trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
        f"{trade_path_line}"
        f"{offer_notes_line}"
    )
    offer_owner_msg = (
        f"{offer_trade_emoji} <b>{offer_trade_label}</b>\n\n"
        f"💰 فی: {trade_price:,}\n"
        f"📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {commodity_name}\n"
        f"{settlement_line}"
        f"👤 طرف معامله: {responder_user_name}\n"
        f"🔢 شماره معامله: {trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
        f"{trade_path_line}"
        f"{offer_notes_line}"
    )
    notif_msg_responder = _build_trade_notification_message(
        trade_emoji=responder_trade_emoji,
        trade_type_label=responder_trade_label,
        trade_price=trade_price,
        trade_quantity=trade_quantity,
        commodity_name=commodity_name,
        trade_number=trade_number,
        trade_datetime=trade_datetime,
        counterparty_name=offer_user_name,
        audience_user_id=None,
        customer_relation_map=customer_relation_map,
        trade_path_summary=trade_path_summary,
        offer_notes=offer_notes,
        settlement_type=settlement_type,
    )
    notif_msg_owner = _build_trade_notification_message(
        trade_emoji=offer_trade_emoji,
        trade_type_label=offer_trade_label,
        trade_price=trade_price,
        trade_quantity=trade_quantity,
        commodity_name=commodity_name,
        trade_number=trade_number,
        trade_datetime=trade_datetime,
        counterparty_name=responder_user_name,
        audience_user_id=None,
        customer_relation_map=customer_relation_map,
        trade_path_summary=trade_path_summary,
        offer_notes=offer_notes,
        settlement_type=settlement_type,
    )
    return responder_msg, offer_owner_msg, notif_msg_responder, notif_msg_owner


def _resolve_trade_history_subject_prefix(
    *,
    trade: Trade | object,
    history_target_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> str | None:
    target_user_id = _coerce_trade_user_id(history_target_user_id)
    if target_user_id is None:
        return None

    offer_user_id = _coerce_trade_user_id(getattr(trade, "offer_user_id", None))
    responder_user_id = _coerce_trade_user_id(getattr(trade, "responder_user_id", None))
    if offer_user_id == target_user_id:
        return "offer_user"
    if responder_user_id == target_user_id:
        return "responder_user"
    return None


def _build_trade_counterparty_projection_payload(
    *,
    trade: Trade | object,
    offer_user_payload: Mapping[str, object | None],
    responder_user_payload: Mapping[str, object | None],
    history_target_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> dict[str, object | None]:
    payload: dict[str, object | None] = {
        "counterparty_user_id": None,
        "counterparty_name": None,
        "counterparty_profile_user_id": None,
        "counterparty_profile_account_name": None,
        "counterparty_highlight_accountant_user_id": None,
        "counterparty_highlight_accountant_relation_display_name": None,
    }

    subject_prefix = _resolve_trade_history_subject_prefix(
        trade=trade,
        history_target_user_id=history_target_user_id,
        customer_relation_map=customer_relation_map,
    )
    if subject_prefix is None:
        return payload

    counterparty_prefix = responder_user_payload if subject_prefix == "offer_user" else offer_user_payload
    payload["counterparty_user_id"] = _coerce_trade_user_id(
        counterparty_prefix.get("responder_user_id" if subject_prefix == "offer_user" else "offer_user_id")
    )
    payload["counterparty_name"] = counterparty_prefix.get(
        "responder_user_name" if subject_prefix == "offer_user" else "offer_user_name"
    )
    payload["counterparty_profile_user_id"] = _coerce_trade_user_id(
        counterparty_prefix.get(
            "responder_user_profile_user_id" if subject_prefix == "offer_user" else "offer_user_profile_user_id"
        )
    )
    payload["counterparty_profile_account_name"] = counterparty_prefix.get(
        "responder_user_profile_account_name" if subject_prefix == "offer_user" else "offer_user_profile_account_name"
    )
    payload["counterparty_highlight_accountant_user_id"] = _coerce_trade_user_id(
        counterparty_prefix.get(
            "responder_user_highlight_accountant_user_id"
            if subject_prefix == "offer_user"
            else "offer_user_highlight_accountant_user_id"
        )
    )
    payload["counterparty_highlight_accountant_relation_display_name"] = counterparty_prefix.get(
        "responder_user_highlight_accountant_relation_display_name"
        if subject_prefix == "offer_user"
        else "offer_user_highlight_accountant_relation_display_name"
    )
    return payload


def _build_trade_customer_context_payload(
    *,
    trade: Trade | object,
    viewer_context: EffectiveOwnerActor | None,
    history_target_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> dict[str, object | None]:
    payload: dict[str, object | None] = {
        "customer_context_visible": False,
        "customer_context_user_id": None,
        "customer_context_management_name": None,
        "customer_context_tier": None,
    }
    if viewer_context is None or not customer_relation_map:
        return payload

    actor_user_id = _coerce_trade_user_id(getattr(trade, "actor_user_id", None))
    if actor_user_id is None:
        return payload

    if actor_user_id == _coerce_trade_user_id(history_target_user_id):
        return payload

    actor_relation = customer_relation_map.get(actor_user_id)
    if not _viewer_can_access_customer_history_relation(relation=actor_relation, context=viewer_context):
        return payload

    payload["customer_context_visible"] = True
    payload["customer_context_user_id"] = actor_user_id
    payload["customer_context_management_name"] = getattr(actor_relation, "management_name", None)
    payload["customer_context_tier"] = _normalize_customer_tier_value(
        getattr(actor_relation, "customer_tier", None)
    )
    return payload


def trade_to_response(
    trade: Trade,
    *,
    identity_map: Mapping[int, AccountantChatIdentity] | None = None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None = None,
    viewer_context: EffectiveOwnerActor | None = None,
    history_target_user_id: int | None = None,
    offer_notes: str | None = None,
) -> TradeResponse:
    """تبدیل مدل Trade به پاسخ API"""
    offer_user_payload = _build_trade_participant_payload(
        "offer_user",
        user=trade.offer_user,
        user_id=trade.offer_user_id,
        identity_map=identity_map,
        customer_relation_map=customer_relation_map,
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=trade.responder_user,
        user_id=trade.responder_user_id,
        identity_map=identity_map,
        customer_relation_map=customer_relation_map,
    )
    trade_path_payload = _build_trade_path_payload(
        offer_user_id=trade.offer_user_id,
        responder_user_id=trade.responder_user_id,
        customer_relation_map=customer_relation_map,
    )
    counterparty_payload = _build_trade_counterparty_projection_payload(
        trade=trade,
        offer_user_payload=offer_user_payload,
        responder_user_payload=responder_user_payload,
        history_target_user_id=history_target_user_id,
        customer_relation_map=customer_relation_map,
    )
    customer_context_payload = _build_trade_customer_context_payload(
        trade=trade,
        viewer_context=viewer_context,
        history_target_user_id=history_target_user_id,
        customer_relation_map=customer_relation_map,
    )
    loaded_offer = getattr(trade, "__dict__", {}).get("offer")
    resolved_offer_notes = offer_notes if offer_notes is not None else getattr(loaded_offer, "notes", None)

    return TradeResponse(
        id=trade.id,
        trade_number=trade.trade_number,
        offer_id=trade.offer_id,
        trade_type=trade.trade_type.value,
        settlement_type=settlement_type_value(getattr(trade, "settlement_type", None)),
        commodity_id=trade.commodity_id,
        commodity_name=trade.commodity.name if trade.commodity else "نامشخص",
        quantity=trade.quantity,
        price=trade.price,
        status=trade.status.value,
        **offer_user_payload,
        **responder_user_payload,
        **counterparty_payload,
        **customer_context_payload,
        **trade_path_payload,
        offer_notes=resolved_offer_notes,
        created_at=to_jalali_str(trade.created_at) or ""
    )


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """ارسال پیام به تلگرام"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    try:
        result = await telegram_gateway.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            bot_token=bot_token,
            idempotency_key=f"trade-message:{chat_id}",
        )
        if not result.ok:
            log_trading_event(
                logger,
                "trade_telegram_message_failed",
                level="error",
                action="trading_side_effect",
                result="failure",
                side_effect="telegram_message",
                status_code=result.status_code,
                error_class=result.error,
            )
        return result.ok
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_telegram_message_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            error_class=type(exc).__name__,
        )
        return False


async def update_channel_buttons(offer: Offer) -> bool:
    """آپدیت دکمه‌های پست کانال"""
    if current_server() != "foreign":
        return False

    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id or not offer.channel_message_id:
        return False
    
    if offer.remaining_quantity <= 0 or offer.status != OfferStatus.ACTIVE:
        return await apply_offer_channel_state(offer, reason="trade_channel_buttons")
    else:
        # ساخت دکمه‌های جدید
        if offer.is_wholesale or not offer.lot_sizes:
            buttons = [[{
                "text": f"{offer.remaining_quantity} عدد",
                "callback_data": build_channel_trade_callback_data(
                    offer_id=offer.id,
                    offer_public_id=getattr(offer, "offer_public_id", None),
                    amount=offer.remaining_quantity,
                ),
            }]]
        else:
            valid_lots = get_available_trade_amounts(
                quantity=offer.quantity,
                remaining_quantity=offer.remaining_quantity,
                is_wholesale=False,
                lot_sizes=offer.lot_sizes,
            )
            if not valid_lots:
                buttons = None
            else:
                buttons = [[{
                    "text": f"{a} عدد",
                    "callback_data": build_channel_trade_callback_data(
                        offer_id=offer.id,
                        offer_public_id=getattr(offer, "offer_public_id", None),
                        amount=a,
                    ),
                } for a in valid_lots]]
        
        if buttons is not None:
            reply_markup = {"inline_keyboard": buttons}
        else:
            reply_markup = None
    
    try:
        result = await telegram_gateway.edit_message_reply_markup(
            channel_id,
            offer.channel_message_id,
            reply_markup=reply_markup,
            bot_token=bot_token,
            idempotency_key=f"trade-channel-buttons:{getattr(offer, 'id', '')}",
        )
        if not result.ok:
            log_trading_event(
                logger,
                "trade_channel_buttons_update_failed",
                level="error",
                action="trading_side_effect",
                result="failure",
                side_effect="telegram_channel_buttons",
                offer_id=getattr(offer, "id", None),
                status_code=result.status_code,
                error_class=result.error,
            )
        return result.ok
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_channel_buttons_update_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_channel_buttons",
            offer_id=getattr(offer, "id", None),
            error_class=type(exc).__name__,
        )
        return False


# ===== Sync Wrappers for BackgroundTasks =====
# استفاده از gateway sync client به جای asyncio.run برای جلوگیری از مشکلات event loop

def send_telegram_message_sync(chat_id: int, text: str) -> bool:
    """نسخه sync برای استفاده در BackgroundTasks"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    try:
        result = telegram_gateway.send_message_sync(
            chat_id,
            text,
            parse_mode="HTML",
            bot_token=bot_token,
            idempotency_key=f"trade-message-sync:{chat_id}",
        )
        if not result.ok:
            log_trading_event(
                logger,
                "trade_telegram_message_failed",
                level="error",
                action="trading_side_effect",
                result="failure",
                side_effect="telegram_message",
                status_code=result.status_code,
                error_class=result.error,
            )
        return result.ok
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_telegram_message_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            error_class=type(exc).__name__,
        )
        return False


def _queue_trade_telegram_message(background_tasks: BackgroundTasks, chat_id: int | None, text: str) -> bool:
    if current_server() != "foreign" or not chat_id:
        return False
    background_tasks.add_task(send_telegram_message_sync, chat_id, text)
    return True


async def create_user_notification(
    db: AsyncSession,
    user_id: int,
    message: str,
    level: NotificationLevel = NotificationLevel.INFO,
    category: NotificationCategory = NotificationCategory.SYSTEM,
    extra_payload: dict | None = None,
    dedupe_key: str | None = None,
):
    """Trade-router notification compatibility wrapper.

    Trade-completion WebApp notifications are routed through receipt-backed
    delivery. Non-trade notifications keep the legacy helper behavior.
    """
    payload = dict(extra_payload or {})
    trade_number = _coerce_trade_user_id(payload.get("trade_number"))
    if category == NotificationCategory.TRADE and trade_number is None:
        raise ValueError("trade_notification_requires_trade_number")
    if category == NotificationCategory.TRADE:
        result = await deliver_webapp_trade_notification(
            db,
            trade_number=trade_number,
            recipient_user_id=user_id,
            message=message,
            current_server=current_server(),
            trade_id=_coerce_trade_user_id(payload.get("trade_id")),
            offer_id=_coerce_trade_user_id(payload.get("offer_id")),
            recipient_role=str(payload.get("recipient_role") or "trade_recipient"),
            principal_user_id=_coerce_trade_user_id(payload.get("principal_user_id")),
            side=str(payload.get("side") or "") or None,
            extra_payload=payload,
            reason=str(payload.get("delivery_reason") or "webapp_required"),
        )
        return result.notification

    return await _legacy_create_user_notification(
        db,
        user_id,
        message,
        level=level,
        category=category,
        extra_payload=extra_payload,
        dedupe_key=dedupe_key,
    )


async def _create_user_notification_background(
    user_id: int,
    message: str,
    level: NotificationLevel,
    category: NotificationCategory,
    extra_payload: dict | None,
) -> bool:
    from core.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as notification_db:
            await create_user_notification(
                notification_db,
                user_id,
                message,
                level=level,
                category=category,
                extra_payload=extra_payload,
            )
        return True
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_notification_create_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="notification",
            error_class=type(exc).__name__,
        )
        return False


def _queue_trade_user_notification(
    background_tasks: BackgroundTasks,
    user_id: int,
    message: str,
    *,
    level: NotificationLevel,
    category: NotificationCategory,
    extra_payload: dict | None,
) -> bool:
    background_tasks.add_task(
        _create_user_notification_background,
        user_id,
        message,
        level,
        category,
        extra_payload,
    )
    return True


async def _repair_trade_completion_delivery_background(
    trade_number: int,
    current_server_name: str,
) -> bool:
    from core.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as delivery_db:
            result = await delivery_db.execute(
                select(Trade)
                .options(
                    selectinload(Trade.offer),
                    selectinload(Trade.offer_user),
                    selectinload(Trade.responder_user),
                    selectinload(Trade.commodity),
                )
                .where(Trade.trade_number == int(trade_number))
                .limit(1)
            )
            trade = result.scalar_one_or_none()
            if trade is None:
                log_trading_event(
                    logger,
                    "trade_delivery_repair_missing_trade",
                    level="warning",
                    action="trading_side_effect",
                    result="noop",
                    side_effect="receipt_delivery_repair",
                    trade_number=trade_number,
                )
                return False
            await repair_webapp_trade_delivery_for_trade(
                delivery_db,
                trade,
                current_server=current_server_name,
            )
            await repair_telegram_trade_delivery_for_trade(
                delivery_db,
                trade,
                current_server=current_server_name,
            )
        return True
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_delivery_repair_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="receipt_delivery_repair",
            trade_number=trade_number,
            error_class=type(exc).__name__,
        )
        return False


def _queue_trade_completion_delivery_repair(
    background_tasks: BackgroundTasks,
    trade: Trade | object,
) -> bool:
    trade_number = _coerce_trade_user_id(getattr(trade, "trade_number", None))
    if trade_number is None:
        return False
    background_tasks.add_task(
        _repair_trade_completion_delivery_background,
        trade_number,
        current_server(),
    )
    return True


async def _update_trade_channel_buttons_background(offer: Offer | object) -> bool:
    try:
        return await update_channel_buttons(offer)
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_channel_buttons_update_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_channel_buttons",
            offer_id=getattr(offer, "id", None),
            error_class=type(exc).__name__,
        )
        return False


def _queue_trade_channel_buttons_update(background_tasks: BackgroundTasks, offer: Offer | object) -> bool:
    if current_server() != "foreign" or getattr(offer, "id", None) is None:
        return False
    background_tasks.add_task(
        _update_trade_channel_buttons_background,
        offer,
    )
    return True


def update_channel_buttons_sync(offer_id: int, remaining_quantity: int, status, lot_sizes) -> bool:
    """نسخه sync برای استفاده در BackgroundTasks"""
    from core.db import AsyncSessionLocal
    import asyncio
    
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return False
    
    # برای گرفتن channel_message_id باید از DB بخوانیم
    # این کار در یک thread جداگانه انجام می‌شود
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _update_channel_buttons_async(offer_id, remaining_quantity, status, lot_sizes)
            )
        finally:
            loop.close()
    except Exception as exc:
        log_trading_event(
            logger,
            "trade_channel_buttons_update_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_channel_buttons",
            offer_id=offer_id,
            error_class=type(exc).__name__,
        )
        return False


async def _update_channel_buttons_async(offer_id: int, remaining_quantity: int, offer_status, lot_sizes) -> bool:
    """Helper async function - باید offer را از دیتابیس بخواند"""
    from core.db import AsyncSessionLocal
    
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return False
    
    # گرفتن اطلاعات offer از دیتابیس
    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id, options=[selectinload(Offer.commodity)])
        if not offer or not offer.channel_message_id:
            return False
        offer.remaining_quantity = remaining_quantity
        offer.status = offer_status
        offer.lot_sizes = lot_sizes
        if remaining_quantity <= 0 or offer_status != OfferStatus.ACTIVE:
            return await apply_offer_channel_state(offer, reason="trade_channel_buttons_sync")

        if offer.is_wholesale or not lot_sizes:
            buttons = [[{
                "text": f"{remaining_quantity} عدد",
                "callback_data": build_channel_trade_callback_data(
                    offer_id=offer_id,
                    offer_public_id=getattr(offer, "offer_public_id", None),
                    amount=remaining_quantity,
                ),
            }]]
        else:
            valid_lots = get_available_trade_amounts(
                quantity=offer.quantity,
                remaining_quantity=remaining_quantity,
                is_wholesale=False,
                lot_sizes=lot_sizes,
            )
            if not valid_lots:
                buttons = None
            else:
                buttons = [[{
                    "text": f"{a} عدد",
                    "callback_data": build_channel_trade_callback_data(
                        offer_id=offer_id,
                        offer_public_id=getattr(offer, "offer_public_id", None),
                        amount=a,
                    ),
                } for a in valid_lots]]

        if buttons is not None:
            reply_markup = {"inline_keyboard": buttons}
        else:
            reply_markup = None

    result = await telegram_gateway.edit_message_reply_markup(
        channel_id,
        offer.channel_message_id,
        reply_markup=reply_markup,
        bot_token=bot_token,
        idempotency_key=f"trade-channel-buttons-sync:{offer_id}",
    )
    return result.ok


# --- Endpoints ---

def _normalize_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _normalize_internal_trade_source(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = normalize_server(value, default="")
    return normalized if normalized in KNOWN_SERVERS else None


def _normalize_trade_request_surface(value: object | None) -> OfferRequestSourceSurface:
    raw_value = getattr(value, "value", value)
    if not raw_value:
        return OfferRequestSourceSurface.WEBAPP
    try:
        return OfferRequestSourceSurface(str(raw_value).strip().lower())
    except ValueError:
        return OfferRequestSourceSurface.INTERNAL_FORWARD


def _offer_request_public_id(offer: Offer | object, trade_data: TradeCreate) -> str:
    return (
        (trade_data.offer_public_id or "").strip()
        or (getattr(offer, "offer_public_id", None) or "").strip()
    )


async def _resolve_internal_offer_by_public_id(
    db: AsyncSession,
    *,
    offer_public_id: str,
) -> Offer | None:
    public_id = (offer_public_id or "").strip()
    if not public_id:
        return None
    result = await db.execute(select(Offer).where(Offer.offer_public_id == public_id))
    return result.scalar_one_or_none()


def _build_offer_request_ledger_command(
    *,
    offer: Offer | object,
    trade_data: TradeCreate,
    owner_user: User | object,
    actor_user: User | object,
    request_source_surface: OfferRequestSourceSurface | str,
    request_source_server: str | None,
    edge_received_at: datetime | None,
    customer_relation: CustomerRelation | object | None = None,
    result_status: OfferRequestStatus | str = OfferRequestStatus.RECEIVED,
    public_failure_code: str | None = None,
    public_failure_message: str | None = None,
    internal_failure_code: str | None = None,
    internal_failure_context: Mapping[str, object] | None = None,
) -> OfferRequestLedgerCommand:
    snapshot = customer_relation_snapshot(customer_relation)
    return OfferRequestLedgerCommand(
        request_home_server=normalize_server(getattr(offer, "home_server", None), current_server()),
        local_offer_id=getattr(offer, "id", None),
        offer_public_id=_offer_request_public_id(offer, trade_data),
        requester_user_id=getattr(owner_user, "id", None),
        actor_user_id=getattr(actor_user, "id", None),
        request_source_surface=request_source_surface,
        request_source_server=normalize_server(request_source_server, current_server()),
        requested_quantity=trade_data.quantity,
        idempotency_key=trade_data.idempotency_key,
        received_at=edge_received_at,
        result_status=result_status,
        public_failure_code=public_failure_code,
        public_failure_message=public_failure_message,
        internal_failure_code=internal_failure_code,
        internal_failure_context=internal_failure_context,
        **snapshot,
    )


async def _create_offer_request_ledger_for_trade(
    db: AsyncSession,
    *,
    offer: Offer | object,
    trade_data: TradeCreate,
    owner_user: User | object,
    actor_user: User | object,
    request_source_surface: OfferRequestSourceSurface | str,
    request_source_server: str | None,
    edge_received_at: datetime | None,
    customer_relation: CustomerRelation | object | None = None,
) -> OfferRequest | None:
    offer_public_id = _offer_request_public_id(offer, trade_data)
    if not offer_public_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="شناسه عمومی لفظ برای ثبت درخواست موجود نیست.")
    if trade_data.offer_public_id and getattr(offer, "offer_public_id", None) != trade_data.offer_public_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="شناسه عمومی لفظ با رکورد مرجع همخوانی ندارد.")

    result = await create_offer_request_ledger_entry(
        db,
        _build_offer_request_ledger_command(
            offer=offer,
            trade_data=trade_data,
            owner_user=owner_user,
            actor_user=actor_user,
            request_source_surface=request_source_surface,
            request_source_server=request_source_server,
            edge_received_at=edge_received_at,
            customer_relation=customer_relation,
        ),
    )
    if result.duplicate_replay:
        replay_exception = _terminal_duplicate_request_http_exception(result.ledger)
        if replay_exception is not None:
            raise replay_exception
    return result.ledger


def _finalize_offer_request_ledger(
    ledger: OfferRequest | object | None,
    *,
    result_status: OfferRequestStatus,
    public_failure_code: str | None = None,
    public_failure_message: str | None = None,
    internal_failure_code: str | None = None,
    internal_failure_context: Mapping[str, object] | None = None,
    resulting_trade_id: int | None = None,
) -> None:
    if ledger is None:
        return
    apply_offer_request_decision(
        ledger,
        result_status=result_status,
        public_failure_code=public_failure_code,
        public_failure_message=public_failure_message,
        internal_failure_code=internal_failure_code,
        internal_failure_context=internal_failure_context,
        resulting_trade_id=resulting_trade_id,
    )


def _terminal_duplicate_request_http_exception(ledger: OfferRequest | object) -> HTTPException | None:
    raw_status = getattr(ledger, "result_status", None)
    status_value = getattr(raw_status, "value", raw_status)
    if status_value in {
        OfferRequestStatus.RECEIVED.value,
        OfferRequestStatus.AUTHORIZED.value,
        OfferRequestStatus.COMPLETED_TRADE.value,
    }:
        return None

    message = getattr(ledger, "public_failure_message", None) or TRADE_UNAVAILABLE_DETAIL
    if status_value == OfferRequestStatus.REJECTED_BUSINESS_RULE.value:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _is_completed_offer_request_replay(ledger: OfferRequest | object | None) -> bool:
    raw_status = getattr(ledger, "result_status", None)
    status_value = getattr(raw_status, "value", raw_status)
    return status_value == OfferRequestStatus.COMPLETED_TRADE.value


async def _load_completed_idempotent_replay_trade(
    db: AsyncSession,
    *,
    ledger: OfferRequest | object,
    trade_data: TradeCreate,
) -> Trade | None:
    resulting_trade_id = getattr(ledger, "resulting_trade_id", None)
    stmt = (
        select(Trade)
        .options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity),
        )
    )
    if resulting_trade_id is not None:
        stmt = stmt.where(Trade.id == resulting_trade_id)
    elif trade_data.idempotency_key:
        stmt = stmt.where(Trade.idempotency_key == trade_data.idempotency_key)
    else:
        return None

    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _validate_completed_idempotent_replay_trade(
    *,
    existing_trade: Trade | object,
    ledger: OfferRequest | object,
    offer: Offer | object,
    owner_user: User | object,
    actor_user: User | object,
    trade_data: TradeCreate,
) -> None:
    mismatches: list[str] = []
    resulting_trade_id = getattr(ledger, "resulting_trade_id", None)
    if resulting_trade_id is not None and getattr(existing_trade, "id", None) != resulting_trade_id:
        mismatches.append("resulting_trade_id")
    if getattr(existing_trade, "offer_id", None) not in (None, getattr(offer, "id", None)):
        mismatches.append("offer_id")
    if getattr(existing_trade, "commodity_id", None) != getattr(offer, "commodity_id", None):
        mismatches.append("commodity_id")
    if getattr(existing_trade, "quantity", None) != trade_data.quantity:
        mismatches.append("quantity")
    if _coerce_trade_user_id(getattr(existing_trade, "responder_user_id", None)) != _coerce_trade_user_id(
        getattr(owner_user, "id", None)
    ):
        mismatches.append("responder_user_id")
    existing_actor_user_id = _coerce_trade_user_id(getattr(existing_trade, "actor_user_id", None))
    expected_actor_user_id = _coerce_trade_user_id(getattr(actor_user, "id", None))
    if existing_actor_user_id is not None and existing_actor_user_id != expected_actor_user_id:
        mismatches.append("actor_user_id")
    existing_idempotency_key = getattr(existing_trade, "idempotency_key", None)
    if (
        existing_idempotency_key
        and trade_data.idempotency_key
        and existing_idempotency_key != trade_data.idempotency_key
    ):
        mismatches.append("idempotency_key")

    if mismatches:
        log_trading_event(
            logger,
            "trade_idempotent_replay_conflict",
            level="warning",
            action="trade_idempotent_replay",
            result="conflict",
            offer_id=trade_data.offer_id,
            trade_id=getattr(existing_trade, "id", None),
            mismatches=",".join(mismatches),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="کلید تکرار این معامله با درخواست فعلی همخوانی ندارد.",
        )


async def _try_return_completed_idempotent_replay(
    *,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    offer_request_ledger: OfferRequest | object | None,
    offer: Offer | object,
    trade_data: TradeCreate,
    owner_user: User | object,
    actor_user: User | object,
) -> TradeResponse | None:
    if not trade_data.idempotency_key or not _is_completed_offer_request_replay(offer_request_ledger):
        return None

    existing_trade_obj = await _load_completed_idempotent_replay_trade(
        db,
        ledger=offer_request_ledger,
        trade_data=trade_data,
    )
    if existing_trade_obj is None:
        return None

    _validate_completed_idempotent_replay_trade(
        existing_trade=existing_trade_obj,
        ledger=offer_request_ledger,
        offer=offer,
        owner_user=owner_user,
        actor_user=actor_user,
        trade_data=trade_data,
    )
    existing_identity_map = await _load_trade_identity_map_for_user_ids(
        db,
        [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
    )
    existing_customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
        db,
        [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
    )
    log_trading_event(
        logger,
        "trade_idempotent_replay",
        action="trade_idempotent_replay",
        result="completed_ledger_replay",
        offer_id=trade_data.offer_id,
        trade_id=getattr(existing_trade_obj, "id", None),
        trade_number=getattr(existing_trade_obj, "trade_number", None),
        source_server=current_server(),
        has_idempotency_key=True,
    )
    _queue_trade_completion_delivery_repair(background_tasks, existing_trade_obj)
    existing_response_kwargs = {
        "identity_map": existing_identity_map,
        "customer_relation_map": existing_customer_relation_map,
    }
    existing_offer_notes = getattr(offer, "notes", None)
    if existing_offer_notes is not None:
        existing_response_kwargs["offer_notes"] = existing_offer_notes
    return trade_to_response(existing_trade_obj, **existing_response_kwargs)


def _apply_offer_request_customer_snapshot(
    ledger: OfferRequest | object | None,
    relation: CustomerRelation | object | None,
) -> None:
    if ledger is None:
        return
    snapshot = customer_relation_snapshot(relation)
    for key, value in snapshot.items():
        setattr(ledger, key, value)


async def _commit_rejected_offer_request_ledger(
    db: AsyncSession,
    ledger: OfferRequest | object | None,
    *,
    result_status: OfferRequestStatus,
    public_failure_code: str,
    public_failure_message: str,
    internal_failure_code: str | None = None,
    internal_failure_context: Mapping[str, object] | None = None,
) -> None:
    if ledger is None or not callable(getattr(db, "commit", None)):
        return
    _finalize_offer_request_ledger(
        ledger,
        result_status=result_status,
        public_failure_code=public_failure_code,
        public_failure_message=public_failure_message,
        internal_failure_code=internal_failure_code,
        internal_failure_context=internal_failure_context,
    )
    try:
        await db.commit()
    except Exception as exc:
        rollback = getattr(db, "rollback", None)
        if callable(rollback):
            await rollback()
        log_trading_event(
            logger,
            "offer_request_ledger_rejection_commit_failed",
            level="warning",
            action="offer_request_ledger",
            result="failure",
            error_class=type(exc).__name__,
        )


async def _reject_trade_offer_contention(
    db: AsyncSession,
    *,
    trade_data: TradeCreate,
    owner_user: User | object,
    actor_user: User | object,
    request_source_surface: OfferRequestSourceSurface | str,
    request_source_server: str | None,
    edge_received_at: datetime | None,
) -> None:
    offer = await db.get(Offer, trade_data.offer_id)
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")

    ledger_trade_data = trade_data
    if trade_data.idempotency_key:
        ledger_trade_data = TradeCreate(
            offer_id=trade_data.offer_id,
            offer_public_id=trade_data.offer_public_id,
            quantity=trade_data.quantity,
            idempotency_key=None,
        )

    offer_request_ledger = await _create_offer_request_ledger_for_trade(
        db,
        offer=offer,
        trade_data=ledger_trade_data,
        owner_user=owner_user,
        actor_user=actor_user,
        request_source_surface=request_source_surface,
        request_source_server=request_source_server,
        edge_received_at=edge_received_at,
    )
    await _commit_rejected_offer_request_ledger(
        db,
        offer_request_ledger,
        result_status=OfferRequestStatus.REJECTED_CONFLICT,
        public_failure_code="offer_contention",
        public_failure_message=TRADE_CONFLICT_DETAIL,
        internal_failure_code="offer_execution_lock_busy",
        internal_failure_context={
            "offer_id": trade_data.offer_id,
            "source_server": normalize_server(request_source_server, current_server()),
            "source_surface": _normalize_trade_request_surface(request_source_surface).value,
            "idempotency_key_present": bool(trade_data.idempotency_key),
        },
    )
    log_trading_event(
        logger,
        "trade_execute.offer_contention_rejected",
        level="warning",
        action="trade_execute",
        result="conflict",
        offer_id=trade_data.offer_id,
        source_server=current_server(),
        request_source_server=normalize_server(request_source_server, current_server()),
        has_idempotency_key=bool(trade_data.idempotency_key),
    )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=TRADE_CONFLICT_DETAIL)


async def _flush_trade_request_state(db: AsyncSession) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()


async def _is_offer_expired_for_trade(offer: Offer, edge_received_at: Optional[datetime]) -> bool:
    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    if ts.offer_expiry_minutes <= 0 or not offer.created_at:
        return False

    created_at = _normalize_naive_utc(offer.created_at) or datetime.utcnow()
    expiry_at = created_at + timedelta(minutes=ts.offer_expiry_minutes)
    now = datetime.utcnow()
    edge_at = _normalize_naive_utc(edge_received_at)

    if edge_at and edge_at <= expiry_at:
        transit_seconds = max(0.0, (now - edge_at).total_seconds())
        if transit_seconds <= settings.trade_forward_grace_seconds:
            return False

    return now > expiry_at


def _is_time_limit_expired_offer(offer: Offer | object) -> bool:
    expire_reason = getattr(offer, "expire_reason", None)
    expire_reason_value = getattr(expire_reason, "value", expire_reason)
    return (
        getattr(offer, "status", None) == OfferStatus.EXPIRED
        and expire_reason_value == OfferExpiryReason.TIME_LIMIT
    )


async def _forward_trade_if_remote_home(
    db: AsyncSession,
    trade_data: TradeCreate,
    context: EffectiveOwnerActor,
    edge_received_at: datetime,
    *,
    request_source_surface: OfferRequestSourceSurface | str = OfferRequestSourceSurface.WEBAPP,
    request_pre_gated: bool = False,
) -> Optional[JSONResponse]:
    offer = await db.get(Offer, trade_data.offer_id)
    if not offer or not is_remote_home(offer.home_server):
        return None
    offer_public_id = (getattr(offer, "offer_public_id", None) or "").strip()
    if not offer_public_id:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "شناسه عمومی لفظ برای ارسال به سرور مرجع موجود نیست."},
        )

    owner_user = getattr(context, "owner_user", context)
    actor_user = getattr(context, "actor_user", owner_user)
    payload = {
        "offer_id": trade_data.offer_id,
        "offer_public_id": offer_public_id,
        "quantity": trade_data.quantity,
        "responder_user_id": owner_user.id,
        "edge_received_at": edge_received_at.isoformat(),
        "source_surface": _normalize_trade_request_surface(request_source_surface).value,
        "source_server": current_server(),
        "idempotency_key": trade_data.idempotency_key,
    }
    if request_pre_gated:
        payload["request_pre_gated"] = True
    if getattr(actor_user, "id", None) != owner_user.id:
        payload["actor_user_id"] = actor_user.id
    log_trading_event(
        logger,
        "trade_forward.remote_home",
        action="trade_forward",
        result="attempt",
        source_server=payload["source_server"],
        target_server=normalize_server(offer.home_server),
        offer_id=trade_data.offer_id,
        has_idempotency_key=bool(trade_data.idempotency_key),
        delegated_actor=getattr(actor_user, "id", None) != owner_user.id,
    )
    status_code, body = await forward_trade_to_home_server(offer.home_server, payload)
    if status_code >= 400:
        log_trading_event(
            logger,
            "trade_forward.remote_home_failed",
            level="warning",
            action="trade_forward",
            result="failure",
            source_server=payload["source_server"],
            target_server=normalize_server(offer.home_server),
            offer_id=trade_data.offer_id,
            status_code=status_code,
            has_idempotency_key=bool(trade_data.idempotency_key),
        )
    else:
        log_trading_event(
            logger,
            "trade_forward.remote_home_completed",
            action="trade_forward",
            result="success",
            source_server=payload["source_server"],
            target_server=normalize_server(offer.home_server),
            offer_id=trade_data.offer_id,
            status_code=status_code,
            has_idempotency_key=bool(trade_data.idempotency_key),
        )
    return JSONResponse(status_code=status_code, content=body)


async def _execute_trade_authoritatively(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    edge_received_at: Optional[datetime] = None,
    request_source_surface: OfferRequestSourceSurface | str = OfferRequestSourceSurface.WEBAPP,
    request_source_server: str | None = None,
    request_pre_gated: bool = False,
):
    """
    انجام معامله روی یک لفظ از MiniApp
    """
    from core.enums import UserRole
    owner_user = context.owner_user
    actor_user = context.actor_user
    request_source_surface = _normalize_trade_request_surface(request_source_surface)
    request_source_server = normalize_server(request_source_server, current_server())
    defer_cross_server_side_effects = request_source_server != current_server()
    defer_noncritical_side_effects = defer_cross_server_side_effects or request_pre_gated
    timing_started_at = time_module.perf_counter()
    timing_last_mark = timing_started_at
    idempotency_lock_held = False

    def mark_trade_phase(phase: str) -> None:
        nonlocal timing_last_mark
        if not defer_cross_server_side_effects:
            return
        now = time_module.perf_counter()
        log_trading_event(
            logger,
            "trade_execute.phase_timing",
            action="trade_execute",
            result="timing",
            offer_id=trade_data.offer_id,
            phase=phase,
            phase_duration_ms=round((now - timing_last_mark) * 1000, 2),
            total_duration_ms=round((now - timing_started_at) * 1000, 2),
            source_server=current_server(),
            request_source_server=request_source_server,
            has_idempotency_key=bool(trade_data.idempotency_key),
        )
        timing_last_mark = now

    offer_request_ledger: OfferRequest | None = None
    _ensure_accountant_market_access_allowed(context)
    log_trading_event(
        logger,
        "trade_execute.attempt",
        action="trade_execute",
        result="attempt",
        offer_id=trade_data.offer_id,
        source_server=current_server(),
        has_idempotency_key=bool(trade_data.idempotency_key),
        delegated_actor=getattr(actor_user, "id", None) != getattr(owner_user, "id", None),
    )
    
    # بررسی نقش
    if owner_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )

    if is_user_trade_blocked(owner_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="حساب شما غیرفعال است و امکان انجام معامله ندارید.",
        )

    market_evaluation = await evaluate_current_market_schedule(db)
    if not market_evaluation.is_open:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MARKET_CLOSED_DETAIL,
        )
    
    # بررسی مسدودیت (قبل از قفل)
    if owner_user.trading_restricted_until:
        if owner_user.trading_restricted_until > datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="حساب شما مسدود است."
            )
    
    # ===== قفل کاربر برای جلوگیری از Race Condition در محدودیت‌ها =====
    # اگر دو درخواست همزمان بیاید، اولی قفل می‌کند و دومی منتظر می‌ماند
    locked_user = await db.execute(
        select(User).where(User.id == owner_user.id).with_for_update()
    )
    owner_user = locked_user.scalar_one()
    
    # بررسی محدودیت معامله (حالا با قفل)
    allowed, error_msg = check_user_limits(owner_user, 'trade', trade_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)

    idempotency_lock_held = await _lock_trade_idempotency_key(db, trade_data.idempotency_key)
    if not await _try_lock_trade_offer_execution(db, trade_data.offer_id, wait=request_pre_gated):
        await _reject_trade_offer_contention(
            db,
            trade_data=trade_data,
            owner_user=owner_user,
            actor_user=actor_user,
            request_source_surface=request_source_surface,
            request_source_server=request_source_server,
            edge_received_at=edge_received_at,
        )
    
    # گرفتن لفظ با قفل
    offer = await db.get(Offer, trade_data.offer_id, with_for_update=True)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    mark_trade_phase("locked_offer")

    offer_request_ledger = await _create_offer_request_ledger_for_trade(
        db,
        offer=offer,
        trade_data=trade_data,
        owner_user=owner_user,
        actor_user=actor_user,
        request_source_surface=request_source_surface,
        request_source_server=request_source_server,
        edge_received_at=edge_received_at,
    )

    completed_replay_response = await _try_return_completed_idempotent_replay(
        db=db,
        background_tasks=background_tasks,
        offer_request_ledger=offer_request_ledger,
        offer=offer,
        trade_data=trade_data,
        owner_user=owner_user,
        actor_user=actor_user,
    )
    if completed_replay_response is not None:
        return completed_replay_response
    
    expired_for_trade = await _is_offer_expired_for_trade(offer, edge_received_at)

    allow_in_flight_after_time_limit_expiry = (
        _is_time_limit_expired_offer(offer)
        and edge_received_at is not None
        and not expired_for_trade
    )
    if (offer.status != OfferStatus.ACTIVE and not allow_in_flight_after_time_limit_expiry) or expired_for_trade:
        await _commit_rejected_offer_request_ledger(
            db,
            offer_request_ledger,
            result_status=OfferRequestStatus.REJECTED_OFFER_EXPIRED,
            public_failure_code="offer_not_active",
            public_failure_message="این لفظ دیگر فعال نیست.",
            internal_failure_code="offer_not_active_or_expired",
            internal_failure_context={
                "offer_status": getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)),
                "expired_for_trade": expired_for_trade,
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ دیگر فعال نیست.")
    
    if offer.user_id == owner_user.id:
        await _commit_rejected_offer_request_ledger(
            db,
            offer_request_ledger,
            result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
            public_failure_code="own_offer",
            public_failure_message="نمی‌توانید روی لفظ خودتان معامله کنید.",
            internal_failure_code="requester_owns_offer",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="نمی‌توانید روی لفظ خودتان معامله کنید.")

    await db.refresh(offer, ["user"])
    offer_owner_user = getattr(offer, "user", None)
    if offer_owner_user is None or is_user_trade_blocked(offer_owner_user):
        await _commit_rejected_offer_request_ledger(
            db,
            offer_request_ledger,
            result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
            public_failure_code="offer_owner_inactive",
            public_failure_message="این لفظ در حال حاضر قابل معامله نیست.",
            internal_failure_code="offer_owner_inactive_or_missing",
            internal_failure_context={
                "offer_user_id": getattr(offer, "user_id", None),
                "offer_owner_missing": offer_owner_user is None,
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ در حال حاضر قابل معامله نیست.")
    
    responder_customer_relation = await get_active_customer_relation_for_customer(db, owner_user.id)
    _apply_offer_request_customer_snapshot(offer_request_ledger, responder_customer_relation)
    responder_customer_tier = getattr(
        getattr(responder_customer_relation, "customer_tier", None),
        "value",
        getattr(responder_customer_relation, "customer_tier", None),
    )
    source_customer_relation = await get_active_customer_relation_for_customer(db, offer.user_id)

    # بررسی بلاک بین سرگروه‌های واقعی دو طرف. اگر یک طرف مشتری باشد،
    # بلاک سرگروه همان مشتری باید روی معامله او هم اثر بگذارد.
    blocked, _, responder_principal_user_id, source_principal_user_id = await is_trade_blocked_by_principals(
        db,
        owner_user.id,
        offer.user_id,
        user_a_customer_relation=responder_customer_relation,
        user_b_customer_relation=source_customer_relation,
    )
    if blocked:
        await _commit_rejected_offer_request_ledger(
            db,
            offer_request_ledger,
            result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
            public_failure_code="business_rule",
            public_failure_message="امکان انجام این معامله وجود ندارد.",
            internal_failure_code="blocked_principal_pair",
            internal_failure_context={
                "source_principal_user_id": source_principal_user_id,
                "responder_principal_user_id": responder_principal_user_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="امکان انجام این معامله وجود ندارد."
        )
    
    is_valid_amount, amount_error, trade_quantity, available_amounts = validate_offer_trade_amount(
        quantity=offer.quantity,
        remaining_quantity=offer.remaining_quantity,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
        requested_amount=trade_data.quantity,
    )
    if not is_valid_amount:
        if (
            not offer.is_wholesale
            and available_amounts
            and amount_error == "این لات دیگر موجود نیست."
        ):
            await db.refresh(offer, ["commodity"])
            await _commit_rejected_offer_request_ledger(
                db,
                offer_request_ledger,
                result_status=OfferRequestStatus.REJECTED_LOT_UNAVAILABLE,
                public_failure_code="lot_unavailable",
                public_failure_message=amount_error,
                internal_failure_code="lot_unavailable",
                internal_failure_context={
                    "requested_amount": trade_data.quantity,
                    "available_amounts": available_amounts,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=build_lot_unavailable_suggestion_payload(
                    offer_id=offer.id,
                    offer_public_id=getattr(offer, "offer_public_id", None),
                    requested_amount=trade_data.quantity,
                    offer_type=offer.offer_type,
                    settlement_type=getattr(offer, "settlement_type", None),
                    commodity_name=offer.commodity.name if offer.commodity else None,
                    price=offer.price,
                    remaining_quantity=offer.remaining_quantity or offer.quantity,
                    available_amounts=available_amounts,
                ),
            )
        await _commit_rejected_offer_request_ledger(
            db,
            offer_request_ledger,
            result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
            public_failure_code="invalid_quantity",
            public_failure_message=amount_error,
            internal_failure_code="invalid_trade_amount",
            internal_failure_context={
                "requested_amount": trade_data.quantity,
                "available_amounts": available_amounts,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=amount_error
        )
    
    # بارگذاری روابط لفظ
    await db.refresh(offer, ["user", "commodity"])
    mark_trade_phase("validated_amount")

    is_tier2_customer_responder = (
        responder_customer_relation is not None
        and responder_customer_tier == CustomerTier.TIER_2.value
    )

    executed_trade_price = offer.price
    if is_tier2_customer_responder:
        executed_trade_price = apply_customer_commission(
            offer.price,
            getattr(responder_customer_relation, "commission_rate", None),
            offer.offer_type,
        )

    source_principal_user: object | None = offer.user if source_principal_user_id == offer.user_id else None
    responder_principal_user: object | None = owner_user if responder_principal_user_id == owner_user.id else None

    if responder_principal_user is None and responder_principal_user_id == offer.user_id:
        responder_principal_user = offer.user

    if responder_principal_user is None:
        responder_principal_user = await db.get(User, responder_principal_user_id)
        if responder_principal_user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="امکان انجام این معامله وجود ندارد.",
            )

    if source_principal_user is None:
        if source_principal_user_id == responder_principal_user_id:
            source_principal_user = responder_principal_user
        else:
            source_principal_user = await db.get(User, source_principal_user_id)
            if source_principal_user is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="امکان انجام این معامله وجود ندارد.",
                )

    try:
        trade_execution_plan = _build_trade_execution_plan(
            offer_user_id=offer.user_id,
            offer_user=offer.user,
            source_principal_user_id=source_principal_user_id,
            source_principal_user=source_principal_user,
            responder_principal_user_id=responder_principal_user_id,
            responder_principal_user=responder_principal_user,
            owner_user_id=owner_user.id,
            owner_user=owner_user,
        )
    except TradeExecutionPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or TRADE_UNAVAILABLE_DETAIL,
        ) from exc

    trade_execution_nodes = [
        {
            "user_id": node.user_id,
            "user": node.user,
        }
        for node in trade_execution_plan.nodes
    ]
    uses_customer_trade_chain = trade_execution_plan.uses_customer_trade_chain
    mark_trade_phase("built_execution_plan")

    if trade_data.idempotency_key:
        if not idempotency_lock_held:
            await _lock_trade_idempotency_key(db, trade_data.idempotency_key)
        existing_trade = await db.execute(
            select(Trade)
            .options(
                selectinload(Trade.offer_user),
                selectinload(Trade.responder_user),
                selectinload(Trade.commodity),
            )
            .where(Trade.idempotency_key == trade_data.idempotency_key)
        )
        existing_trade_obj = existing_trade.scalar_one_or_none()
        if existing_trade_obj:
            try:
                _validate_idempotent_trade_replay(
                    existing_trade=existing_trade_obj,
                    offer=offer,
                    owner_user=owner_user,
                    actor_user=actor_user,
                    trade_quantity=trade_quantity,
                    expected_price=executed_trade_price,
                    uses_customer_trade_chain=uses_customer_trade_chain,
                )
            except TradeIdempotencyConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            existing_identity_map = await _load_trade_identity_map_for_user_ids(
                db,
                [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
            )
            existing_customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
                db,
                [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
            )
            log_trading_event(
                logger,
                "trade_idempotent_replay",
                action="trade_idempotent_replay",
                result="replay",
                offer_id=trade_data.offer_id,
                trade_id=getattr(existing_trade_obj, "id", None),
                trade_number=getattr(existing_trade_obj, "trade_number", None),
                source_server=current_server(),
                has_idempotency_key=True,
                chain_length=len(trade_execution_nodes) - 1,
            )
            _finalize_offer_request_ledger(
                offer_request_ledger,
                result_status=OfferRequestStatus.COMPLETED_TRADE,
                resulting_trade_id=getattr(existing_trade_obj, "id", None),
            )
            if callable(getattr(db, "commit", None)):
                await _commit_trade_execution(db)
            _queue_trade_completion_delivery_repair(background_tasks, existing_trade_obj)
            existing_response_kwargs = {
                "identity_map": existing_identity_map,
                "customer_relation_map": existing_customer_relation_map,
            }
            existing_offer_notes = getattr(offer, "notes", None)
            if existing_offer_notes is not None:
                existing_response_kwargs["offer_notes"] = existing_offer_notes
            return trade_to_response(existing_trade_obj, **existing_response_kwargs)
    mark_trade_phase("checked_idempotency")
    
    trade_number_count = max(1, len(trade_execution_nodes) - 1) if uses_customer_trade_chain else 1
    allocated_trade_numbers = await _allocate_trade_numbers(db, trade_number_count)
    mark_trade_phase("allocated_trade_number")
    
    # نوع معامله از دید پاسخ‌دهنده
    responder_trade_type = TradeType.BUY if offer.offer_type == OfferType.SELL else TradeType.SELL

    created_chain_trades: list[Trade] = []
    response_trade_record: Trade
    response_trade_number: int

    if uses_customer_trade_chain:
        final_leg_index = len(trade_execution_nodes) - 2
        for leg_index, (offer_node, responder_node) in enumerate(
            zip(trade_execution_nodes, trade_execution_nodes[1:])
        ):
            leg_offer_user = offer_node["user"]
            leg_responder_user = responder_node["user"]
            leg_trade = Trade(
                trade_number=allocated_trade_numbers[leg_index],
                offer_id=offer.id if leg_index == 0 else None,
                offer_user_id=int(offer_node["user_id"]),
                offer_user_mobile=getattr(leg_offer_user, "mobile_number", None),
                responder_user_id=int(responder_node["user_id"]),
                responder_user_mobile=getattr(leg_responder_user, "mobile_number", None),
                actor_user_id=actor_user.id,
                commodity_id=offer.commodity_id,
                trade_type=responder_trade_type,
                settlement_type=getattr(offer, "settlement_type", SettlementType.CASH),
                quantity=trade_quantity,
                price=executed_trade_price if leg_index == final_leg_index and is_tier2_customer_responder else offer.price,
                status=TradeStatus.COMPLETED,
                idempotency_key=trade_data.idempotency_key if leg_index == final_leg_index else None,
            )
            db.add(leg_trade)
            created_chain_trades.append(leg_trade)

        response_trade_record = created_chain_trades[-1]
    else:
        response_trade_record = Trade(
            trade_number=allocated_trade_numbers[0],
            offer_id=offer.id,
            offer_user_id=offer.user_id,
            offer_user_mobile=offer.user.mobile_number if offer.user else None,
            responder_user_id=owner_user.id,
            responder_user_mobile=owner_user.mobile_number,
            actor_user_id=actor_user.id,
            commodity_id=offer.commodity_id,
            trade_type=responder_trade_type,
            settlement_type=getattr(offer, "settlement_type", SettlementType.CASH),
            quantity=trade_quantity,
            price=executed_trade_price,
            status=TradeStatus.COMPLETED,
            idempotency_key=trade_data.idempotency_key,
        )
        db.add(response_trade_record)

    response_trade_number = response_trade_record.trade_number
    
    try:
        lot_sizes_modified = _apply_offer_trade_mutation(offer, trade_quantity)
    except TradeAtomicityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if lot_sizes_modified:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(offer, "lot_sizes")  # اجبار SQLAlchemy برای تشخیص تغییر

    await _flush_trade_request_state(db)
    _finalize_offer_request_ledger(
        offer_request_ledger,
        result_status=OfferRequestStatus.COMPLETED_TRADE,
        resulting_trade_id=getattr(response_trade_record, "id", None),
    )
    _apply_trade_counter_increment(owner_user, trade_quantity)
    mark_trade_phase("flushed_trade_state")
    
    # Commit با محافظت Optimistic Locking
    await _commit_trade_execution(db)
    mark_trade_phase("committed")
    
    # بارگذاری روابط معامله
    result = await db.execute(
        select(Trade)
        .options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity)
        )
        .where(Trade.id == response_trade_record.id)
    )
    created_trade = response_trade_record
    response_trade = result.scalar_one()
    response_offer_user = getattr(response_trade, "offer_user", None)
    if response_offer_user is None:
        if uses_customer_trade_chain:
            response_offer_user = trade_execution_nodes[-2]["user"]
        else:
            response_offer_user = offer.user
    response_responder_user = getattr(response_trade, "responder_user", None) or owner_user
    chain_participant_trades = created_chain_trades if uses_customer_trade_chain else [response_trade_record]
    participant_identity_map = await _load_trade_identity_map_for_user_ids(
        db,
        [
            raw_user_id
            for trade in chain_participant_trades
            for raw_user_id in (
                getattr(trade, "offer_user_id", None),
                getattr(trade, "responder_user_id", None),
            )
        ],
    )
    participant_customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
        db,
        [
            raw_user_id
            for trade in chain_participant_trades
            for raw_user_id in (
                getattr(trade, "offer_user_id", None),
                getattr(trade, "responder_user_id", None),
            )
        ],
    )
    mark_trade_phase("loaded_response_context")
    offer_user_payload = _build_trade_participant_payload(
        "offer_user",
        user=response_offer_user,
        user_id=getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id,
        identity_map=participant_identity_map,
        customer_relation_map=participant_customer_relation_map,
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=response_responder_user,
        user_id=getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id,
        identity_map=participant_identity_map,
        customer_relation_map=participant_customer_relation_map,
    )
    offer_user_display_name = offer_user_payload.get("offer_user_name") or "نامشخص"
    responder_user_display_name = responder_user_payload.get("responder_user_name") or "نامشخص"
    if defer_noncritical_side_effects:
        _queue_trade_channel_buttons_update(background_tasks, offer)
    else:
        try:
            await update_channel_buttons(offer)
        except Exception as exc:
            log_trading_event(
                logger,
                "trade_channel_buttons_update_failed",
                level="error",
                action="trading_side_effect",
                result="failure",
                side_effect="telegram_channel_buttons",
                offer_id=getattr(offer, "id", None),
                trade_id=getattr(response_trade_record, "id", None),
                trade_number=response_trade_number,
                error_class=type(exc).__name__,
            )
    
    log_trading_event(
        logger,
        "trade_execute.accepted",
        action="trade_execute",
        result="success",
        offer_id=getattr(offer, "id", None),
        trade_id=getattr(response_trade, "id", None) or getattr(response_trade_record, "id", None),
        trade_number=response_trade_number,
        source_server=current_server(),
        has_idempotency_key=bool(trade_data.idempotency_key),
        delegated_actor=getattr(actor_user, "id", None) != getattr(owner_user, "id", None),
        chain_length=len(trade_execution_nodes) - 1,
    )

    trade_timestamp = getattr(response_trade, "created_at", None) or getattr(created_trade, "created_at", None) or datetime.now(timezone.utc)
    trade_datetime = to_jalali_str(trade_timestamp, "%Y/%m/%d   %H:%M") or ""

    if responder_trade_type == TradeType.BUY:
        respond_emoji = "🟢"
        respond_type_fa = "خرید"
        offer_emoji = "🔴"
        offer_type_fa = "فروش"
    else:
        respond_emoji = "🔴"
        respond_type_fa = "فروش"
        offer_emoji = "🟢"
        offer_type_fa = "خرید"

    async def _create_trade_notifications_for_leg(
        *,
        audience_user_ids: list[int],
        trade_emoji: str,
        trade_type_label: str,
        trade_price: int,
        trade_number: int,
        counterparty_name: str | None,
        counterparty_user_id: int | None,
        trade_path_summary: str | None,
        offer_notes: str | None,
        extra_payload: dict[str, object | None],
    ) -> None:
        for audience_user_id in audience_user_ids:
            message = _build_trade_notification_message(
                trade_emoji=trade_emoji,
                trade_type_label=trade_type_label,
                trade_price=trade_price,
                trade_quantity=trade_quantity,
                commodity_name=offer.commodity.name,
                trade_number=trade_number,
                trade_datetime=trade_datetime,
                counterparty_name=counterparty_name,
                counterparty_user_id=counterparty_user_id,
                audience_user_id=audience_user_id,
                customer_relation_map=participant_customer_relation_map,
                trade_path_summary=trade_path_summary,
                offer_notes=offer_notes,
                settlement_type=getattr(offer, "settlement_type", SettlementType.CASH),
            )
            if defer_noncritical_side_effects:
                _queue_trade_user_notification(
                    background_tasks,
                    audience_user_id,
                    message,
                    level=NotificationLevel.SUCCESS,
                    category=NotificationCategory.TRADE,
                    extra_payload=extra_payload,
                )
            else:
                await create_user_notification(
                    db,
                    audience_user_id,
                    message,
                    level=NotificationLevel.SUCCESS,
                    category=NotificationCategory.TRADE,
                    extra_payload=extra_payload,
                )

    chain_leg_contexts: list[dict[str, object]] = []
    if uses_customer_trade_chain:
        for leg_index, leg_trade in enumerate(created_chain_trades):
            leg_offer_user = trade_execution_nodes[leg_index]["user"]
            leg_responder_user = trade_execution_nodes[leg_index + 1]["user"]
            if leg_index == len(created_chain_trades) - 1:
                leg_trade_obj = response_trade
                delivery_trade_obj = response_trade_record
                leg_offer_user = response_offer_user or leg_offer_user
                leg_responder_user = response_responder_user or leg_responder_user
            else:
                leg_trade_obj = leg_trade
                delivery_trade_obj = leg_trade
            chain_leg_contexts.append(
                {
                    "trade": leg_trade_obj,
                    "delivery_trade": delivery_trade_obj,
                    "offer_user": leg_offer_user,
                    "responder_user": leg_responder_user,
                }
            )

        for leg_context in [chain_leg_contexts[-1], *chain_leg_contexts[:-1]]:
            leg_trade_obj = leg_context["trade"]
            leg_offer_user = leg_context["offer_user"]
            leg_responder_user = leg_context["responder_user"]
            leg_context["responder_audience"] = [
                normalized_user_id
                for normalized_user_id in [_coerce_trade_user_id(getattr(leg_trade_obj, "responder_user_id", None))]
                if normalized_user_id is not None
            ]
            leg_context["offer_audience"] = [
                normalized_user_id
                for normalized_user_id in [_coerce_trade_user_id(getattr(leg_trade_obj, "offer_user_id", None))]
                if normalized_user_id is not None
            ]
            leg_offer_payload = _build_trade_participant_payload(
                "offer_user",
                user=leg_offer_user,
                user_id=getattr(leg_trade_obj, "offer_user_id", None),
                identity_map=participant_identity_map,
                customer_relation_map=participant_customer_relation_map,
            )
            leg_responder_payload = _build_trade_participant_payload(
                "responder_user",
                user=leg_responder_user,
                user_id=getattr(leg_trade_obj, "responder_user_id", None),
                identity_map=participant_identity_map,
                customer_relation_map=participant_customer_relation_map,
            )
            leg_trade_path_summary = _build_trade_path_payload(
                offer_user_id=getattr(leg_trade_obj, "offer_user_id", None),
                responder_user_id=getattr(leg_trade_obj, "responder_user_id", None),
                customer_relation_map=participant_customer_relation_map,
            ).get("trade_path_summary")
            _queue_trade_completion_delivery_repair(
                background_tasks,
                leg_context.get("delivery_trade") or leg_trade_obj,
            )

            try:
                leg_responder_audience = await build_trade_notification_audience_user_ids(
                    db,
                    [getattr(leg_trade_obj, "responder_user_id", None)],
                )
                leg_offer_audience = await build_trade_notification_audience_user_ids(
                    db,
                    [getattr(leg_trade_obj, "offer_user_id", None)],
                )
                leg_context["responder_audience"] = leg_responder_audience
                leg_context["offer_audience"] = leg_offer_audience
                await _create_trade_notifications_for_leg(
                    audience_user_ids=leg_responder_audience,
                    trade_emoji=respond_emoji,
                    trade_type_label=respond_type_fa,
                    trade_price=getattr(leg_trade_obj, "price", offer.price),
                    trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                    counterparty_name=leg_offer_payload.get("offer_user_name") or "نامشخص",
                    counterparty_user_id=_coerce_trade_user_id(getattr(leg_trade_obj, "offer_user_id", None)),
                    trade_path_summary=leg_trade_path_summary,
                    offer_notes=getattr(offer, "notes", None),
                    extra_payload=_build_trade_notification_extra_payload(
                        "offer_user",
                        leg_offer_payload,
                        trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                        settlement_type=getattr(leg_trade_obj, "settlement_type", getattr(offer, "settlement_type", None)),
                    ),
                )
                await _create_trade_notifications_for_leg(
                    audience_user_ids=leg_offer_audience,
                    trade_emoji=offer_emoji,
                    trade_type_label=offer_type_fa,
                    trade_price=getattr(leg_trade_obj, "price", offer.price),
                    trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                    counterparty_name=leg_responder_payload.get("responder_user_name") or "نامشخص",
                    counterparty_user_id=_coerce_trade_user_id(getattr(leg_trade_obj, "responder_user_id", None)),
                    trade_path_summary=leg_trade_path_summary,
                    offer_notes=getattr(offer, "notes", None),
                    extra_payload=_build_trade_notification_extra_payload(
                        "responder_user",
                        leg_responder_payload,
                        trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                        settlement_type=getattr(leg_trade_obj, "settlement_type", getattr(offer, "settlement_type", None)),
                    ),
                )
            except Exception as exc:
                log_trading_event(
                    logger,
                    "trade_notification_create_failed",
                    level="warning",
                    action="trading_side_effect",
                    result="failure",
                    side_effect="notification",
                    offer_id=getattr(offer, "id", None),
                    trade_id=getattr(leg_trade_obj, "id", None),
                    trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                    error_class=type(exc).__name__,
                )
    else:
        _queue_trade_completion_delivery_repair(background_tasks, response_trade_record)

        responder_audience = [owner_user.id]
        offer_owner_audience = [offer.user_id]
        try:
            responder_audience = await build_trade_notification_audience_user_ids(db, [owner_user.id])
            offer_owner_audience = await build_trade_notification_audience_user_ids(db, [offer.user_id])
            responder_notification_payload = _build_trade_notification_extra_payload(
                "offer_user",
                offer_user_payload,
                trade_number=response_trade_number,
                settlement_type=getattr(response_trade, "settlement_type", getattr(offer, "settlement_type", None)),
            )
            offer_owner_notification_payload = _build_trade_notification_extra_payload(
                "responder_user",
                responder_user_payload,
                trade_number=response_trade_number,
                settlement_type=getattr(response_trade, "settlement_type", getattr(offer, "settlement_type", None)),
            )

            await _create_trade_notifications_for_leg(
                audience_user_ids=responder_audience,
                trade_emoji=respond_emoji,
                trade_type_label=respond_type_fa,
                trade_price=executed_trade_price,
                trade_number=response_trade_number,
                counterparty_name=offer_user_display_name,
                counterparty_user_id=_coerce_trade_user_id(
                    getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id
                ),
                trade_path_summary=_build_trade_path_payload(
                    offer_user_id=getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id,
                    responder_user_id=getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id,
                    customer_relation_map=participant_customer_relation_map,
                ).get("trade_path_summary"),
                offer_notes=getattr(offer, "notes", None),
                extra_payload=responder_notification_payload,
            )
            await _create_trade_notifications_for_leg(
                audience_user_ids=offer_owner_audience,
                trade_emoji=offer_emoji,
                trade_type_label=offer_type_fa,
                trade_price=executed_trade_price,
                trade_number=response_trade_number,
                counterparty_name=responder_user_display_name,
                counterparty_user_id=_coerce_trade_user_id(
                    getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id
                ),
                trade_path_summary=_build_trade_path_payload(
                    offer_user_id=getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id,
                    responder_user_id=getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id,
                    customer_relation_map=participant_customer_relation_map,
                ).get("trade_path_summary"),
                offer_notes=getattr(offer, "notes", None),
                extra_payload=offer_owner_notification_payload,
            )
        except Exception as exc:
            log_trading_event(
                logger,
                "trade_notification_create_failed",
                level="warning",
                action="trading_side_effect",
                result="failure",
                side_effect="notification",
                offer_id=getattr(offer, "id", None),
                trade_id=getattr(response_trade_record, "id", None),
                trade_number=response_trade_number,
                error_class=type(exc).__name__,
            )
    mark_trade_phase("prepared_side_effects")
    
    # ارسال رویداد SSE
    if uses_customer_trade_chain:
        for leg_context in [chain_leg_contexts[-1], *chain_leg_contexts[:-1]]:
            leg_trade_obj = leg_context["trade"]
            await _publish_trade_created_realtime(
                trade=leg_trade_obj,
                fallback_trade_id=getattr(leg_trade_obj, "id", None),
                fallback_trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                fallback_offer_id=getattr(leg_trade_obj, "offer_id", None),
                fallback_commodity_id=offer.commodity_id,
                fallback_quantity=trade_quantity,
                fallback_price=getattr(leg_trade_obj, "price", offer.price),
                fallback_status=getattr(getattr(leg_trade_obj, "status", None), "value", None) or TradeStatus.COMPLETED.value,
                fallback_created_at=to_jalali_str(getattr(leg_trade_obj, "created_at", None)) or "",
                fallback_offer_user_id=getattr(leg_trade_obj, "offer_user_id", None),
                fallback_responder_user_id=getattr(leg_trade_obj, "responder_user_id", None),
                commodity_name=offer.commodity.name if offer.commodity else None,
                fallback_trade_type=responder_trade_type.value,
                offer_user=leg_context["offer_user"],
                responder_user=leg_context["responder_user"],
                identity_map=participant_identity_map,
                customer_relation_map=participant_customer_relation_map,
                responder_audience_user_ids=leg_context.get("responder_audience"),
                offer_owner_audience_user_ids=leg_context.get("offer_audience"),
            )
    else:
        await _publish_trade_created_realtime(
            trade=response_trade,
            fallback_trade_id=getattr(response_trade, "id", None) or created_trade.id,
            fallback_trade_number=response_trade_number,
            fallback_offer_id=getattr(response_trade, "offer_id", None) or created_trade.offer_id,
            fallback_commodity_id=offer.commodity_id,
            fallback_quantity=trade_quantity,
            fallback_price=executed_trade_price,
            fallback_status=getattr(getattr(response_trade, "status", None), "value", None) or TradeStatus.COMPLETED.value,
            fallback_created_at=to_jalali_str(getattr(response_trade, "created_at", None)) or "",
            fallback_offer_user_id=created_trade.offer_user_id,
            fallback_responder_user_id=created_trade.responder_user_id,
            commodity_name=offer.commodity.name if offer.commodity else None,
            fallback_trade_type=responder_trade_type.value,
            offer_user=response_offer_user,
            responder_user=response_responder_user,
            identity_map=participant_identity_map,
            customer_relation_map=participant_customer_relation_map,
            responder_audience_user_ids=responder_audience,
            offer_owner_audience_user_ids=offer_owner_audience,
        )

    from .realtime import publish_event
    try:
        await publish_event("offer:updated", {
            "id": offer.id,
            "remaining_quantity": offer.remaining_quantity,
            "lot_sizes": offer.lot_sizes,
            "status": offer.status.value
        })
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_update_realtime_publish_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="realtime_publish",
            offer_id=getattr(offer, "id", None),
            trade_id=getattr(response_trade, "id", None) or getattr(response_trade_record, "id", None),
            trade_number=response_trade_number,
            error_class=type(exc).__name__,
        )
    mark_trade_phase("published_realtime")
    
    response_kwargs = {
        "identity_map": participant_identity_map,
        "customer_relation_map": participant_customer_relation_map,
        "viewer_context": context,
        "history_target_user_id": owner_user.id,
    }
    offer_notes = getattr(offer, "notes", None)
    if offer_notes is not None:
        response_kwargs["offer_notes"] = offer_notes
    response = trade_to_response(response_trade, **response_kwargs)
    mark_trade_phase("built_response")
    return response


async def _execute_trade_authoritatively_with_transient_retry(
    *,
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    context: EffectiveOwnerActor,
    edge_received_at: Optional[datetime] = None,
    request_source_surface: OfferRequestSourceSurface | str = OfferRequestSourceSurface.WEBAPP,
    request_source_server: str | None = None,
    request_pre_gated: bool = False,
    max_attempts: int = TRADE_TRANSIENT_RETRY_ATTEMPTS,
):
    attempts = max(1, int(max_attempts or 1))
    retry_context = context
    for attempt in range(1, attempts + 1):
        try:
            return await _execute_trade_authoritatively(
                trade_data=trade_data,
                background_tasks=background_tasks,
                db=db,
                context=retry_context,
                edge_received_at=edge_received_at,
                request_source_surface=request_source_surface,
                request_source_server=request_source_server,
                request_pre_gated=request_pre_gated,
            )
        except Exception as exc:
            if not _is_retryable_trade_transient_error(exc) or attempt >= attempts:
                raise
            try:
                await db.rollback()
            except Exception:
                pass
            expunge_all = getattr(db, "expunge_all", None)
            if callable(expunge_all):
                expunge_all()

            owner_user_id = getattr(retry_context.owner_user, "id", None)
            actor_user_id = getattr(retry_context.actor_user, "id", None)
            owner_user = await db.get(User, int(owner_user_id)) if owner_user_id is not None else retry_context.owner_user
            actor_user = owner_user
            if actor_user_id is not None and actor_user_id != owner_user_id:
                actor_user = await db.get(User, int(actor_user_id))
            elif actor_user_id is not None:
                actor_user = owner_user
            if owner_user is None or actor_user is None:
                raise
            retry_context = EffectiveOwnerActor(
                owner_user=owner_user,
                actor_user=actor_user,
                relation=getattr(retry_context, "relation", None),
                is_accountant_context=getattr(retry_context, "is_accountant_context", False),
            )
            log_trading_event(
                logger,
                "trade_execute.transient_retry",
                level="warning",
                action="trade_execute",
                result="attempt",
                offer_id=trade_data.offer_id,
                error_class=type(exc).__name__,
                has_idempotency_key=bool(trade_data.idempotency_key),
            )
            await asyncio.sleep(TRADE_TRANSIENT_RETRY_BASE_DELAY_SECONDS * attempt)

    raise RuntimeError("unreachable_trade_transient_retry_loop")


@router.post("/", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def create_trade(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    trade_contention_lease: TradeContentionLease = Depends(_acquire_trade_contention_gate_dependency),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    try:
        edge_received_at = datetime.utcnow()
        _ensure_accountant_market_access_allowed(context)
        forwarded_response = await _forward_trade_if_remote_home(
            db,
            trade_data,
            context,
            edge_received_at,
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_pre_gated=trade_contention_lease_was_pre_gated(trade_contention_lease),
        )
        if forwarded_response is not None:
            return forwarded_response

        return await _execute_trade_authoritatively_with_transient_retry(
            trade_data=trade_data,
            background_tasks=background_tasks,
            db=db,
            context=context,
            edge_received_at=edge_received_at,
            request_source_surface=OfferRequestSourceSurface.WEBAPP,
            request_source_server=current_server(),
            request_pre_gated=trade_contention_lease_was_pre_gated(trade_contention_lease),
        )
    finally:
        await _release_trade_contention_lease(trade_contention_lease)


@router.post("/internal/execute", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def execute_trade_internal(
    internal_data: InternalTradeExecuteRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    target_server = current_server()
    payload_source_server = _normalize_internal_trade_source(internal_data.source_server)
    header_source_server = _normalize_internal_trade_source(raw_request.headers.get("x-source-server"))
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        log_trading_event(
            logger,
            "trade_internal_execute.rejected",
            level="warning",
            action="trade_internal_execute",
            result="denied",
            reason="bad_signature",
            source_server=payload_source_server or header_source_server,
            target_server=target_server,
            offer_id=internal_data.offer_id,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal trade signature")

    if (
        not payload_source_server
        or not header_source_server
        or payload_source_server != header_source_server
        or payload_source_server == target_server
    ):
        log_trading_event(
            logger,
            "trade_internal_execute.rejected",
            level="warning",
            action="trade_internal_execute",
            result="denied",
            reason="invalid_source_server",
            source_server=payload_source_server or header_source_server,
            target_server=target_server,
            offer_id=internal_data.offer_id,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal trade source")

    offer = await _resolve_internal_offer_by_public_id(db, offer_public_id=internal_data.offer_public_id)
    if not offer:
        log_trading_event(
            logger,
            "trade_internal_execute.rejected",
            level="warning",
            action="trade_internal_execute",
            result="denied",
            reason="missing_offer_public_id",
            source_server=payload_source_server,
            target_server=target_server,
            offer_id=internal_data.offer_id,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")

    if offer and normalize_server(offer.home_server) != target_server:
        log_trading_event(
            logger,
            "trade_internal_execute.rejected",
            level="warning",
            action="trade_internal_execute",
            result="denied",
            reason="wrong_authoritative_server",
            source_server=payload_source_server,
            target_server=target_server,
            offer_id=internal_data.offer_id,
            status_code=status.HTTP_409_CONFLICT,
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع آفر نیست.")

    resolved_offer_id = offer.id
    expunge_offer = getattr(db, "expunge", None)
    if callable(expunge_offer):
        expunge_offer(offer)

    responder = await db.get(User, internal_data.responder_user_id)
    if not responder or responder.is_deleted:
        log_trading_event(
            logger,
            "trade_internal_execute.rejected",
            level="warning",
            action="trade_internal_execute",
            result="denied",
            reason="missing_responder",
            source_server=payload_source_server,
            target_server=target_server,
            offer_id=internal_data.offer_id,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر درخواست‌دهنده یافت نشد")

    actor_user = responder
    if internal_data.actor_user_id and internal_data.actor_user_id != responder.id:
        actor_user = await db.get(User, internal_data.actor_user_id)
        if not actor_user or actor_user.is_deleted:
            log_trading_event(
                logger,
                "trade_internal_execute.rejected",
                level="warning",
                action="trade_internal_execute",
                result="denied",
                reason="missing_actor",
                source_server=payload_source_server,
                target_server=target_server,
                offer_id=internal_data.offer_id,
                status_code=status.HTTP_404_NOT_FOUND,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر اجراکننده یافت نشد")

    return await _execute_trade_authoritatively_with_transient_retry(
        trade_data=TradeCreate(
            offer_id=resolved_offer_id,
            offer_public_id=internal_data.offer_public_id,
            quantity=internal_data.quantity,
            idempotency_key=internal_data.idempotency_key,
        ),
        background_tasks=background_tasks,
        db=db,
        context=EffectiveOwnerActor(
            owner_user=responder,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=actor_user.id != responder.id,
        ),
        edge_received_at=internal_data.edge_received_at,
        request_source_surface=_normalize_trade_request_surface(internal_data.source_surface),
        request_source_server=payload_source_server,
        request_pre_gated=internal_data.request_pre_gated,
    )


@router.get("/my", response_model=List[TradeResponse])
async def get_my_trades(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    commodity_id: Optional[int] = Query(None, ge=1),
    commodity_query: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات کاربر
    """
    owner_user = context.owner_user
    query = _build_my_trades_query(
        owner_user.id,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    ).order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()

    identity_map = await _load_trade_identity_map(db, list(trades))
    customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
        db,
        [
            raw_user_id
            for trade in trades
            for raw_user_id in (
                getattr(trade, "offer_user_id", None),
                getattr(trade, "responder_user_id", None),
                getattr(trade, "actor_user_id", None),
            )
        ],
        include_inactive_historical=True,
    )
    return [
        trade_to_response(
            t,
            identity_map=identity_map,
            customer_relation_map=customer_relation_map,
            viewer_context=context,
            history_target_user_id=owner_user.id,
        )
        for t in trades
    ]


@router.get("/my/export")
async def export_my_trades(
    format: str = Query(..., pattern="^(excel|pdf)$"),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    commodity_id: Optional[int] = Query(None, ge=1),
    commodity_query: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    owner_user = context.owner_user
    query = _build_my_trades_query(
        owner_user.id,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    ).order_by(Trade.created_at.asc(), Trade.id.asc())
    trades = (await db.execute(query)).scalars().all()
    if not trades:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="معامله‌ای برای خروجی گرفتن یافت نشد.")

    subject_name = _build_trade_history_export_subject_name(current_user=owner_user, target_user=None)
    date_range_label = build_trade_history_date_range_label(from_date, to_date)
    export_rows = build_trade_history_export_rows(trades, owner_user.id)

    if format == "excel":
        output_path = generate_trade_history_excel_file(
            subject_name=subject_name,
            date_range_label=date_range_label,
            rows=export_rows,
        )
        return _build_trade_history_file_response(
            path=output_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=_build_trade_history_download_name(subject_name, "xlsx"),
        )

    output_path = generate_trade_history_pdf_file(
        subject_name=subject_name,
        date_range_label=date_range_label,
        rows=export_rows,
    )
    return _build_trade_history_file_response(
        path=output_path,
        media_type="application/pdf",
        filename=_build_trade_history_download_name(subject_name, "pdf"),
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت جزئیات یک معامله
    """
    result = await db.execute(
        select(Trade).options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity),
            selectinload(Trade.offer),
        ).where(Trade.id == trade_id)
    )
    trade = result.scalar_one_or_none()

    owner_user = context.owner_user
    
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="معامله یافت نشد.")
    
    if not await _viewer_can_access_trade_history_row(db, trade=trade, context=context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما به این معامله دسترسی ندارید.")

    identity_map = await _load_trade_identity_map(db, [trade])
    customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
        db,
        [trade.offer_user_id, trade.responder_user_id, getattr(trade, "actor_user_id", None)],
        include_inactive_historical=True,
    )
    return trade_to_response(
        trade,
        identity_map=identity_map,
        customer_relation_map=customer_relation_map,
        viewer_context=context,
        history_target_user_id=owner_user.id,
    )


@router.get("/with/{other_user_id}", response_model=List[TradeResponse])
async def get_trades_with_user(
    other_user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    commodity_id: Optional[int] = Query(None, ge=1),
    commodity_query: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات با یک کاربر خاص
    """
    owner_user = context.owner_user
    
    # کاربر نمی‌تواند معاملات خودش با خودش را بگیرد (که منطقاً وجود ندارد)
    if other_user_id == owner_user.id:
        return []

    query, _target_customer_relation = await _build_trades_with_user_query(
        db,
        other_user_id=other_user_id,
        context=context,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    )
    query = query.order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()

    identity_map = await _load_trade_identity_map(db, list(trades))
    customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
        db,
        [
            raw_user_id
            for trade in trades
            for raw_user_id in (
                getattr(trade, "offer_user_id", None),
                getattr(trade, "responder_user_id", None),
                getattr(trade, "actor_user_id", None),
            )
        ],
        include_inactive_historical=True,
    )
    return [
        trade_to_response(
            t,
            identity_map=identity_map,
            customer_relation_map=customer_relation_map,
            viewer_context=context,
            history_target_user_id=other_user_id,
        )
        for t in trades
    ]


@router.get("/with/{other_user_id}/export")
async def export_trades_with_user(
    other_user_id: int,
    format: str = Query(..., pattern="^(excel|pdf)$"),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    commodity_id: Optional[int] = Query(None, ge=1),
    commodity_query: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    owner_user = context.owner_user
    if other_user_id == owner_user.id:
        return await export_my_trades(
            format=format,
            from_date=from_date,
            to_date=to_date,
            commodity_id=commodity_id,
            commodity_query=commodity_query,
            db=db,
            context=context,
        )

    query, target_customer_relation = await _build_trades_with_user_query(
        db,
        other_user_id=other_user_id,
        context=context,
        from_date=from_date,
        to_date=to_date,
        commodity_id=commodity_id,
        commodity_query=commodity_query,
    )
    trades = (await db.execute(query.order_by(Trade.created_at.asc(), Trade.id.asc()))).scalars().all()
    if not trades:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="معامله‌ای برای خروجی گرفتن یافت نشد.")

    target_user = await db.get(User, other_user_id)
    subject_name = _build_trade_history_export_subject_name(current_user=owner_user, target_user=target_user)
    date_range_label = build_trade_history_date_range_label(from_date, to_date)
    export_rows = build_trade_history_export_rows(
        trades,
        other_user_id if (target_customer_relation is not None or _is_super_admin_trade_history_viewer(context)) else owner_user.id,
    )

    if format == "excel":
        output_path = generate_trade_history_excel_file(
            subject_name=subject_name,
            date_range_label=date_range_label,
            rows=export_rows,
        )
        return _build_trade_history_file_response(
            path=output_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=_build_trade_history_download_name(subject_name, "xlsx"),
        )

    output_path = generate_trade_history_pdf_file(
        subject_name=subject_name,
        date_range_label=date_range_label,
        rows=export_rows,
    )
    return _build_trade_history_file_response(
        path=output_path,
        media_type="application/pdf",
        filename=_build_trade_history_download_name(subject_name, "pdf"),
    )
