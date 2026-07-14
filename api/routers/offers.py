# api/routers/offers.py
"""
API Router for Offer Management - Web App Integration
"""
import base64
import binascii
import json
import logging
from datetime import datetime, timedelta
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, and_, or_, case
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from core.db import AsyncSessionLocal, get_db
from core.config import settings
from core.trading_settings import TradingSettings, get_trading_settings
from core.utils import check_user_limits, to_jalali_str, utc_now_naive
from core.services.market_transition_service import (
    MarketOfferAdmissionError,
    MarketOfferAdmissionClosedError,
    evaluate_current_market_schedule,
    register_market_offer_created,
)
from core.services.telegram_offer_channel_service import (
    apply_offer_channel_state,
    build_offer_channel_message,
    build_offer_channel_reply_markup,
)
from core.services.telegram_offer_publication_service import (
    TelegramOfferSendResult,
    publish_offer_to_telegram_channel_once,
    telegram_offer_send_result_from_gateway,
)
from core.services.customer_relation_service import (
    build_customer_offer_read_model,
    customer_management_name_for_user_id,
    get_active_customer_relation_for_customer,
    load_offer_customer_read_context,
)
from core.services.user_account_status_service import is_user_market_blocked
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.offer_expiry_contracts import (
    OfferExpiryCommandIdentityError,
    build_offer_expiry_forward_payload,
    canonical_offer_expiry_command_payload,
    validate_offer_expiry_command_identity,
)
from core.offer_identity import build_offer_public_link, ensure_offer_public_id, is_offer_public_id_shape
from core.offer_quantity import coalesce_offer_remaining_quantity
from core.offer_settlement import settlement_type_value
from core.offer_request_policy import (
    OfferRequestVisibility,
    map_legacy_expire_reason,
    sanitize_offer_request_payload,
)
from core.offer_source import OfferSourceSurface
from core.services.offer_creation_service import (
    OfferCreationAdmissionError,
    OfferCreationCommand,
    OfferCreationLimitExceededError,
    OfferCreationQuotaPolicy,
    OfferCreationQuotaUnavailableError,
    OfferCreationValidationError,
    create_authoritative_offer_with_outcome,
)
from core.services.offer_expiry_service import (
    OfferAlreadyInactiveError,
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    OfferNotAuthoritativeError,
    expire_offer_authoritatively,
    is_offer_expiry_lock_busy,
)
from core.services.offer_cancel_all_service import cancel_all_active_offers_authoritatively
from core.services.offer_expiry_limits import (
    OfferManualExpireLimitError,
    enforce_manual_offer_expire_limits,
)
from core.services.offer_expiry_gate import try_acquire_offer_expiry_gate
from core.services.offer_expiry_command_receipt_service import (
    OfferExpiryCommandReceiptIncomplete,
    OfferExpiryCommandReplayConflict,
    OfferExpiryReceiptOutcome,
    finalize_offer_expiry_command_receipt,
    offer_expiry_side_effect_dedupe_key,
    prepare_offer_expiry_command_receipt,
    replay_offer_expiry_receipt_outcome,
)
from core.services.offer_republish_service import (
    OfferNotRepeatableError,
    ensure_republish_payload_matches_source,
    list_repeatable_offers,
    lock_repeatable_offer,
)
from core import telegram_gateway
from core.trade_forwarding import verify_internal_signature
from core.trading_observability import log_trading_event
from models.user import User
from core.enums import SettlementType, UserRole
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
OFFER_EXPIRY_LOCK_BUSY_DETAIL = "این لفظ در حال غیرفعال شدن است."
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
    settlement_type: str = Field(default="cash", pattern="^(cash|tomorrow)$", description="تسویه: cash یا tomorrow")
    commodity_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    price: int = Field(..., gt=0)
    is_wholesale: bool = Field(default=True, description="یکجا یا خُرد")
    lot_sizes: Optional[List[int]] = Field(default=None, description="بخش‌ها برای فروش خُرد")
    notes: Optional[str] = Field(default=None, max_length=200)
    republished_from_id: Optional[int] = Field(default=None, description="شناسه لفظ قدیمی برای تکرار")
    republished_from_public_id: Optional[str] = Field(
        default=None,
        max_length=40,
        description="شناسه عمومی لفظ منبع برای تکرار",
    )
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
    settlement_type: str
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


