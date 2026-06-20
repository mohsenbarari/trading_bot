# api/routers/offers.py
"""
API Router for Offer Management - Web App Integration
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, or_, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.db import get_db
from core.config import settings
from core.trading_settings import TradingSettings, get_trading_settings
from core.utils import check_user_limits, increment_user_counter, to_jalali_str, utc_now_naive
from core.services.market_transition_service import (
    evaluate_current_market_schedule,
    register_market_offer_created,
)
from core.services.telegram_offer_channel_service import (
    apply_offer_channel_state,
    build_offer_channel_message,
    build_offer_channel_reply_markup,
)
from core.services.telegram_offer_publication_service import publish_offer_to_telegram_channel_once
from core.services.customer_relation_service import (
    build_customer_offer_read_model,
    get_active_customer_relation_for_customer,
    load_offer_customer_read_context,
)
from core.services.user_account_status_service import is_user_market_blocked
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.offer_identity import build_offer_public_link, ensure_offer_public_id, is_offer_public_id_shape
from core.offer_request_policy import (
    OfferRequestVisibility,
    map_legacy_expire_reason,
    sanitize_offer_request_payload,
)
from core.offer_source import OfferSourceSurface
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationValidationError,
    create_authoritative_offer,
)
from core.services.offer_expiry_service import (
    OfferAlreadyInactiveError,
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    OfferNotAuthoritativeError,
    apply_offer_expiry,
    expire_offer_authoritatively,
    expire_offers_authoritatively,
)
from core import telegram_gateway
from core.trade_forwarding import verify_internal_signature
from core.trading_observability import log_trading_event
from models.user import User
from core.enums import UserRole
from models.customer_relation import CustomerTier
from models.offer import Offer, OfferType, OfferStatus
from models.offer_publication_state import OfferPublicationState
from models.offer_request import OfferRequest
from models.trade import Trade, TradeStatus
from models.commodity import Commodity
from api.deps import EffectiveOwnerActor, get_current_user, get_current_user_optional, get_effective_owner_actor_context
from core.server_routing import current_server, is_remote_home, normalize_server


logger = logging.getLogger(__name__)


MARKET_CLOSED_DETAIL = "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید."
ACCOUNTANT_MARKET_BLOCKED_DETAIL = "حسابدار دسترسی به بازار ندارد."
OFFER_STATUS_FILTERS = {
    "active": OfferStatus.ACTIVE,
    "completed": OfferStatus.COMPLETED,
    "cancelled": OfferStatus.CANCELLED,
    "expired": OfferStatus.EXPIRED,
}


def _resolve_offer_owner_context(
    context: EffectiveOwnerActor | None,
    current_user: Optional[User],
) -> EffectiveOwnerActor:
    if hasattr(context, "owner_user") and hasattr(context, "actor_user"):
        return context
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return EffectiveOwnerActor(
        owner_user=current_user,
        actor_user=current_user,
        relation=None,
        is_accountant_context=False,
    )


def _ensure_accountant_market_access_allowed(context: EffectiveOwnerActor) -> None:
    if getattr(context, "is_accountant_context", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNTANT_MARKET_BLOCKED_DETAIL,
        )


def build_offer_read_options(*, include_owner_identity: bool):
    """Return the relationships required by the offer response serializer."""
    options = [selectinload(Offer.commodity)]
    if include_owner_identity:
        options.append(selectinload(Offer.user))
    return tuple(options)


async def _load_offer_idempotency_replay(
    db: AsyncSession,
    *,
    owner_user_id: int,
    idempotency_key: str | None,
) -> Offer | None:
    normalized_key = (idempotency_key or "").strip()
    if not normalized_key:
        return None

    result = await db.execute(
        select(Offer)
        .options(*build_offer_read_options(include_owner_identity=True))
        .where(
            Offer.user_id == owner_user_id,
            Offer.idempotency_key == normalized_key,
        )
    )
    return result.scalar_one_or_none()


router = APIRouter(
    tags=["Offers"],
)


# --- Pydantic Schemas ---

class OfferCreate(BaseModel):
    """ایجاد لفظ جدید"""
    offer_type: str = Field(..., pattern="^(buy|sell)$", description="نوع: buy یا sell")
    commodity_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    price: int = Field(..., gt=0)
    is_wholesale: bool = Field(default=True, description="یکجا یا خُرد")
    lot_sizes: Optional[List[int]] = Field(default=None, description="بخش‌ها برای فروش خُرد")
    notes: Optional[str] = Field(default=None, max_length=200)
    republished_from_id: Optional[int] = Field(default=None, description="شناسه لفظ قدیمی برای تکرار")
    warning_acknowledged: bool = Field(default=False, description="آیا هشدار قیمت غیرعادی توسط کاربر تایید شده است")
    idempotency_key: Optional[str] = Field(default=None, max_length=64, description="شناسه یکتای تلاش ثبت لفظ")


class OfferResponse(BaseModel):
    """پاسخ لفظ"""
    id: int
    offer_public_id: str
    public_link: str
    user_id: Optional[int] = None
    user_account_name: str = ""
    is_own_offer: bool = False
    offer_type: str
    commodity_id: int
    commodity_name: str
    quantity: int
    remaining_quantity: int
    price: int
    raw_price: int
    market_published_price: int
    viewer_effective_price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]]
    original_lot_sizes: Optional[List[int]]
    notes: Optional[str]
    status: str
    expire_reason: Optional[str] = None
    expired_at: Optional[str] = None
    channel_message_id: Optional[int]
    customer_badge_visible: bool = False
    customer_management_name: Optional[str] = None
    customer_tier: Optional[str] = None
    created_at: str
    expires_at_ts: Optional[int] = None
    
    class Config:
        from_attributes = True


class MarketHistoryOfferResponse(OfferResponse):
    """Read-only market history row with terminal-state metadata."""
    history_state: str
    history_label: str
    traded_quantity: int = 0
    is_partially_traded: bool = False
    is_read_only: bool = True
    history_event_at: Optional[str] = None


class PublicOfferResponse(BaseModel):
    """Safe public offer-link payload."""
    offer_public_id: str
    public_link: str
    status: str
    offer_type: str
    commodity_name: str
    quantity: int
    remaining_quantity: int
    price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]]
    notes: Optional[str]
    created_at: str
    expires_at_ts: Optional[int] = None
    safe_public_state_label: str
    interaction_available: bool


class OfferRequestLedgerResponse(BaseModel):
    """Visibility-filtered request ledger row."""
    data: dict[str, Any]


class OfferPublicationStateDetailResponse(BaseModel):
    """Admin-only publication state projection."""
    surface: str
    publication_owner_server: str
    status: str
    surface_resource_id: Optional[str] = None
    telegram_message_id: Optional[int] = None
    error_code: Optional[str] = None
    last_attempt_at: Optional[str] = None
    last_success_at: Optional[str] = None
    lagged_at: Optional[str] = None
    disabled_at: Optional[str] = None


class OfferDetailResponse(BaseModel):
    """Authorized offer detail payload."""
    offer: PublicOfferResponse
    viewer_visibility: str
    request_ledger: List[OfferRequestLedgerResponse]
    request_ledger_limit: int
    request_ledger_offset: int
    expiry_metadata: dict[str, Any]
    publication_states: Optional[List[OfferPublicationStateDetailResponse]] = None


class ParseOfferRequest(BaseModel):
    """درخواست پارس متن لفظ"""
    text: str = Field(..., min_length=3, max_length=500)


class ParseOfferResponse(BaseModel):
    """پاسخ پارس لفظ"""
    success: bool
    error: Optional[str] = None
    data: Optional[dict] = None


class InternalOfferExpireRequest(BaseModel):
    """Internal request to expire an offer on its authoritative home server."""
    offer_id: int = Field(..., gt=0)
    offer_public_id: Optional[str] = None
    owner_user_id: int = Field(..., gt=0)
    actor_user_id: Optional[int] = Field(None, gt=0)
    source_surface: str
    source_server: str
    expire_reason: str


# --- Helper Functions ---

def offer_to_response(
    offer: Offer,
    start_settings: Optional['TradingSettings'] = None,
    *,
    viewer_user_id: Optional[int] = None,
    include_owner_identity: bool = False,
    offer_owner_relation: object | None = None,
    viewer_customer_relation: object | None = None,
) -> OfferResponse:
    """تبدیل مدل Offer به پاسخ API"""
    remaining = offer.remaining_quantity or offer.quantity
    is_own_offer = viewer_user_id is not None and offer.user_id == viewer_user_id
    offer_read_model = build_customer_offer_read_model(
        raw_price=offer.price,
        offer_type=offer.offer_type,
        viewer_user_id=viewer_user_id,
        offer_owner_relation=offer_owner_relation,
        viewer_customer_relation=viewer_customer_relation,
    )
    offer_public_id = ensure_offer_public_id(offer)

    expires_at_ts = None
    logger.debug("offer_expiry_trace id=%s status=%s", offer.id, offer.status)
    if offer.status == OfferStatus.ACTIVE:
        try:
            ts = start_settings or get_trading_settings()
            created_ts = offer.created_at.timestamp()
            expiry_seconds = ts.offer_expiry_minutes * 60
            expires_at_ts = int(created_ts + expiry_seconds)
            logger.debug("offer_expiry_result id=%s expires_at_ts=%s", offer.id, expires_at_ts)
        except Exception as exc:
            logger.error(
                "offer_expiry_projection_failed",
                extra={"offer_id": getattr(offer, "id", None), "error_class": type(exc).__name__},
            )

    return OfferResponse(
        id=offer.id,
        offer_public_id=offer_public_id,
        public_link=build_offer_public_link(offer_public_id),
        user_id=offer.user_id if include_owner_identity else None,
        user_account_name=(offer.user.account_name if include_owner_identity and offer.user else ""),
        is_own_offer=is_own_offer,
        offer_type=offer.offer_type.value,
        commodity_id=offer.commodity_id,
        commodity_name=offer.commodity.name if offer.commodity else "نامشخص",
        quantity=offer.quantity,
        remaining_quantity=remaining,
        price=offer_read_model.market_published_price,
        raw_price=offer_read_model.raw_price,
        market_published_price=offer_read_model.market_published_price,
        viewer_effective_price=offer_read_model.viewer_effective_price,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
        original_lot_sizes=offer.original_lot_sizes,
        notes=offer.notes,
        status=offer.status.value,
        expire_reason=getattr(offer, "expire_reason", None),
        expired_at=to_jalali_str(getattr(offer, "expired_at", None)) if getattr(offer, "expired_at", None) else None,
        channel_message_id=offer.channel_message_id,
        customer_badge_visible=offer_read_model.customer_badge_visible,
        customer_management_name=offer_read_model.customer_management_name,
        customer_tier=offer_read_model.customer_tier,
        created_at=to_jalali_str(offer.created_at) or "",
        expires_at_ts=expires_at_ts,
    )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _safe_public_state_label(offer: Any) -> str:
    status_value = _enum_value(getattr(offer, "status", None))
    if status_value == OfferStatus.ACTIVE.value:
        return "active"
    if status_value == OfferStatus.COMPLETED.value:
        return "traded"
    if status_value == OfferStatus.EXPIRED.value:
        return "expired"
    if status_value == OfferStatus.CANCELLED.value:
        return "cancelled"
    return status_value or "unknown"


def _offer_remaining_quantity(offer: Any) -> int:
    remaining = getattr(offer, "remaining_quantity", None)
    if remaining is None:
        remaining = getattr(offer, "quantity", 0)
    try:
        return int(remaining or 0)
    except (TypeError, ValueError):
        return 0


def _build_public_offer_response(
    offer: Offer,
    *,
    start_settings: Optional['TradingSettings'] = None,
) -> PublicOfferResponse:
    offer_public_id = ensure_offer_public_id(offer)
    status_value = _enum_value(getattr(offer, "status", None))
    remaining_quantity = _offer_remaining_quantity(offer)
    expires_at_ts = None
    if status_value == OfferStatus.ACTIVE.value:
        try:
            ts = start_settings or get_trading_settings()
            expires_at_ts = int(offer.created_at.timestamp() + ts.offer_expiry_minutes * 60)
        except Exception as exc:
            logger.error(
                "public_offer_expiry_projection_failed",
                extra={"offer_id": getattr(offer, "id", None), "error_class": type(exc).__name__},
            )

    commodity = getattr(offer, "commodity", None)
    return PublicOfferResponse(
        offer_public_id=offer_public_id,
        public_link=build_offer_public_link(offer_public_id),
        status=status_value,
        offer_type=_enum_value(getattr(offer, "offer_type", None)),
        commodity_name=getattr(commodity, "name", None) or "نامشخص",
        quantity=int(getattr(offer, "quantity", 0) or 0),
        remaining_quantity=remaining_quantity,
        price=int(getattr(offer, "price", 0) or 0),
        is_wholesale=bool(getattr(offer, "is_wholesale", True)),
        lot_sizes=getattr(offer, "lot_sizes", None),
        notes=getattr(offer, "notes", None),
        created_at=to_jalali_str(getattr(offer, "created_at", None)) or "",
        expires_at_ts=expires_at_ts,
        safe_public_state_label=_safe_public_state_label(offer),
        interaction_available=status_value == OfferStatus.ACTIVE.value and remaining_quantity > 0,
    )


def _offer_request_payload(ledger: OfferRequest) -> dict[str, Any]:
    commission_rate = getattr(ledger, "customer_commission_rate_snapshot", None)
    return {
        "id": getattr(ledger, "id", None),
        "version_id": getattr(ledger, "version_id", None),
        "request_home_server": getattr(ledger, "request_home_server", None),
        "local_offer_id": getattr(ledger, "local_offer_id", None),
        "offer_public_id": getattr(ledger, "offer_public_id", None),
        "requester_user_id": getattr(ledger, "requester_user_id", None),
        "actor_user_id": getattr(ledger, "actor_user_id", None),
        "request_source_surface": _enum_value(getattr(ledger, "request_source_surface", None)),
        "request_source_server": getattr(ledger, "request_source_server", None),
        "requested_quantity": getattr(ledger, "requested_quantity", None),
        "idempotency_key": getattr(ledger, "idempotency_key", None),
        "received_at": to_jalali_str(getattr(ledger, "received_at", None)) if getattr(ledger, "received_at", None) else None,
        "decided_at": to_jalali_str(getattr(ledger, "decided_at", None)) if getattr(ledger, "decided_at", None) else None,
        "result_status": _enum_value(getattr(ledger, "result_status", None)),
        "public_failure_code": getattr(ledger, "public_failure_code", None),
        "public_failure_message": getattr(ledger, "public_failure_message", None),
        "internal_failure_code": getattr(ledger, "internal_failure_code", None),
        "internal_failure_context": getattr(ledger, "internal_failure_context", None),
        "resulting_trade_id": getattr(ledger, "resulting_trade_id", None),
        "customer_relation_id": getattr(ledger, "customer_relation_id", None),
        "customer_owner_user_id": getattr(ledger, "customer_owner_user_id", None),
        "customer_tier_snapshot": getattr(ledger, "customer_tier_snapshot", None),
        "customer_management_name_snapshot": getattr(ledger, "customer_management_name_snapshot", None),
        "customer_commission_rate_snapshot": str(commission_rate) if commission_rate is not None else None,
        "customer_commission_context": getattr(ledger, "customer_commission_context", None),
        "archived": getattr(ledger, "archived", None),
        "created_at": to_jalali_str(getattr(ledger, "created_at", None)) if getattr(ledger, "created_at", None) else None,
        "updated_at": to_jalali_str(getattr(ledger, "updated_at", None)) if getattr(ledger, "updated_at", None) else None,
    }


def _is_offer_detail_admin(user: User) -> bool:
    role = getattr(user, "role", None)
    return role in {UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER}


async def _resolve_offer_detail_visibility(
    db: AsyncSession,
    offer: Offer,
    current_user: Optional[User],
) -> tuple[OfferRequestVisibility, bool]:
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if _is_offer_detail_admin(current_user):
        return OfferRequestVisibility.ADMIN_AUDIT, True
    if getattr(offer, "user_id", None) == current_user.id:
        return OfferRequestVisibility.OWNER, True

    result = await db.execute(
        select(OfferRequest.id)
        .where(
            OfferRequest.offer_public_id == offer.offer_public_id,
            or_(
                OfferRequest.requester_user_id == current_user.id,
                OfferRequest.actor_user_id == current_user.id,
            ),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return OfferRequestVisibility.REQUESTER, False
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view offer detail")


def _build_expiry_metadata(offer: Offer) -> dict[str, Any]:
    mapping = map_legacy_expire_reason(getattr(offer, "expire_reason", None))
    return {
        "status": _enum_value(getattr(offer, "status", None)),
        "expire_reason": getattr(offer, "expire_reason", None),
        "normalized_category": mapping.normalized_category,
        "metadata_known": mapping.metadata_known,
        "expired_at": to_jalali_str(getattr(offer, "expired_at", None)) if getattr(offer, "expired_at", None) else None,
        "expired_by_user_id": getattr(offer, "expired_by_user_id", None),
        "expired_by_actor_user_id": getattr(offer, "expired_by_actor_user_id", None),
        "expire_source_surface": getattr(offer, "expire_source_surface", None) or mapping.default_source_surface,
        "expire_source_server": getattr(offer, "expire_source_server", None),
    }


def _publication_state_detail(state: OfferPublicationState) -> OfferPublicationStateDetailResponse:
    return OfferPublicationStateDetailResponse(
        surface=_enum_value(getattr(state, "surface", None)),
        publication_owner_server=getattr(state, "publication_owner_server", None),
        status=_enum_value(getattr(state, "status", None)),
        surface_resource_id=getattr(state, "surface_resource_id", None),
        telegram_message_id=getattr(state, "telegram_message_id", None),
        error_code=getattr(state, "error_code", None),
        last_attempt_at=to_jalali_str(getattr(state, "last_attempt_at", None)) if getattr(state, "last_attempt_at", None) else None,
        last_success_at=to_jalali_str(getattr(state, "last_success_at", None)) if getattr(state, "last_success_at", None) else None,
        lagged_at=to_jalali_str(getattr(state, "lagged_at", None)) if getattr(state, "lagged_at", None) else None,
        disabled_at=to_jalali_str(getattr(state, "disabled_at", None)) if getattr(state, "disabled_at", None) else None,
    )


async def _load_offer_by_public_id(db: AsyncSession, offer_public_id: str) -> Offer | None:
    if not is_offer_public_id_shape(offer_public_id):
        return None
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(Offer.offer_public_id == offer_public_id)
    )
    return result.scalar_one_or_none()


async def _serialize_offer_responses(
    offers: List[Offer],
    *,
    db: AsyncSession,
    start_settings: Optional['TradingSettings'] = None,
    viewer_user_id: Optional[int] = None,
    include_owner_identity: bool = False,
) -> List[OfferResponse]:
    if not offers:
        return []

    offer_owner_relation_map, viewer_customer_relation = await load_offer_customer_read_context(
        db,
        offer_owner_user_ids={offer.user_id for offer in offers if getattr(offer, "user_id", None) is not None},
        viewer_user_id=viewer_user_id,
    )
    return [
        offer_to_response(
            offer,
            start_settings,
            viewer_user_id=viewer_user_id,
            include_owner_identity=include_owner_identity,
            offer_owner_relation=offer_owner_relation_map.get(getattr(offer, "user_id", None)),
            viewer_customer_relation=viewer_customer_relation,
        )
        for offer in offers
    ]


def _offer_response_to_dict(response: OfferResponse) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    return response.dict()


def _build_market_history_response(
    response: OfferResponse,
    offer: Offer,
    *,
    traded_quantity: int,
    history_event_at: datetime | None,
) -> MarketHistoryOfferResponse:
    normalized_traded_quantity = max(0, int(traded_quantity or 0))
    is_traded_history = offer.status == OfferStatus.COMPLETED or normalized_traded_quantity > 0
    history_state = "traded" if is_traded_history else "expired"
    history_label = "معامله‌شده" if is_traded_history else "منقضی"
    original_quantity = int(getattr(offer, "quantity", 0) or 0)
    is_partially_traded = (
        offer.status == OfferStatus.EXPIRED
        and normalized_traded_quantity > 0
        and (original_quantity <= 0 or normalized_traded_quantity < original_quantity)
    )
    return MarketHistoryOfferResponse(
        **_offer_response_to_dict(response),
        history_state=history_state,
        history_label=history_label,
        traded_quantity=normalized_traded_quantity,
        is_partially_traded=is_partially_traded,
        is_read_only=True,
        history_event_at=to_jalali_str(history_event_at) if history_event_at else None,
    )


async def _read_active_offer_count(db: AsyncSession, user_id: int) -> int:
    count = await db.scalar(
        select(func.count(Offer.id)).where(
            Offer.user_id == user_id,
            Offer.status == OfferStatus.ACTIVE,
        )
    )
    return int(count or 0)


async def _cached_active_offer_count(user_id: int) -> Optional[int]:
    try:
        from core.cache import get_active_offer_count

        return await get_active_offer_count(user_id)
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_active_count_cache_read_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="active_offer_count_cache",
            reason="create_offer_guard_repair",
            error_class=type(exc).__name__,
        )
        return None


async def _set_active_offer_count_safely(user_id: int, count: int, *, reason: str) -> None:
    try:
        from core.cache import set_active_offer_count

        await set_active_offer_count(user_id, max(0, int(count)))
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_active_count_cache_write_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="active_offer_count_cache",
            reason=reason,
            error_class=type(exc).__name__,
        )


async def _publish_offer_event_safely(event_type: str, payload: dict, *, reason: str) -> None:
    try:
        from .realtime import publish_event

        await publish_event(event_type, payload)
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_lifecycle_realtime_publish_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="realtime_publish",
            offer_id=payload.get("id"),
            reason=reason,
            error_class=type(exc).__name__,
        )


async def _remove_offer_channel_buttons_safely(offer: Offer, *, reason: str, timeout: float = 10) -> None:
    if current_server() != "foreign":
        return

    if not getattr(offer, "channel_message_id", None):
        return

    try:
        await apply_offer_channel_state(offer, reason=reason, timeout=timeout)
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_channel_state_apply_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="offer_channel_state_apply",
            offer_id=getattr(offer, "id", None),
            reason=reason,
            error_class=type(exc).__name__,
        )


async def _expire_offer_side_effects(
    db: AsyncSession,
    offer: Offer,
    *,
    realtime_reason: str,
    channel_reason: str,
    channel_timeout: float,
    update_active_count: bool = True,
) -> None:
    await _remove_offer_channel_buttons_safely(offer, reason=channel_reason, timeout=channel_timeout)
    await _publish_offer_event_safely("offer:expired", {"id": offer.id}, reason=realtime_reason)
    if update_active_count and getattr(offer, "user_id", None):
        active_count = await _read_active_offer_count(db, offer.user_id)
        await _set_active_offer_count_safely(offer.user_id, active_count, reason=realtime_reason)


async def send_offer_to_channel(offer: Offer, user: User) -> Optional[int]:
    """ارسال لفظ به کانال تلگرام و برگرداندن message_id"""
    if current_server() != "foreign":
        return None

    channel_id = settings.channel_id
    
    if not channel_id:
        return None
    
    try:
        result = await telegram_gateway.send_message(
            channel_id,
            build_offer_channel_message(offer),
            reply_markup=build_offer_channel_reply_markup(offer),
            idempotency_key=f"offer-channel-send:{getattr(offer, 'id', '')}",
        )
        if result.ok:
            return result.message_id
        log_trading_event(
            logger,
            "offer_channel_send_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            offer_id=getattr(offer, "id", None),
            status_code=result.status_code,
            error=result.error,
        )
    except Exception as exc:
        log_trading_event(
            logger,
            "offer_channel_send_failed",
            level="error",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            offer_id=getattr(offer, "id", None),
            error_class=type(exc).__name__,
        )
    
    return None


def _normalize_internal_offer_source(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = normalize_server(value, default="")
    return normalized if normalized in {"iran", "foreign"} else None


def _build_offer_expiry_forward_payload(
    offer: Offer,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
) -> dict:
    return {
        "offer_id": offer.id,
        "offer_public_id": getattr(offer, "offer_public_id", None),
        "owner_user_id": owner_user_id,
        "actor_user_id": actor_user_id,
        "source_surface": getattr(source_surface, "value", source_surface),
        "source_server": current_server(),
        "expire_reason": expire_reason,
    }


async def _forward_offer_expiry_if_remote_home(
    offer: Offer,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
) -> Response | None:
    if not is_remote_home(getattr(offer, "home_server", None)):
        return None

    payload = _build_offer_expiry_forward_payload(
        offer,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        expire_reason=expire_reason,
    )
    status_code, body = await forward_offer_expiry_to_home_server(offer.home_server, payload)
    if status_code < 400:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(status_code=status_code, content=body)


async def _forward_offer_expiry_for_cancel_all(
    offer: Offer,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
) -> bool:
    payload = _build_offer_expiry_forward_payload(
        offer,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        expire_reason=expire_reason,
    )
    status_code, _body = await forward_offer_expiry_to_home_server(offer.home_server, payload)
    return status_code < 400


# --- Endpoints ---

@router.post("/", response_model=OfferResponse, status_code=status.HTTP_201_CREATED)
async def create_offer(
    offer_data: OfferCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    ثبت لفظ جدید از MiniApp
    - بررسی محدودیت‌ها
    - ذخیره در دیتابیس
    - ارسال به کانال تلگرام
    """
    from core.enums import UserRole
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    actor_user = context.actor_user
    idempotency_key = (offer_data.idempotency_key or "").strip() or None

    existing_offer = await _load_offer_idempotency_replay(
        db,
        owner_user_id=owner_user.id,
        idempotency_key=idempotency_key,
    )
    if existing_offer is not None:
        from core.trading_settings import get_trading_settings_async

        ts = await get_trading_settings_async()
        log_trading_event(
            logger,
            "offer_idempotent_replay",
            action="offer_idempotent_replay",
            result="replay",
            offer_id=getattr(existing_offer, "id", None),
            has_idempotency_key=bool(idempotency_key),
        )
        return offer_to_response(existing_offer, ts, viewer_user_id=owner_user.id, include_owner_identity=True)
    
    # بررسی نقش
    if owner_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )

    if is_user_market_blocked(owner_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="حساب شما غیرفعال است و دسترسی شما به بازار بسته شده است.",
        )

    market_evaluation = await evaluate_current_market_schedule(db)
    if not market_evaluation.is_open:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MARKET_CLOSED_DETAIL,
        )
    
    # بررسی مسدودیت
    if owner_user.trading_restricted_until:
        if owner_user.trading_restricted_until > utc_now_naive():
            remaining = owner_user.trading_restricted_until - utc_now_naive()
            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(owner_user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"حساب شما مسدود است.\n📅 رفع مسدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
            )
    
    # بررسی محدودیت ارسال لفظ
    allowed, error_msg = check_user_limits(owner_user, 'channel_message')
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # بررسی محدودیت معامله و کالا
    allowed, error_msg = check_user_limits(owner_user, 'trade', offer_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)

    actor_customer_relation = await get_active_customer_relation_for_customer(db, actor_user.id)
    if actor_customer_relation and actor_customer_relation.customer_tier == CustomerTier.TIER_2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="مشتری سطح 2 مجاز به ثبت لفظ نیست و فقط می‌تواند روی لفظ‌های دیگر درخواست بزند.",
        )
    
    # بررسی تعداد لفظ‌های فعال
    ts = get_trading_settings()

    old_offer = None
    republishing_active_offer = False
    if offer_data.republished_from_id:
        old_offer = await db.get(Offer, offer_data.republished_from_id)
        if old_offer and old_offer.user_id == owner_user.id:
            republishing_active_offer = old_offer.status == OfferStatus.ACTIVE

    active_count = await _read_active_offer_count(db, owner_user.id)
    cached_active_count = await _cached_active_offer_count(owner_user.id)
    if cached_active_count != active_count:
        await _set_active_offer_count_safely(owner_user.id, active_count, reason="create_offer_guard_repair")

    effective_active_count = max(0, active_count - 1) if republishing_active_offer else active_count
    if effective_active_count >= ts.max_active_offers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"شما حداکثر {ts.max_active_offers} لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید."
        )
    
    # بررسی وجود کالا
    commodity = await db.get(Commodity, offer_data.commodity_id)
    if not commodity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کالا یافت نشد.")

    # ===== اعتبارسنجی‌های مشترک (Shared Service) =====
    from core.services.trade_service import (
        detect_offer_price_warning,
        validate_quantity,
        validate_price,
        validate_lot_sizes,
    )
    
    # 1. اعتبارسنجی تعداد
    is_valid_qty, err_qty = validate_quantity(offer_data.quantity)
    if not is_valid_qty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_qty)

    # 2. اعتبارسنجی قیمت
    is_valid_price, err_price = validate_price(offer_data.price)
    if not is_valid_price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_price)
    
    # 3. اعتبارسنجی لات‌ها
    if not offer_data.is_wholesale:
        if not offer_data.lot_sizes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="برای آفر خُرد باید لات‌ها مشخص شوند.")
        is_valid_lots, err_lots, suggested_lots = validate_lot_sizes(offer_data.quantity, offer_data.lot_sizes)
        if not is_valid_lots:
            detail_msg = err_lots
            # اگر پیشنهاد اصلاح دارد، آن را به کاربر نشان می‌دهیم
            if suggested_lots:
                # اینجا می‌توانیم پیشنهاد را در قالب خاصی بفرستیم، اما فعلاً در متن خطا می‌گذاریم
                pass 
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail_msg)
    
    # 4. اعتبارسنجی قیمت رقابتی
    from core.services.trade_service import validate_competitive_price
    is_valid_comp, err_comp = await validate_competitive_price(
        db=db,
        offer_type=offer_data.offer_type,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        proposed_price=offer_data.price,
        user_id=owner_user.id
    )
    if not is_valid_comp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_comp)

    price_warning = await detect_offer_price_warning(
        db=db,
        offer_type=offer_data.offer_type,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        proposed_price=offer_data.price,
        user_id=owner_user.id,
    )
    if price_warning and not offer_data.warning_acknowledged:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error_code": price_warning["error_code"],
                "detail": price_warning["detail"],
                "warning": price_warning,
            },
        )

    # مدیریت تکرار لفظ قدیمی
    if old_offer and old_offer.user_id == owner_user.id:
        # اگر لفظ قبلی هنوز فعال باشد، آن را منقضی می‌کنیم
        if old_offer.status == OfferStatus.ACTIVE:
            apply_offer_expiry(
                old_offer,
                OfferExpiryCommand(
                    reason=OfferExpiryReason.REPUBLISHED,
                    source_surface=OfferExpirySourceSurface.WEBAPP,
                    source_server=current_server(),
                    expired_by_user_id=owner_user.id,
                    expired_by_actor_user_id=actor_user.id,
                ),
                require_authority=False,
            )

    try:
        new_offer = await create_authoritative_offer(
            db,
            OfferCreationCommand(
                source_surface=OfferSourceSurface.WEBAPP,
                owner_user_id=owner_user.id,
                actor_user_id=actor_user.id,
                offer_type=OfferType.BUY if offer_data.offer_type == "buy" else OfferType.SELL,
                commodity_id=offer_data.commodity_id,
                quantity=offer_data.quantity,
                price=offer_data.price,
                exclude_from_competitive_price=bool(price_warning),
                price_warning_type=price_warning["warning_type"] if price_warning else None,
                is_wholesale=offer_data.is_wholesale,
                lot_sizes=offer_data.lot_sizes,
                original_lot_sizes=offer_data.lot_sizes,
                notes=offer_data.notes,
                idempotency_key=idempotency_key,
                status=OfferStatus.ACTIVE,
            ),
        )
    except OfferCreationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError:
        await db.rollback()
        existing_offer = await _load_offer_idempotency_replay(
            db,
            owner_user_id=owner_user.id,
            idempotency_key=idempotency_key,
        )
        if existing_offer is None:
            raise
        from core.trading_settings import get_trading_settings_async

        ts = await get_trading_settings_async()
        log_trading_event(
            logger,
            "offer_idempotent_replay_after_integrity_conflict",
            action="offer_idempotent_replay",
            result="replay",
            offer_id=getattr(existing_offer, "id", None),
            has_idempotency_key=bool(idempotency_key),
            reason="idempotency_integrity_conflict",
        )
        return offer_to_response(existing_offer, ts, viewer_user_id=owner_user.id, include_owner_identity=True)

    # لینک کردن لفظ قدیمی به جدید
    if offer_data.republished_from_id:
        # دوباره لود می‌کنیم چون ممکن است سشن بسته شده باشد یا نیاز به اتچ مجدد باشد، اما اینجا سشن باز است
        # فقط باید مطمئن شویم old_offer در سشن است
        if old_offer and old_offer.user_id == owner_user.id:
            old_offer.republished_offer_id = new_offer.id
            await db.commit()
    
    # بارگذاری روابط
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.user), selectinload(Offer.commodity))
        .where(Offer.id == new_offer.id)
    )
    new_offer = result.scalar_one()
    
    # ارسال idempotent به کانال تلگرام و ثبت نتیجه در publication-state
    publish_result = await publish_offer_to_telegram_channel_once(
        db,
        new_offer,
        owner_user,
        send_offer_to_channel=send_offer_to_channel,
    )
    if publish_result.message_id:
        new_offer.channel_message_id = publish_result.message_id
        await db.commit()

    log_trading_event(
        logger,
        "offer_create.accepted",
        action="offer_create",
        result="success",
        offer_id=getattr(new_offer, "id", None),
        source_server=getattr(new_offer, "home_server", None) or current_server(),
        has_idempotency_key=bool(idempotency_key),
    )
    
    expected_active_count = active_count if republishing_active_offer else active_count + 1
    await _set_active_offer_count_safely(owner_user.id, expected_active_count, reason="create_offer_final")
    
    # افزایش شمارنده
    await increment_user_counter(db, owner_user, 'channel_message')

    await register_market_offer_created(db)
    
    # دریافت تنظیمات برای محاسبه انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    # محاسبه expires_at_ts برای SSE event
    sse_expires_at_ts = None
    try:
        created_ts = new_offer.created_at.timestamp()
        sse_expires_at_ts = int(created_ts + ts.offer_expiry_minutes * 60)
    except Exception:
        pass
    
    if republishing_active_offer and old_offer:
        await _remove_offer_channel_buttons_safely(old_offer, reason="republish_old_offer", timeout=10)
        await _publish_offer_event_safely("offer:expired", {"id": old_offer.id}, reason="republish_old_offer")

    await _publish_offer_event_safely("offer:created", {
        "id": new_offer.id,
        "offer_public_id": new_offer.offer_public_id,
        "public_link": build_offer_public_link(new_offer.offer_public_id),
        "user_id": None,
        "offer_type": new_offer.offer_type.value,
        "commodity_id": new_offer.commodity_id,
        "commodity_name": new_offer.commodity.name,
        "quantity": new_offer.quantity,
        "remaining_quantity": new_offer.remaining_quantity or new_offer.quantity,
        "price": new_offer.price,
        "status": new_offer.status.value,
        "created_at": to_jalali_str(new_offer.created_at) or "",
        "user_account_name": "",
        "is_own_offer": False,
        "notes": new_offer.notes,
        "is_wholesale": new_offer.is_wholesale,
        "lot_sizes": new_offer.lot_sizes,
        "original_lot_sizes": new_offer.original_lot_sizes,
        "expires_at_ts": sse_expires_at_ts,
    }, reason="create_offer")

    if not republishing_active_offer:
        try:
            from core.web_push import schedule_market_offer_web_push

            schedule_market_offer_web_push(new_offer.id)
        except Exception as exc:
            log_trading_event(
                logger,
                "market_offer_web_push_schedule_failed",
                level="warning",
                action="trading_side_effect",
                result="failure",
                side_effect="web_push_schedule",
                offer_id=getattr(new_offer, "id", None),
                error_class=type(exc).__name__,
            )
    
    return offer_to_response(new_offer, ts, viewer_user_id=owner_user.id, include_owner_identity=True)