class ActiveOfferPageResponse(BaseModel):
    items: List[OfferResponse]
    next_cursor: Optional[str] = None
    has_more: bool
    page_size: int


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
    settlement_type: str
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
    command_id: Optional[UUID] = None
    idempotency_key: Optional[str] = Field(
        None,
        min_length=16,
        max_length=192,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @model_validator(mode="after")
    def validate_optional_command_identity(self):
        if (self.command_id is None) != (self.idempotency_key is None):
            raise ValueError("command_id and idempotency_key must be provided together")
        if self.command_id is not None and not self.offer_public_id:
            raise ValueError("offer_public_id is required for idempotent expiry")
        return self


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
    remaining = coalesce_offer_remaining_quantity(offer.remaining_quantity, offer.quantity)
    is_own_offer = viewer_user_id is not None and offer.user_id == viewer_user_id
    offer_read_model = build_customer_offer_read_model(
        raw_price=offer.price,
        offer_type=offer.offer_type,
        viewer_user_id=viewer_user_id,
        offer_owner_relation=offer_owner_relation,
        viewer_customer_relation=viewer_customer_relation,
    )
    offer_public_id = ensure_offer_public_id(offer)
    owner_display_name = ""
    if include_owner_identity and offer.user:
        owner_relation_map = {int(offer.user_id): offer_owner_relation} if offer_owner_relation else None
        owner_display_name = (
            customer_management_name_for_user_id(offer.user_id, owner_relation_map)
            or offer.user.account_name
            or ""
        )

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
        user_account_name=owner_display_name,
        is_own_offer=is_own_offer,
        offer_type=offer.offer_type.value,
        settlement_type=settlement_type_value(getattr(offer, "settlement_type", None)),
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
        settlement_type=settlement_type_value(getattr(offer, "settlement_type", None)),
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


def _active_offer_filter_signature(
    *,
    offer_type: str | None,
    settlement_type: str | None,
    commodity_id: int | None,
    own_only: bool,
) -> dict[str, object]:
    return {
        "offer_type": offer_type or "",
        "settlement_type": settlement_type or "",
        "commodity_id": int(commodity_id or 0),
        "own_only": bool(own_only),
    }


def _encode_active_offer_cursor(
    offer: Offer | object,
    *,
    filter_signature: dict[str, object],
) -> str:
    created_at = getattr(offer, "created_at", None)
    offer_id = getattr(offer, "id", None)
    if not isinstance(created_at, datetime) or not isinstance(offer_id, int) or offer_id <= 0:
        raise ValueError("active offer cursor requires created_at and id")
    payload = {
        "v": 1,
        "created_at": created_at.isoformat(),
        "id": offer_id,
        "filters": filter_signature,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return encoded.rstrip("=")


def _decode_active_offer_cursor(
    cursor: str,
    *,
    expected_filter_signature: dict[str, object],
) -> tuple[datetime, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            f"{cursor}{padding}",
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("unsupported cursor version")
        offer_id = payload.get("id")
        if not isinstance(offer_id, int) or isinstance(offer_id, bool) or offer_id <= 0:
            raise ValueError("invalid cursor id")
        created_at = datetime.fromisoformat(payload.get("created_at"))
        if payload.get("filters") != expected_filter_signature:
            raise ValueError("cursor filters do not match")
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="نشانگر صفحه بازار نامعتبر است.",
        ) from exc
    return created_at, offer_id


def _build_active_offer_page_query(
    *,
    owner_user_id: int,
    offer_type: str | None,
    settlement_type: str | None,
    commodity_id: int | None,
    own_only: bool,
    cursor_position: tuple[datetime, int] | None,
    limit: int,
):
    query = select(Offer).options(
        *build_offer_read_options(include_owner_identity=False)
    ).where(Offer.status == OfferStatus.ACTIVE)

    if offer_type:
        query = query.where(
            Offer.offer_type == (OfferType.BUY if offer_type == "buy" else OfferType.SELL)
        )
    if settlement_type:
        query = query.where(
            Offer.settlement_type == (
                SettlementType.CASH if settlement_type == "cash" else SettlementType.TOMORROW
            )
        )
    if commodity_id:
        query = query.where(Offer.commodity_id == commodity_id)
    if own_only:
        query = query.where(Offer.user_id == owner_user_id)
    if cursor_position is not None:
        cursor_created_at, cursor_id = cursor_position
        query = query.where(
            or_(
                Offer.created_at < cursor_created_at,
                and_(Offer.created_at == cursor_created_at, Offer.id < cursor_id),
            )
        )

    return query.order_by(Offer.created_at.desc(), Offer.id.desc()).limit(limit + 1)


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
    side_effect_dedupe_key: str | None = None,
) -> None:
    if side_effect_dedupe_key:
        log_trading_event(
            logger,
            "offer_expiry_side_effects.dispatch",
            action="trading_side_effect",
            result="attempt",
            side_effect="offer_expiry_post_commit",
            offer_id=getattr(offer, "id", None),
            dedupe_key=side_effect_dedupe_key,
        )
    await _remove_offer_channel_buttons_safely(offer, reason=channel_reason, timeout=channel_timeout)
    realtime_payload = {"id": offer.id}
    if getattr(offer, "offer_public_id", None):
        realtime_payload["offer_public_id"] = offer.offer_public_id
    await _publish_offer_event_safely("offer:expired", realtime_payload, reason=realtime_reason)
    if update_active_count and getattr(offer, "user_id", None):
        active_count = await _read_active_offer_count(db, offer.user_id)
        await _set_active_offer_count_safely(offer.user_id, active_count, reason=realtime_reason)


async def send_offer_to_channel_with_result(offer: Offer, user: User) -> TelegramOfferSendResult | None:
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
            return telegram_offer_send_result_from_gateway(result)
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
        return telegram_offer_send_result_from_gateway(result)
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


async def send_offer_to_channel(offer: Offer, user: User) -> Optional[int]:
    """Backward-compatible Telegram offer-channel send callback."""
    result = await send_offer_to_channel_with_result(offer, user)
    return result.message_id if result else None


def _normalize_internal_offer_source(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = normalize_server(value, default="")
    return normalized if normalized in {"iran", "foreign"} else None


def _expunge_if_supported(db: AsyncSession, obj: object | None) -> None:
    if obj is None:
        return
    expunge = getattr(db, "expunge", None)
    if callable(expunge):
        expunge(obj)


async def _rollback_if_supported(db: AsyncSession) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        await rollback()


async def _raise_offer_expiry_lock_busy(db: AsyncSession) -> None:
    await _rollback_if_supported(db)
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=OFFER_EXPIRY_LOCK_BUSY_DETAIL)


def _build_offer_expiry_forward_payload(
    offer: Offer,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    source_surface: OfferExpirySourceSurface | str,
    expire_reason: str,
) -> dict:
    return build_offer_expiry_forward_payload(
        offer,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        source_surface=source_surface,
        source_server=current_server(),
        expire_reason=expire_reason,
        include_command_identity=bool(settings.offer_expiry_command_receipts_enabled),
    )


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

    try:
        payload = _build_offer_expiry_forward_payload(
            offer,
            owner_user_id=owner_user_id,
            actor_user_id=actor_user_id,
            source_surface=source_surface,
            expire_reason=expire_reason,
        )
    except OfferExpiryCommandIdentityError:
        log_trading_event(
            logger,
            "offer_expiry_forward.identity_invalid",
            level="warning",
            action="offer_expiry_forward",
            result="denied",
            offer_id=getattr(offer, "id", None),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "شناسه عمومی لفظ برای انتقال معتبر نیست."},
        )
    status_code, body = await forward_offer_expiry_to_home_server(offer.home_server, payload)
    if status_code < 400:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(status_code=status_code, content=body)


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
        return (
            await _serialize_offer_responses(
                [existing_offer],
                db=db,
                start_settings=ts,
                viewer_user_id=owner_user.id,
                include_owner_identity=True,
            )
        )[0]
    
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

    republish_source_public_id = (offer_data.republished_from_public_id or "").strip()
    republish_requested = bool(offer_data.republished_from_id or republish_source_public_id)
    if republish_requested:
        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="برای انتشار مجدد، شناسه یکتای درخواست الزامی است.",
            )
        if not is_offer_public_id_shape(republish_source_public_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="شناسه عمومی لفظ قبلی برای انتشار مجدد الزامی است.",
            )
        try:
            republish_source = await lock_repeatable_offer(
                db,
                owner_user_id=owner_user.id,
                offer_public_id=republish_source_public_id,
                expected_local_id=offer_data.republished_from_id,
                market_is_open=True,
            )
            ensure_republish_payload_matches_source(
                republish_source,
                offer_type=offer_data.offer_type,
                settlement_type=offer_data.settlement_type,
                commodity_id=offer_data.commodity_id,
                quantity=offer_data.quantity,
                price=offer_data.price,
                is_wholesale=offer_data.is_wholesale,
                lot_sizes=offer_data.lot_sizes,
                notes=offer_data.notes,
            )
        except OfferNotRepeatableError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="این لفظ دیگر قابل انتشار مجدد نیست. فهرست لفظ‌های اخیر را تازه‌سازی کنید.",
            ) from exc
    
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

    active_count = await _read_active_offer_count(db, owner_user.id)
    cached_active_count = await _cached_active_offer_count(owner_user.id)
    if cached_active_count != active_count:
        await _set_active_offer_count_safely(owner_user.id, active_count, reason="create_offer_guard_repair")

    if active_count >= ts.max_active_offers:
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
        settlement_type=offer_data.settlement_type,
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
        settlement_type=offer_data.settlement_type,
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

    try:
        creation_outcome = await create_authoritative_offer_with_outcome(
            db,
            OfferCreationCommand(
                source_surface=OfferSourceSurface.WEBAPP,
                owner_user_id=owner_user.id,
                actor_user_id=actor_user.id,
                offer_type=OfferType.BUY if offer_data.offer_type == "buy" else OfferType.SELL,
                settlement_type=offer_data.settlement_type,
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
                republished_from_offer_public_id=republish_source_public_id or None,
            ),
            enforce_market_admission=True,
            quota_policy=OfferCreationQuotaPolicy(
                max_active_offers=ts.max_active_offers,
            ),
        )
        new_offer = creation_outcome.offer
    except MarketOfferAdmissionError as exc:
        await db.rollback()
        rejection_reason = (
            "market_closed_at_final_admission"
            if isinstance(exc, MarketOfferAdmissionClosedError)
            else "market_admission_fence_unavailable"
        )
        log_trading_event(
            logger,
            "offer_create.final_admission_rejected",
            action="offer_create",
            result="rejected",
            source_server=current_server(),
            has_idempotency_key=bool(idempotency_key),
            reason=rejection_reason,
            error_class=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MARKET_CLOSED_DETAIL,
        ) from exc
    except OfferCreationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OfferCreationLimitExceededError as exc:
        await db.rollback()
        log_trading_event(
            logger,
            "offer_create.final_quota_rejected",
            action="offer_create",
            result="rejected",
            source_server=current_server(),
            has_idempotency_key=bool(idempotency_key),
            reason=exc.reason,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail) from exc
    except OfferCreationQuotaUnavailableError as exc:
        await db.rollback()
        log_trading_event(
            logger,
            "offer_create.final_quota_unavailable",
            level="warning",
            action="offer_create",
            result="rejected",
            source_server=current_server(),
            has_idempotency_key=bool(idempotency_key),
            reason=exc.reason,
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail) from exc
    except OfferCreationAdmissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from exc
    except IntegrityError:
        await db.rollback()
        existing_offer = await _load_offer_idempotency_replay(
            db,
            owner_user_id=owner_user.id,
            idempotency_key=idempotency_key,
        )
        if existing_offer is None:
            if republish_source_public_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="این لفظ قبلاً مجدداً منتشر شده است.",
                )
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
        return (
            await _serialize_offer_responses(
                [existing_offer],
                db=db,
                start_settings=ts,
                viewer_user_id=owner_user.id,
                include_owner_identity=True,
            )
        )[0]

    if not creation_outcome.created:
        existing_offer = await _load_offer_idempotency_replay(
            db,
            owner_user_id=owner_user.id,
            idempotency_key=idempotency_key,
        )
        if existing_offer is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="نتیجه درخواست قبلی قابل بازیابی نیست. لطفاً دوباره تلاش کنید.",
            )
        from core.trading_settings import get_trading_settings_async

        replay_settings = await get_trading_settings_async()
        log_trading_event(
            logger,
            "offer_idempotent_replay_after_quota_lock",
            action="offer_idempotent_replay",
            result="replay",
            offer_id=getattr(existing_offer, "id", None),
            has_idempotency_key=True,
        )
        return (
            await _serialize_offer_responses(
                [existing_offer],
                db=db,
                start_settings=replay_settings,
                viewer_user_id=owner_user.id,
                include_owner_identity=True,
            )
        )[0]

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
    
    expected_active_count = creation_outcome.active_offer_count
    if expected_active_count is None:
        expected_active_count = await _read_active_offer_count(db, owner_user.id)
    await _set_active_offer_count_safely(owner_user.id, expected_active_count, reason="create_offer_final")

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
    
    await _publish_offer_event_safely("offer:created", {
        "id": new_offer.id,
        "offer_public_id": new_offer.offer_public_id,
        "public_link": build_offer_public_link(new_offer.offer_public_id),
        "user_id": None,
        "offer_type": new_offer.offer_type.value,
        "settlement_type": settlement_type_value(getattr(new_offer, "settlement_type", None)),
        "commodity_id": new_offer.commodity_id,
        "commodity_name": new_offer.commodity.name,
        "quantity": new_offer.quantity,
        "remaining_quantity": coalesce_offer_remaining_quantity(
            new_offer.remaining_quantity,
            new_offer.quantity,
        ),
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
    
    return (
        await _serialize_offer_responses(
            [new_offer],
            db=db,
            start_settings=ts,
            viewer_user_id=owner_user.id,
            include_owner_identity=True,
        )
    )[0]


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
    
    query = query.order_by(Offer.created_at.desc(), Offer.id.desc()).offset(skip).limit(limit)
    
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


@router.get("/page", response_model=ActiveOfferPageResponse)
async def get_active_offer_page(
    offer_type: Optional[str] = Query(None, pattern="^(buy|sell)$"),
    settlement_type: Optional[str] = Query(None, pattern="^(cash|tomorrow)$"),
    commodity_id: Optional[int] = Query(None, gt=0),
    own_only: bool = Query(False),
    cursor: Optional[str] = Query(None, min_length=1, max_length=512),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    filter_signature = _active_offer_filter_signature(
        offer_type=offer_type,
        settlement_type=settlement_type,
        commodity_id=commodity_id,
        own_only=own_only,
    )
    cursor_position = None
    if cursor:
        cursor_position = _decode_active_offer_cursor(
            cursor,
            expected_filter_signature=filter_signature,
        )

    query = _build_active_offer_page_query(
        owner_user_id=owner_user.id,
        offer_type=offer_type,
        settlement_type=settlement_type,
        commodity_id=commodity_id,
        own_only=own_only,
        cursor_position=cursor_position,
        limit=limit,
    )
    rows = list((await db.execute(query)).scalars().all())
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    if not page_rows:
        return ActiveOfferPageResponse(items=[], has_more=False, page_size=0)

    from core.trading_settings import get_trading_settings_async

    trading_settings = await get_trading_settings_async()
    items = await _serialize_offer_responses(
        page_rows,
        db=db,
        start_settings=trading_settings,
        viewer_user_id=owner_user.id,
        include_owner_identity=False,
    )
    next_cursor = None
    if has_more:
        next_cursor = _encode_active_offer_cursor(
            page_rows[-1],
            filter_signature=filter_signature,
        )
    return ActiveOfferPageResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        page_size=len(items),
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
    # Initial Telegram publication failures never became visible market offers.
    market_visible_expired_condition = and_(
        Offer.status.in_([OfferStatus.EXPIRED, OfferStatus.CANCELLED]),
        or_(
            Offer.expire_reason.is_(None),
            Offer.expire_reason != OfferExpiryReason.TELEGRAM_SEND_FAILED,
        ),
        expired_at_expr >= cutoff_time,
    )
    history_conditions = [
        and_(
            Offer.status == OfferStatus.COMPLETED,
            completed_event_at_expr >= cutoff_time,
        ),
        market_visible_expired_condition,
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


@router.get("/my/repeatable", response_model=List[OfferResponse])
async def get_my_repeatable_offers(
    limit: int = Query(3, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """Return repeatable leaf offers created and expired in the current market session."""
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)

    actor_relation = await get_active_customer_relation_for_customer(db, context.actor_user.id)
    if actor_relation is not None and actor_relation.customer_tier == CustomerTier.TIER_2:
        return []

    offers = await list_repeatable_offers(
        db,
        owner_user_id=context.owner_user.id,
        limit=limit,
        since_hours=1,
        options=build_offer_read_options(include_owner_identity=False),
    )
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


def _offer_expiry_receipt_response(receipt, *, replayed: bool) -> dict[str, Any]:
    outcome = replay_offer_expiry_receipt_outcome(receipt)
    return {
        "expired": outcome == OfferExpiryReceiptOutcome.EXPIRED,
        "offer_public_id": receipt.offer_public_id,
        "command_id": str(receipt.command_id),
        "outcome": outcome.value,
        "replayed": replayed,
    }


async def _expire_offer_internal_with_receipt(
    internal_data: InternalOfferExpireRequest,
    *,
    target_server: str,
    db: AsyncSession,
) -> dict[str, Any]:
    assert internal_data.command_id is not None
    assert internal_data.idempotency_key is not None
    assert internal_data.offer_public_id is not None

    try:
        canonical_payload = canonical_offer_expiry_command_payload(
            offer_public_id=internal_data.offer_public_id,
            owner_user_id=internal_data.owner_user_id,
            actor_user_id=internal_data.actor_user_id,
            source_surface=internal_data.source_surface,
            source_server=internal_data.source_server,
            expire_reason=internal_data.expire_reason,
        )
        identity = validate_offer_expiry_command_identity(
            command_id=internal_data.command_id,
            idempotency_key=internal_data.idempotency_key,
            offer_public_id=internal_data.offer_public_id,
            owner_user_id=internal_data.owner_user_id,
            actor_user_id=internal_data.actor_user_id,
            source_surface=internal_data.source_surface,
            source_server=internal_data.source_server,
            expire_reason=internal_data.expire_reason,
        )
    except OfferExpiryCommandIdentityError as exc:
        await _rollback_if_supported(db)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="هویت فرمان انقضا با محتوای درخواست همخوانی ندارد.",
        ) from exc

    try:
        receipt, replayed = await prepare_offer_expiry_command_receipt(
            db,
            command_id=identity.command_id,
            idempotency_key=identity.idempotency_key,
            request_hash=identity.request_hash,
            offer_public_id=canonical_payload["offer_public_id"],
            source_server=canonical_payload["source_server"],
            source_surface=canonical_payload["source_surface"],
            expire_reason=canonical_payload["expire_reason"],
        )
        if replayed:
            response = _offer_expiry_receipt_response(receipt, replayed=True)
            await db.commit()
            log_trading_event(
                logger,
                "offer_expiry_command.replayed",
                action="offer_expiry_command",
                result="success",
                command_id=str(identity.command_id),
                offer_public_id=canonical_payload["offer_public_id"],
            )
            return response

        try:
            result = await db.execute(
                select(Offer)
                .where(Offer.offer_public_id == canonical_payload["offer_public_id"])
                .options(selectinload(Offer.commodity))
                .with_for_update(nowait=True)
            )
            offer = result.scalar_one_or_none()
        except (OperationalError, DBAPIError) as exc:
            if is_offer_expiry_lock_busy(exc):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=OFFER_EXPIRY_LOCK_BUSY_DETAIL,
                ) from exc
            raise

        if not offer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
        if normalize_server(getattr(offer, "home_server", None), target_server) != target_server:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع لفظ نیست.")
        if offer.user_id != canonical_payload["owner_user_id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")
        if offer.status != OfferStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="این لفظ قبلاً غیرفعال شده است.",
            )

        if canonical_payload["expire_reason"] == OfferExpiryReason.MANUAL:
            try:
                await enforce_manual_offer_expire_limits(
                    db,
                    owner_user_id=canonical_payload["owner_user_id"],
                )
            except OfferManualExpireLimitError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        await expire_offer_authoritatively(
            db,
            offer,
            OfferExpiryCommand(
                reason=canonical_payload["expire_reason"],
                source_surface=canonical_payload["source_surface"],
                source_server=canonical_payload["source_server"],
                expired_by_user_id=canonical_payload["owner_user_id"],
                expired_by_actor_user_id=canonical_payload["actor_user_id"],
            ),
            commit=False,
        )
        finalize_offer_expiry_command_receipt(
            receipt,
            outcome=OfferExpiryReceiptOutcome.EXPIRED,
        )
        await db.flush()
        await db.commit()
        log_trading_event(
            logger,
            "offer_expiry_command.committed",
            action="offer_expiry_command",
            result="success",
            command_id=str(identity.command_id),
            offer_public_id=canonical_payload["offer_public_id"],
        )
    except OfferExpiryCommandReplayConflict as exc:
        await _rollback_if_supported(db)
        log_trading_event(
            logger,
            "offer_expiry_command.replay_conflict",
            level="warning",
            action="offer_expiry_command",
            result="denied",
            command_id=str(identity.command_id),
            offer_public_id=canonical_payload["offer_public_id"],
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="این شناسه فرمان قبلاً با محتوای دیگری استفاده شده است.",
        ) from exc
    except OfferExpiryCommandReceiptIncomplete as exc:
        await _rollback_if_supported(db)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="نتیجه فرمان قبلی کامل نیست؛ لطفاً بعداً دوباره تلاش کنید.",
        ) from exc
    except OfferNotAuthoritativeError as exc:
        await _rollback_if_supported(db)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع لفظ نیست.") from exc
    except OfferAlreadyInactiveError as exc:
        await _rollback_if_supported(db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="این لفظ قبلاً غیرفعال شده است.",
        ) from exc
    except StaleDataError as exc:
        await _rollback_if_supported(db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="این لفظ قبلاً غیرفعال شده است.",
        ) from exc
    except HTTPException:
        await _rollback_if_supported(db)
        raise
    except Exception:
        await _rollback_if_supported(db)
        raise

    dedupe_key = offer_expiry_side_effect_dedupe_key(
        command_id=identity.command_id,
        offer_public_id=canonical_payload["offer_public_id"],
        offer_version=int(getattr(offer, "version_id", 1) or 1),
    )
    await _expire_offer_side_effects(
        db,
        offer,
        realtime_reason="internal_offer_expire",
        channel_reason="internal_offer_expire",
        channel_timeout=10,
        side_effect_dedupe_key=dedupe_key,
    )
    return _offer_expiry_receipt_response(receipt, replayed=False)


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

    if internal_data.command_id is not None:
        return await _expire_offer_internal_with_receipt(
            internal_data,
            target_server=target_server,
            db=db,
        )

    try:
        offer = await db.get(
            Offer,
            internal_data.offer_id,
            options=[selectinload(Offer.commodity)],
            with_for_update={"nowait": True},
        )
    except (OperationalError, DBAPIError) as exc:
        if is_offer_expiry_lock_busy(exc):
            await _raise_offer_expiry_lock_busy(db)
        raise
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

    if internal_data.expire_reason == OfferExpiryReason.MANUAL:
        try:
            await enforce_manual_offer_expire_limits(
                db,
                owner_user_id=internal_data.owner_user_id,
            )
        except OfferManualExpireLimitError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

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

    lease = await try_acquire_offer_expiry_gate(offer_id=offer_id)
    if not lease.acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=OFFER_EXPIRY_LOCK_BUSY_DETAIL)
    try:
        offer = await db.get(Offer, offer_id, options=[selectinload(Offer.commodity)])

        if not offer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")

        if offer.user_id != owner_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")

        forwarded_response = await _forward_offer_expiry_if_remote_home(
            offer,
            owner_user_id=owner_user.id,
            actor_user_id=getattr(context.actor_user, "id", owner_user.id),
            source_surface=OfferExpirySourceSurface.WEBAPP,
            expire_reason=OfferExpiryReason.MANUAL,
        )
        if forwarded_response is not None:
            return forwarded_response

        if offer.status != OfferStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

        _expunge_if_supported(db, offer)
        try:
            offer = await db.get(
                Offer,
                offer_id,
                options=[selectinload(Offer.commodity)],
                with_for_update={"nowait": True},
                populate_existing=True,
            )
        except StaleDataError:
            await _rollback_if_supported(db)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")
        except (OperationalError, DBAPIError) as exc:
            if is_offer_expiry_lock_busy(exc):
                await _raise_offer_expiry_lock_busy(db)
            raise
        if not offer:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
        if offer.user_id != owner_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")
        if offer.status != OfferStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

        try:
            await enforce_manual_offer_expire_limits(
                db,
                owner_user_id=owner_user.id,
                trading_settings=ts,
            )
        except OfferManualExpireLimitError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail)

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
        except StaleDataError:
            await _rollback_if_supported(db)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")

        await _expire_offer_side_effects(
            db,
            offer,
            realtime_reason="manual_expire",
            channel_reason="manual_expire",
            channel_timeout=10,
        )

        return None
    finally:
        await lease.release()


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
    
    result = await cancel_all_active_offers_authoritatively(
        owner_user_id=owner_user.id,
        actor_user_id=getattr(context.actor_user, "id", owner_user.id),
        source_surface=OfferExpirySourceSurface.WEBAPP,
        expire_reason=OfferExpiryReason.CANCEL_ALL,
        session_factory=AsyncSessionLocal,
    )

    for offer in result.locally_cancelled_offers:
        await _expire_offer_side_effects(
            db,
            offer,
            realtime_reason="cancel_all_active_offers",
            channel_reason="cancel_all_active_offers",
            channel_timeout=5,
            update_active_count=False,
        )
    if result.total_count and result.remaining_active_count is not None:
        await _set_active_offer_count_safely(
            owner_user.id,
            result.remaining_active_count,
            reason="cancel_all_active_offers",
        )
    
    return result.to_payload()


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
            "settlement_type": settlement_type_value(getattr(result, "settlement_type", None)),
            "commodity_id": result.commodity_id,
            "commodity_name": result.commodity_name,
            "quantity": result.quantity,
            "price": result.price,
            "is_wholesale": result.is_wholesale,
            "lot_sizes": result.lot_sizes,
            "notes": result.notes
        }
    )