@router.get("/public/{offer_public_id}", response_model=PublicOfferResponse)
async def get_public_offer_by_public_id(
    offer_public_id: str,
    db: AsyncSession = Depends(get_db),
):
    offer = await _load_offer_by_public_id(db, offer_public_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")

    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    return _build_public_offer_response(offer, start_settings=ts)


@router.get("/public/{offer_public_id}/detail", response_model=OfferDetailResponse)
async def get_public_offer_detail(
    offer_public_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    offer = await _load_offer_by_public_id(db, offer_public_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")

    visibility, include_all_ledger = await _resolve_offer_detail_visibility(db, offer, current_user)
    ledger_query = (
        select(OfferRequest)
        .where(OfferRequest.offer_public_id == offer.offer_public_id)
        .order_by(OfferRequest.received_at.desc(), OfferRequest.id.desc())
        .offset(offset)
        .limit(limit)
    )
    if not include_all_ledger:
        ledger_query = ledger_query.where(
            or_(
                OfferRequest.requester_user_id == current_user.id,
                OfferRequest.actor_user_id == current_user.id,
            )
        )

    ledger_result = await db.execute(ledger_query)
    request_ledger = [
        OfferRequestLedgerResponse(
            data=sanitize_offer_request_payload(_offer_request_payload(ledger), visibility)
        )
        for ledger in ledger_result.scalars().all()
    ]

    publication_states = None
    if visibility == OfferRequestVisibility.ADMIN_AUDIT:
        publication_result = await db.execute(
            select(OfferPublicationState)
            .where(OfferPublicationState.offer_public_id == offer.offer_public_id)
            .order_by(OfferPublicationState.surface.asc(), OfferPublicationState.id.asc())
        )
        publication_states = [
            _publication_state_detail(state_row)
            for state_row in publication_result.scalars().all()
        ]

    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    return OfferDetailResponse(
        offer=_build_public_offer_response(offer, start_settings=ts),
        viewer_visibility=visibility.value,
        request_ledger=request_ledger,
        request_ledger_limit=limit,
        request_ledger_offset=offset,
        expiry_metadata=_build_expiry_metadata(offer),
        publication_states=publication_states,
    )


@router.get("/", response_model=List[OfferResponse])
async def get_active_offers(
    offer_type: Optional[str] = Query(None, pattern="^(buy|sell)$"),
    commodity_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    دریافت لیست لفظ‌های فعال
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user

    query = select(Offer).options(
        *build_offer_read_options(include_owner_identity=False)
    ).where(Offer.status == OfferStatus.ACTIVE)
    
    if offer_type:
        query = query.where(Offer.offer_type == (OfferType.BUY if offer_type == "buy" else OfferType.SELL))
    
    if commodity_id:
        query = query.where(Offer.commodity_id == commodity_id)
    
    query = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    offers = result.scalars().all()
    if not offers:
        return []
    
    # دریافت تنظیمات برای محاسبه دقیق انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=ts,
        viewer_user_id=owner_user.id,
        include_owner_identity=False,
    )


@router.get("/market-history", response_model=List[MarketHistoryOfferResponse])
async def get_market_offer_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    since_hours: int = Query(48, ge=1, le=48),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    Read-only two-day market history for terminal offers.

    This endpoint intentionally does not alter active-market feed, fair-price
    validation, Web Push targeting, or trade execution guards.
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)

    actor_relation = await get_active_customer_relation_for_customer(db, context.actor_user.id)
    if actor_relation is not None:
        return []

    cutoff_time = utc_now_naive() - timedelta(hours=since_hours)
    expired_at_expr = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)
    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    expiry_minutes = int(getattr(ts, "offer_expiry_minutes", 0) or 0)

    trade_totals = (
        select(
            Trade.offer_id.label("offer_id"),
            func.coalesce(func.sum(Trade.quantity), 0).label("traded_quantity"),
            func.max(Trade.created_at).label("last_trade_at"),
        )
        .where(
            Trade.offer_id.isnot(None),
            Trade.status == TradeStatus.COMPLETED,
        )
        .group_by(Trade.offer_id)
        .subquery()
    )

    traded_quantity_expr = func.coalesce(trade_totals.c.traded_quantity, 0)
    completed_event_at_expr = func.coalesce(
        trade_totals.c.last_trade_at,
        Offer.updated_at,
        Offer.created_at,
    )
    active_stale_event_at_expr = Offer.created_at + timedelta(minutes=expiry_minutes)
    history_event_at_expr = case(
        (Offer.status == OfferStatus.COMPLETED, completed_event_at_expr),
        (Offer.status == OfferStatus.ACTIVE, active_stale_event_at_expr),
        else_=expired_at_expr,
    )
    history_conditions = [
        and_(
            Offer.status == OfferStatus.COMPLETED,
            completed_event_at_expr >= cutoff_time,
        ),
        and_(
            Offer.status == OfferStatus.EXPIRED,
            or_(
                Offer.expire_reason == "time_limit",
                traded_quantity_expr > 0,
            ),
            expired_at_expr >= cutoff_time,
        ),
    ]
    if expiry_minutes > 0:
        stale_cutoff_time = utc_now_naive() - timedelta(minutes=expiry_minutes)
        active_stale_created_after = cutoff_time - timedelta(minutes=expiry_minutes)
        history_conditions.append(
            and_(
                Offer.status == OfferStatus.ACTIVE,
                Offer.created_at <= stale_cutoff_time,
                Offer.created_at >= active_stale_created_after,
                traded_quantity_expr == 0,
            )
        )

    query = (
        select(
            Offer,
            traded_quantity_expr.label("traded_quantity"),
            history_event_at_expr.label("history_event_at"),
        )
        .outerjoin(trade_totals, trade_totals.c.offer_id == Offer.id)
        .options(*build_offer_read_options(include_owner_identity=False))
        .where(or_(*history_conditions))
        .order_by(history_event_at_expr.desc(), Offer.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()
    if not rows:
        return []

    offers = [row[0] for row in rows]
    serialized = await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=ts,
        viewer_user_id=context.owner_user.id,
        include_owner_identity=False,
    )
    return [
        _build_market_history_response(
            response,
            offer,
            traded_quantity=row[1],
            history_event_at=row[2],
        )
        for response, offer, row in zip(serialized, offers, rows)
    ]


@router.get("/expired", response_model=List[OfferResponse])
async def get_market_expired_offers(
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    since_hours: int = Query(48, ge=1, le=48),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    Read-only market history for offers that died naturally by time limit.

    This endpoint intentionally does not change the active market feed contract,
    price-warning comparisons, Web Push targeting, or trade execution guards.
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)

    actor_relation = await get_active_customer_relation_for_customer(db, context.actor_user.id)
    if actor_relation is not None:
        return []

    cutoff_time = utc_now_naive() - timedelta(hours=since_hours)
    expired_at_expr = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)

    query = (
        select(Offer)
        .options(*build_offer_read_options(include_owner_identity=False))
        .where(
            Offer.status == OfferStatus.EXPIRED,
            Offer.expire_reason == "time_limit",
            expired_at_expr >= cutoff_time,
        )
        .order_by(expired_at_expr.desc(), Offer.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    offers = result.scalars().all()
    if not offers:
        return []

    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    return await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=ts,
        viewer_user_id=context.owner_user.id,
        include_owner_identity=False,
    )


@router.get("/my", response_model=List[OfferResponse])
async def get_my_offers(
    status_filter: Optional[str] = Query(None, pattern="^(active|completed|cancelled|expired)$"),
    since_hours: Optional[int] = Query(None, gt=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    دریافت لفظ‌های کاربر
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user

    query = select(Offer).options(
        *build_offer_read_options(include_owner_identity=True)
    ).where(Offer.user_id == owner_user.id)
    
    # فیلتر کردن لفظ‌هایی که دوباره منتشر شده‌اند
    query = query.where(Offer.republished_offer_id.is_(None))
    
    applied_status_enum = None
    start_settings = None

    if status_filter:
        applied_status_enum = OFFER_STATUS_FILTERS.get(status_filter)
        if applied_status_enum:
            if not (applied_status_enum == OfferStatus.EXPIRED and since_hours):
                query = query.where(Offer.status == applied_status_enum)

    if since_hours:
        cutoff_time = utc_now_naive() - timedelta(hours=since_hours)

        if applied_status_enum == OfferStatus.EXPIRED:
            from core.trading_settings import get_trading_settings_async

            start_settings = await get_trading_settings_async()
            recent_expired_at = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)
            expired_conditions = [
                and_(Offer.status == OfferStatus.EXPIRED, recent_expired_at >= cutoff_time)
            ]
            expiry_minutes = int(getattr(start_settings, "offer_expiry_minutes", 0) or 0)
            if expiry_minutes > 0:
                stale_cutoff_time = utc_now_naive() - timedelta(minutes=expiry_minutes)
                recent_active_created_after = cutoff_time - timedelta(minutes=expiry_minutes)
                expired_conditions.append(
                    and_(
                        Offer.status == OfferStatus.ACTIVE,
                        Offer.created_at < stale_cutoff_time,
                        Offer.created_at >= recent_active_created_after,
                    )
                )
            query = query.where(or_(*expired_conditions))
        else:
            query = query.where(Offer.created_at >= cutoff_time)
    
    # مرتب‌سازی: جدیدترین‌ها اول
    if applied_status_enum == OfferStatus.EXPIRED:
        recent_expired_at = func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at)
        query = query.order_by(recent_expired_at.desc(), Offer.created_at.desc()).offset(skip).limit(limit)
    else:
        query = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    offers = result.scalars().all()
    if applied_status_enum == OfferStatus.EXPIRED and since_hours:
        logger.info(
            "market.recent_expired_offers.loaded",
            extra={
                "event": "market.recent_expired_offers.loaded",
                "owner_user_id": owner_user.id,
                "actor_user_id": getattr(context.actor_user, "id", None),
                "since_hours": since_hours,
                "limit": limit,
                "result_count": len(offers),
                "offer_ids": [offer.id for offer in offers],
            },
        )
    if not offers:
        return []
    
    # دریافت تنظیمات برای محاسبه انقضا
    if start_settings is None:
        from core.trading_settings import get_trading_settings_async

        start_settings = await get_trading_settings_async()
    
    return await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=start_settings,
        viewer_user_id=owner_user.id,
        include_owner_identity=True,
    )


@router.post("/internal/expire", status_code=status.HTTP_200_OK)
async def expire_offer_internal(
    internal_data: InternalOfferExpireRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    target_server = current_server()
    payload_source_server = _normalize_internal_offer_source(internal_data.source_server)
    header_source_server = _normalize_internal_offer_source(raw_request.headers.get("x-source-server"))
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal offer expiry signature")

    if (
        not payload_source_server
        or not header_source_server
        or payload_source_server != header_source_server
        or payload_source_server == target_server
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal offer expiry source")

    offer = await db.get(Offer, internal_data.offer_id, options=[selectinload(Offer.commodity)])
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    if getattr(offer, "id", None) is None:
        offer.id = internal_data.offer_id

    if normalize_server(getattr(offer, "home_server", None), target_server) != target_server:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع لفظ نیست.")

    if internal_data.offer_public_id and getattr(offer, "offer_public_id", None) != internal_data.offer_public_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="شناسه عمومی لفظ با سرور مرجع همخوانی ندارد.")

    if offer.user_id != internal_data.owner_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")

    if offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

    try:
        await expire_offer_authoritatively(
            db,
            offer,
            OfferExpiryCommand(
                reason=internal_data.expire_reason,
                source_surface=internal_data.source_surface,
                source_server=internal_data.source_server,
                expired_by_user_id=internal_data.owner_user_id,
                expired_by_actor_user_id=internal_data.actor_user_id or internal_data.owner_user_id,
            ),
        )
    except OfferNotAuthoritativeError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع لفظ نیست.")
    except OfferAlreadyInactiveError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

    await _expire_offer_side_effects(
        db,
        offer,
        realtime_reason="internal_offer_expire",
        channel_reason="internal_offer_expire",
        channel_timeout=10,
    )
    return {"expired": True, "offer_id": offer.id}


@router.delete("/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def expire_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    """
    منقضی کردن لفظ
    """
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    ts = get_trading_settings()

    from bot.utils.redis_helpers import track_daily_expire, track_expire_rate

    rate_count = await track_expire_rate(owner_user.id, window_seconds=60)
    if rate_count > ts.offer_expire_rate_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"حداکثر {ts.offer_expire_rate_per_minute} منقضی در دقیقه مجاز است"
        )

    start_of_day = datetime.combine(date.today(), datetime.min.time())
    total_offers_today = await db.scalar(
        select(func.count(Offer.id)).where(
            Offer.user_id == owner_user.id,
            Offer.created_at >= start_of_day
        )
    ) or 0

    daily_data = await track_daily_expire(owner_user.id, total_offers_today)
    threshold = ts.offer_expire_daily_limit_after_threshold
    if daily_data["count"] >= threshold:
        max_allowed = total_offers_today // 3
        if daily_data["count"] >= max_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"شما امروز {daily_data['count']} لفظ منقضی کرده‌اید. "
                    f"برای منقضی کردن بیشتر، باید لفظ‌های جدید ثبت کنید."
                )
            )

    offer = await db.get(Offer, offer_id, options=[selectinload(Offer.commodity)])
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    if offer.user_id != owner_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")
    
    if offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

    forwarded_response = await _forward_offer_expiry_if_remote_home(
        offer,
        owner_user_id=owner_user.id,
        actor_user_id=getattr(context.actor_user, "id", owner_user.id),
        source_surface=OfferExpirySourceSurface.WEBAPP,
        expire_reason=OfferExpiryReason.MANUAL,
    )
    if forwarded_response is not None:
        return forwarded_response

    try:
        await expire_offer_authoritatively(
            db,
            offer,
            OfferExpiryCommand(
                reason=OfferExpiryReason.MANUAL,
                source_surface=OfferExpirySourceSurface.WEBAPP,
                source_server=current_server(),
                expired_by_user_id=owner_user.id,
                expired_by_actor_user_id=getattr(context.actor_user, "id", owner_user.id),
            ),
        )
    except OfferNotAuthoritativeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"این لفظ باید روی سرور {exc.home_server} منقضی شود.")
    except OfferAlreadyInactiveError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

    await _expire_offer_side_effects(
        db,
        offer,
        realtime_reason="manual_expire",
        channel_reason="manual_expire",
        channel_timeout=10,
    )
    
    return None


@router.post("/cancel-all", status_code=status.HTTP_200_OK)
async def cancel_all_active_offers(
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    """
    منقضی کردن تمام لفظ‌های فعال کاربر (برای دستور نشد)
    """
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    
    query = select(Offer).where(
        Offer.user_id == owner_user.id,
        Offer.status == OfferStatus.ACTIVE
    ).options(selectinload(Offer.commodity))
    result = await db.execute(query)
    offers = result.scalars().all()
    
    if not offers:
        return {"cancelled_count": 0}
        
    local_offers = [offer for offer in offers if not is_remote_home(getattr(offer, "home_server", None))]
    remote_offers = [offer for offer in offers if is_remote_home(getattr(offer, "home_server", None))]
    local_result = await expire_offers_authoritatively(
        db,
        local_offers,
        OfferExpiryCommand(
            reason=OfferExpiryReason.CANCEL_ALL,
            source_surface=OfferExpirySourceSurface.WEBAPP,
            source_server=current_server(),
            expired_by_user_id=owner_user.id,
            expired_by_actor_user_id=getattr(context.actor_user, "id", owner_user.id),
        ),
        commit=bool(local_offers),
    )

    remote_expired_count = 0
    for offer in remote_offers:
        if await _forward_offer_expiry_for_cancel_all(
            offer,
            owner_user_id=owner_user.id,
            actor_user_id=getattr(context.actor_user, "id", owner_user.id),
            source_surface=OfferExpirySourceSurface.WEBAPP,
            expire_reason=OfferExpiryReason.CANCEL_ALL,
        ):
            remote_expired_count += 1

    for offer in local_result.expired_offers:
        await _remove_offer_channel_buttons_safely(offer, reason="cancel_all_active_offers", timeout=5)
        await _publish_offer_event_safely("offer:expired", {"id": offer.id}, reason="cancel_all_active_offers")

    if local_result.expired_count or remote_expired_count:
        cache_count = 0 if local_result.expired_count + remote_expired_count == len(offers) else await _read_active_offer_count(db, owner_user.id)
        await _set_active_offer_count_safely(owner_user.id, cache_count, reason="cancel_all_active_offers")
    
    return {"cancelled_count": local_result.expired_count + remote_expired_count}


@router.post("/parse", response_model=ParseOfferResponse)
async def parse_offer_text(
    request: ParseOfferRequest,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    پارس متن لفظ با parser مشترک بات و وب اپ.
    """
    _ensure_accountant_market_access_allowed(context)
    from bot.utils.offer_parser import parse_offer_text as parser
    
    result, error = await parser(request.text)
    
    if error:
        return ParseOfferResponse(success=False, error=error.message)
    
    if result is None:
        return ParseOfferResponse(success=False, error="متن قابل تشخیص نیست.")
    
    return ParseOfferResponse(
        success=True,
        data={
            "trade_type": result.trade_type,
            "commodity_id": result.commodity_id,
            "commodity_name": result.commodity_name,
            "quantity": result.quantity,
            "price": result.price,
            "is_wholesale": result.is_wholesale,
            "lot_sizes": result.lot_sizes,
            "notes": result.notes
        }
    )
