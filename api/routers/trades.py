# api/routers/trades.py
"""
API Router for Trade Management - MiniApp Integration
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional, Mapping
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.db import get_db
from core.config import settings
from core.enums import NotificationLevel, NotificationCategory
from core.utils import (
    check_user_limits, increment_user_counter, to_jalali_str,
    create_user_notification, send_telegram_notification
)
from core.services.accountant_chat_contract import AccountantChatIdentity, load_accountant_chat_identity_map
from core.services.accountant_relation_service import build_trade_notification_audience_user_ids
from core.services.market_transition_service import evaluate_current_market_schedule
from core.services.customer_relation_service import (
    apply_customer_commission,
    get_active_customer_relation_for_customer,
)
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    validate_offer_trade_amount,
)
from core.services.user_account_status_service import is_user_trade_blocked
from models.user import User, UserRole
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity
from api.deps import EffectiveOwnerActor, get_current_user, get_effective_owner_actor_context
from core.server_routing import current_server, is_remote_home, normalize_server
from core.trade_forwarding import forward_trade_to_home_server, verify_internal_signature


logger = logging.getLogger(__name__)


MARKET_CLOSED_DETAIL = "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید."


router = APIRouter(
    tags=["Trades"],
)


# --- Pydantic Schemas ---

class TradeCreate(BaseModel):
    """ایجاد معامله جدید"""
    offer_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    idempotency_key: Optional[str] = None


class InternalTradeExecuteRequest(BaseModel):
    """درخواست داخلی اجرای معامله روی سرور مرجع آفر"""
    offer_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    responder_user_id: int = Field(..., gt=0)
    actor_user_id: Optional[int] = Field(None, gt=0)
    edge_received_at: datetime
    source_server: str
    idempotency_key: Optional[str] = None


class TradeResponse(BaseModel):
    """پاسخ معامله"""
    id: int
    trade_number: int
    offer_id: Optional[int]
    trade_type: str
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
    relation = await get_active_customer_relation_for_customer(db, customer_user_id)
    if not _viewer_can_access_customer_history_relation(relation=relation, context=context):
        return None
    return relation


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

    actor_user_id = _coerce_trade_user_id(getattr(trade, "actor_user_id", None))
    if viewer_owner_user_id is not None and actor_user_id == viewer_owner_user_id:
        return True

    for participant_user_id in participant_user_ids:
        relation = await get_active_customer_relation_for_customer(db, participant_user_id)
        if _viewer_can_access_customer_history_relation(relation=relation, context=context):
            return True

    if actor_user_id is not None:
        actor_relation = await get_active_customer_relation_for_customer(db, actor_user_id)
        if _viewer_can_access_customer_history_relation(relation=actor_relation, context=context):
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

    def _relation_sort_timestamp(relation: CustomerRelation | object) -> datetime:
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

    def _relation_sort_key(relation: CustomerRelation | object) -> tuple[int, datetime, datetime]:
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
        return (is_active, _relation_sort_timestamp(relation), created_at)

    relation_map: dict[int, CustomerRelation] = {}
    for relation in sorted(relations, key=_relation_sort_key, reverse=True):
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
) -> dict[str, object | None]:
    normalized_user_id = _coerce_trade_user_id(user_id)
    fallback_name = getattr(user, "account_name", None)

    payload: dict[str, object | None] = {
        f"{field_prefix}_id": normalized_user_id,
        f"{field_prefix}_name": fallback_name,
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

    payload[f"{field_prefix}_name"] = getattr(identity, "display_name", None) or fallback_name
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
) -> dict[str, object | None]:
    trade_like = SimpleNamespace(
        id=trade_id,
        trade_number=trade_number,
        offer_id=offer_id,
        trade_type=SimpleNamespace(value=trade_type),
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
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=responder_user,
        user_id=responder_user_id,
        identity_map=identity_map,
    )
    payload: dict[str, object | None] = {
        "id": trade_id,
        "trade_number": trade_number,
        "offer_id": offer_id,
        "trade_type": trade_type,
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
    from .realtime import publish_event, publish_user_event

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
            await publish_user_event(audience_user_id, "trade:created", recipient_payload)

    generic_audience = sorted(
        {
            normalized_user_id
            for raw_user_id in [
                *(list(responder_audience_user_ids or [])),
                *(list(offer_owner_audience_user_ids or [])),
            ]
            for normalized_user_id in [_coerce_trade_user_id(raw_user_id)]
            if normalized_user_id is not None
        }
    )
    await publish_event(
        "trade:created",
        _build_trade_created_event_payload(
            **common_payload,
            audience_user_ids=generic_audience,
        ),
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
) -> dict[str, object | None]:
    return {
        "route": _build_trade_profile_route_from_payload(field_prefix, participant_payload),
        "trade_number": trade_number,
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

    actor_user_id = _coerce_trade_user_id(getattr(trade, "actor_user_id", None))
    if actor_user_id != target_user_id or not customer_relation_map:
        return None

    actor_relation = customer_relation_map.get(actor_user_id)
    actor_owner_user_id = _coerce_trade_user_id(getattr(actor_relation, "owner_user_id", None))
    if actor_owner_user_id is None:
        return None
    if offer_user_id == actor_owner_user_id and responder_user_id != actor_owner_user_id:
        return "offer_user"
    if responder_user_id == actor_owner_user_id and offer_user_id != actor_owner_user_id:
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
) -> TradeResponse:
    """تبدیل مدل Trade به پاسخ API"""
    offer_user_payload = _build_trade_participant_payload(
        "offer_user",
        user=trade.offer_user,
        user_id=trade.offer_user_id,
        identity_map=identity_map,
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=trade.responder_user,
        user_id=trade.responder_user_id,
        identity_map=identity_map,
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
    return TradeResponse(
        id=trade.id,
        trade_number=trade.trade_number,
        offer_id=trade.offer_id,
        trade_type=trade.trade_type.value,
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
        created_at=to_jalali_str(trade.created_at) or ""
    )


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """ارسال پیام به تلگرام"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending telegram message: {e}")
        return False


async def update_channel_buttons(offer: Offer) -> bool:
    """آپدیت دکمه‌های پست کانال"""
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id or not offer.channel_message_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
    
    if offer.remaining_quantity <= 0 or offer.status != OfferStatus.ACTIVE:
        # حذف دکمه‌ها
        payload = {
            "chat_id": channel_id,
            "message_id": offer.channel_message_id
        }
    else:
        # ساخت دکمه‌های جدید
        if offer.is_wholesale or not offer.lot_sizes:
            buttons = [[{"text": f"{offer.remaining_quantity} عدد", "callback_data": f"channel_trade:{offer.id}:{offer.remaining_quantity}"}]]
        else:
            valid_lots = get_available_trade_amounts(
                quantity=offer.quantity,
                remaining_quantity=offer.remaining_quantity,
                is_wholesale=False,
                lot_sizes=offer.lot_sizes,
            )
            if not valid_lots:
                payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
                buttons = None
            else:
                buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer.id}:{a}"} for a in valid_lots]]
        
        if buttons is not None:
            payload = {
                "chat_id": channel_id,
                "message_id": offer.channel_message_id,
                "reply_markup": {"inline_keyboard": buttons}
            }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error updating channel buttons: {e}")
        return False


# ===== Sync Wrappers for BackgroundTasks =====
# استفاده از httpx sync client به جای asyncio.run برای جلوگیری از مشکلات event loop

def send_telegram_message_sync(chat_id: int, text: str) -> bool:
    """نسخه sync برای استفاده در BackgroundTasks"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    
    try:
        # استفاده از httpx sync client به جای asyncio.run
        response = httpx.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"[Background] Error sending telegram message: {e}")
        return False


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
    except Exception as e:
        logger.error(f"[Background] Error updating channel buttons: {e}")
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
        offer = await session.get(Offer, offer_id)
        if not offer or not offer.channel_message_id:
            return False
        
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        
        if remaining_quantity <= 0 or offer_status != OfferStatus.ACTIVE:
            payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
        else:
            if offer.is_wholesale or not lot_sizes:
                buttons = [[{"text": f"{remaining_quantity} عدد", "callback_data": f"channel_trade:{offer_id}:{remaining_quantity}"}]]
            else:
                valid_lots = get_available_trade_amounts(
                    quantity=offer.quantity,
                    remaining_quantity=remaining_quantity,
                    is_wholesale=False,
                    lot_sizes=lot_sizes,
                )
                if not valid_lots:
                    payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
                    buttons = None
                else:
                    buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer_id}:{a}"} for a in valid_lots]]
            
            if buttons is not None:
                payload = {"chat_id": channel_id, "message_id": offer.channel_message_id, "reply_markup": {"inline_keyboard": buttons}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=10)
        return response.status_code == 200


# --- Endpoints ---

def _normalize_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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


async def _forward_trade_if_remote_home(
    db: AsyncSession,
    trade_data: TradeCreate,
    context: EffectiveOwnerActor,
    edge_received_at: datetime,
) -> Optional[JSONResponse]:
    offer = await db.get(Offer, trade_data.offer_id)
    if not offer or not is_remote_home(offer.home_server):
        return None

    owner_user = getattr(context, "owner_user", context)
    actor_user = getattr(context, "actor_user", owner_user)
    payload = {
        "offer_id": trade_data.offer_id,
        "quantity": trade_data.quantity,
        "responder_user_id": owner_user.id,
        "edge_received_at": edge_received_at.isoformat(),
        "source_server": current_server(),
        "idempotency_key": trade_data.idempotency_key,
    }
    if getattr(actor_user, "id", None) != owner_user.id:
        payload["actor_user_id"] = actor_user.id
    status_code, body = await forward_trade_to_home_server(offer.home_server, payload)
    return JSONResponse(status_code=status_code, content=body)


async def _execute_trade_authoritatively(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    edge_received_at: Optional[datetime] = None,
):
    """
    انجام معامله روی یک لفظ از MiniApp
    """
    from core.enums import UserRole
    import jdatetime
    owner_user = context.owner_user
    actor_user = context.actor_user
    
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
    
    # گرفتن لفظ با قفل
    offer = await db.get(Offer, trade_data.offer_id, with_for_update=True)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    expired_for_trade = await _is_offer_expired_for_trade(offer, edge_received_at)

    allow_in_flight_after_auto_expiry = (
        offer.status == OfferStatus.EXPIRED
        and edge_received_at is not None
        and not expired_for_trade
    )
    if (offer.status != OfferStatus.ACTIVE and not allow_in_flight_after_auto_expiry) or expired_for_trade:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ دیگر فعال نیست.")
    
    if offer.user_id == owner_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="نمی‌توانید روی لفظ خودتان معامله کنید.")
    
    # بررسی بلاک بین کاربران (پنهان - کاربر نباید متوجه بلاک شدن بشه)
    from core.services.block_service import is_blocked
    blocked, _ = await is_blocked(db, owner_user.id, offer.user_id)
    if blocked:
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
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=build_lot_unavailable_suggestion_payload(
                    offer_id=offer.id,
                    requested_amount=trade_data.quantity,
                    offer_type=offer.offer_type,
                    commodity_name=offer.commodity.name if offer.commodity else None,
                    price=offer.price,
                    remaining_quantity=offer.remaining_quantity or offer.quantity,
                    available_amounts=available_amounts,
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=amount_error
        )
    
    # بارگذاری روابط لفظ
    await db.refresh(offer, ["user", "commodity"])

    responder_customer_relation = await get_active_customer_relation_for_customer(db, owner_user.id)
    responder_customer_tier = getattr(
        getattr(responder_customer_relation, "customer_tier", None),
        "value",
        getattr(responder_customer_relation, "customer_tier", None),
    )
    is_tier2_customer_responder = (
        responder_customer_relation is not None
        and responder_customer_tier == CustomerTier.TIER_2.value
    )
    responder_owner_user_id = _coerce_trade_user_id(
        getattr(responder_customer_relation, "owner_user_id", None)
    )
    source_customer_relation = await get_active_customer_relation_for_customer(db, offer.user_id)
    source_customer_owner_user_id = _coerce_trade_user_id(
        getattr(source_customer_relation, "owner_user_id", None)
    )
    source_principal_user_id = source_customer_owner_user_id or offer.user_id
    responder_principal_user_id = responder_owner_user_id or owner_user.id

    executed_trade_price = offer.price
    if is_tier2_customer_responder:
        executed_trade_price = apply_customer_commission(
            offer.price,
            getattr(responder_customer_relation, "commission_rate", None),
            offer.offer_type,
        )

    def _require_trade_node_user(user_obj: object | None) -> object:
        if user_obj is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="امکان انجام این معامله وجود ندارد.",
            )
        return user_obj

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

    source_principal_user = _require_trade_node_user(source_principal_user)
    responder_principal_user = _require_trade_node_user(responder_principal_user)

    trade_execution_nodes: list[dict[str, object]] = [
        {
            "user_id": offer.user_id,
            "user": _require_trade_node_user(offer.user),
        }
    ]

    def _append_trade_execution_node(user_obj: object | None, user_id: object) -> None:
        normalized_user_id = _coerce_trade_user_id(user_id)
        if normalized_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="امکان انجام این معامله وجود ندارد.",
            )
        if trade_execution_nodes[-1]["user_id"] == normalized_user_id:
            return
        trade_execution_nodes.append(
            {
                "user_id": normalized_user_id,
                "user": _require_trade_node_user(user_obj),
            }
        )

    if source_principal_user_id != offer.user_id:
        _append_trade_execution_node(source_principal_user, source_principal_user_id)
    _append_trade_execution_node(responder_principal_user, responder_principal_user_id)
    _append_trade_execution_node(owner_user, owner_user.id)

    uses_customer_trade_chain = len(trade_execution_nodes) > 2

    if trade_data.idempotency_key:
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
            existing_identity_map = await _load_trade_identity_map_for_user_ids(
                db,
                [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
            )
            existing_customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(
                db,
                [existing_trade_obj.offer_user_id, existing_trade_obj.responder_user_id],
            )
            return trade_to_response(
                existing_trade_obj,
                identity_map=existing_identity_map,
                customer_relation_map=existing_customer_relation_map,
            )
    
    # گرفتن شماره معامله جدید
    max_trade_number = await db.scalar(select(func.max(Trade.trade_number)))
    next_trade_number = (max_trade_number or 9999) + 1
    
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
                trade_number=next_trade_number,
                offer_id=offer.id if leg_index == 0 else None,
                offer_user_id=int(offer_node["user_id"]),
                offer_user_mobile=getattr(leg_offer_user, "mobile_number", None),
                responder_user_id=int(responder_node["user_id"]),
                responder_user_mobile=getattr(leg_responder_user, "mobile_number", None),
                actor_user_id=actor_user.id,
                commodity_id=offer.commodity_id,
                trade_type=responder_trade_type,
                quantity=trade_quantity,
                price=executed_trade_price if leg_index == final_leg_index and is_tier2_customer_responder else offer.price,
                status=TradeStatus.COMPLETED,
                idempotency_key=trade_data.idempotency_key if leg_index == final_leg_index else None,
            )
            db.add(leg_trade)
            created_chain_trades.append(leg_trade)
            next_trade_number += 1

        response_trade_record = created_chain_trades[-1]
    else:
        response_trade_record = Trade(
            trade_number=next_trade_number,
            offer_id=offer.id,
            offer_user_id=offer.user_id,
            offer_user_mobile=offer.user.mobile_number if offer.user else None,
            responder_user_id=owner_user.id,
            responder_user_mobile=owner_user.mobile_number,
            actor_user_id=actor_user.id,
            commodity_id=offer.commodity_id,
            trade_type=responder_trade_type,
            quantity=trade_quantity,
            price=executed_trade_price,
            status=TradeStatus.COMPLETED,
            idempotency_key=trade_data.idempotency_key,
        )
        db.add(response_trade_record)

    response_trade_number = response_trade_record.trade_number
    
    # آپدیت لفظ
    offer.remaining_quantity -= trade_quantity
    
    # بروزرسانی لات‌ها - حذف مقدار معامله شده از لیست
    if offer.remaining_quantity <= 0:
        if offer.lot_sizes is not None:
            from sqlalchemy.orm.attributes import flag_modified
            offer.lot_sizes = None
            flag_modified(offer, "lot_sizes")
    elif offer.lot_sizes:
        from sqlalchemy.orm.attributes import flag_modified
        new_lot_sizes = list(offer.lot_sizes)
        if trade_quantity in new_lot_sizes:
            new_lot_sizes.remove(trade_quantity)
        offer.lot_sizes = new_lot_sizes if new_lot_sizes else None
        flag_modified(offer, "lot_sizes")  # اجبار SQLAlchemy برای تشخیص تغییر
    
    if offer.remaining_quantity <= 0:
        offer.status = OfferStatus.COMPLETED
    
    # Commit با محافظت Optimistic Locking
    try:
        await db.commit()
    except Exception as e:
        # بررسی StaleDataError (تغییر همزمان توسط کاربر دیگر)
        if "StaleDataError" in str(type(e).__name__) or "could not update" in str(e).lower():
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید."
            )
        raise
    
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
    offer_user_payload = _build_trade_participant_payload(
        "offer_user",
        user=response_offer_user,
        user_id=getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id,
        identity_map=participant_identity_map,
    )
    responder_user_payload = _build_trade_participant_payload(
        "responder_user",
        user=response_responder_user,
        user_id=getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id,
        identity_map=participant_identity_map,
    )
    offer_user_display_name = offer_user_payload.get("offer_user_name") or "نامشخص"
    responder_user_display_name = responder_user_payload.get("responder_user_name") or "نامشخص"
    
    # ===== ارسال پیام‌های تلگرام در Background (غیر-بلاکینگ) =====
    # این کار باعث می‌شود پاسخ API سریعتر برگردد
    
    # آپدیت دکمه‌های کانال (مستقیم - نه در background)
    try:
        await update_channel_buttons(offer)
    except Exception as e:
        logger.error(f"Failed to update channel buttons: {e}")
    
    # ارسال نوتیفیکیشن‌ها
    now = datetime.utcnow()
    jalali_dt = jdatetime.datetime.fromgregorian(datetime=now)
    trade_datetime = jalali_dt.strftime("%Y/%m/%d   %H:%M")
    
    # تعیین نوع و ایموجی
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

    def _build_trade_message_bundle(
        *,
        trade_price: int,
        trade_number: int,
        offer_user_name: str,
        responder_user_name: str,
        trade_path_summary: str | None = None,
    ) -> tuple[str, str, str, str]:
        trade_path_line = f"\n🧭 مسیر: {trade_path_summary}" if trade_path_summary else ""
        responder_msg = (
            f"{respond_emoji} <b>{respond_type_fa}</b>\n\n"
            f"💰 فی: {trade_price:,}\n"
            f"📦 تعداد: {trade_quantity}\n"
            f"🏷️ کالا: {offer.commodity.name}\n"
            f"👤 طرف معامله: {offer_user_name}\n"
            f"🔢 شماره معامله: {trade_number}\n"
            f"🕐 زمان معامله: {trade_datetime}"
            f"{trade_path_line}"
        )
        offer_owner_msg = (
            f"{offer_emoji} <b>{offer_type_fa}</b>\n\n"
            f"💰 فی: {trade_price:,}\n"
            f"📦 تعداد: {trade_quantity}\n"
            f"🏷️ کالا: {offer.commodity.name}\n"
            f"👤 طرف معامله: {responder_user_name}\n"
            f"🔢 شماره معامله: {trade_number}\n"
            f"🕐 زمان معامله: {trade_datetime}"
            f"{trade_path_line}"
        )
        notif_msg_responder = (
            f"{respond_emoji} {respond_type_fa}\n"
            f"💰 فی: {trade_price:,} | 📦 تعداد: {trade_quantity}\n"
            f"🏷️ کالا: {offer.commodity.name}\n"
            f"👤 طرف معامله: {offer_user_name}\n"
            f"🔢 شماره: {trade_number}"
            f"{trade_path_line}"
        )
        notif_msg_owner = (
            f"{offer_emoji} {offer_type_fa}\n"
            f"💰 فی: {trade_price:,} | 📦 تعداد: {trade_quantity}\n"
            f"🏷️ کالا: {offer.commodity.name}\n"
            f"👤 طرف معامله: {responder_user_name}\n"
            f"🔢 شماره: {trade_number}"
            f"{trade_path_line}"
        )
        return responder_msg, offer_owner_msg, notif_msg_responder, notif_msg_owner

    async def _create_trade_notifications_for_leg(
        *,
        audience_user_ids: list[int],
        message: str,
        extra_payload: dict[str, object | None],
    ) -> None:
        for audience_user_id in audience_user_ids:
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
                leg_offer_user = response_offer_user or leg_offer_user
                leg_responder_user = response_responder_user or leg_responder_user
            else:
                leg_trade_obj = leg_trade
            chain_leg_contexts.append(
                {
                    "trade": leg_trade_obj,
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
            )
            leg_responder_payload = _build_trade_participant_payload(
                "responder_user",
                user=leg_responder_user,
                user_id=getattr(leg_trade_obj, "responder_user_id", None),
                identity_map=participant_identity_map,
            )
            leg_trade_path_summary = _build_trade_path_payload(
                offer_user_id=getattr(leg_trade_obj, "offer_user_id", None),
                responder_user_id=getattr(leg_trade_obj, "responder_user_id", None),
                customer_relation_map=participant_customer_relation_map,
            ).get("trade_path_summary")
            leg_responder_msg, leg_offer_owner_msg, leg_notif_responder, leg_notif_owner = _build_trade_message_bundle(
                trade_price=getattr(leg_trade_obj, "price", offer.price),
                trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                offer_user_name=leg_offer_payload.get("offer_user_name") or "نامشخص",
                responder_user_name=leg_responder_payload.get("responder_user_name") or "نامشخص",
                trade_path_summary=leg_trade_path_summary,
            )

            responder_telegram_id = getattr(leg_responder_user, "telegram_id", None)
            if responder_telegram_id:
                background_tasks.add_task(send_telegram_message_sync, responder_telegram_id, leg_responder_msg)
            offer_telegram_id = getattr(leg_offer_user, "telegram_id", None)
            if offer_telegram_id:
                background_tasks.add_task(send_telegram_message_sync, offer_telegram_id, leg_offer_owner_msg)

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
                    message=leg_notif_responder,
                    extra_payload=_build_trade_notification_extra_payload(
                        "offer_user",
                        leg_offer_payload,
                        trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                    ),
                )
                await _create_trade_notifications_for_leg(
                    audience_user_ids=leg_offer_audience,
                    message=leg_notif_owner,
                    extra_payload=_build_trade_notification_extra_payload(
                        "responder_user",
                        leg_responder_payload,
                        trade_number=getattr(leg_trade_obj, "trade_number", response_trade_number),
                    ),
                )
            except:
                pass
    else:
        responder_msg, offer_owner_msg, notif_msg_responder, notif_msg_owner = _build_trade_message_bundle(
            trade_price=executed_trade_price,
            trade_number=response_trade_number,
            offer_user_name=offer_user_display_name,
            responder_user_name=responder_user_display_name,
            trade_path_summary=_build_trade_path_payload(
                offer_user_id=getattr(response_trade, "offer_user_id", None) or created_trade.offer_user_id,
                responder_user_id=getattr(response_trade, "responder_user_id", None) or created_trade.responder_user_id,
                customer_relation_map=participant_customer_relation_map,
            ).get("trade_path_summary"),
        )

        background_tasks.add_task(send_telegram_message_sync, owner_user.telegram_id, responder_msg)
        if offer.user:
            background_tasks.add_task(send_telegram_message_sync, offer.user.telegram_id, offer_owner_msg)

        responder_audience = [owner_user.id]
        offer_owner_audience = [offer.user_id]
        try:
            responder_audience = await build_trade_notification_audience_user_ids(db, [owner_user.id])
            offer_owner_audience = await build_trade_notification_audience_user_ids(db, [offer.user_id])
            responder_notification_payload = _build_trade_notification_extra_payload(
                "offer_user",
                offer_user_payload,
                trade_number=response_trade_number,
            )
            offer_owner_notification_payload = _build_trade_notification_extra_payload(
                "responder_user",
                responder_user_payload,
                trade_number=response_trade_number,
            )

            for audience_user_id in responder_audience:
                await create_user_notification(
                    db, audience_user_id, notif_msg_responder,
                    level=NotificationLevel.SUCCESS,
                    category=NotificationCategory.TRADE,
                    extra_payload=responder_notification_payload,
                )
            for audience_user_id in offer_owner_audience:
                await create_user_notification(
                    db, audience_user_id, notif_msg_owner,
                    level=NotificationLevel.SUCCESS,
                    category=NotificationCategory.TRADE,
                    extra_payload=offer_owner_notification_payload,
                )
        except:
            pass
    
    # افزایش شمارنده معامله
    # فقط پاسخ‌دهنده (کسی که روی لفظ دیگران معامله می‌کند) شمارنده‌اش افزایش می‌یابد
    # صاحب لفظ شمارنده‌اش افزایش نمی‌یابد (چون او فقط لفظ داده، فعالانه معامله نکرده)
    await increment_user_counter(db, owner_user, 'trade', trade_quantity)
    
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
    await publish_event("offer:updated", {
        "id": offer.id,
        "remaining_quantity": offer.remaining_quantity,
        "lot_sizes": offer.lot_sizes,
        "status": offer.status.value
    })
    
    return trade_to_response(
        response_trade,
        identity_map=participant_identity_map,
        customer_relation_map=participant_customer_relation_map,
        viewer_context=context,
        history_target_user_id=owner_user.id,
    )


@router.post("/", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def create_trade(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    edge_received_at = datetime.utcnow()
    forwarded_response = await _forward_trade_if_remote_home(db, trade_data, context, edge_received_at)
    if forwarded_response is not None:
        return forwarded_response

    return await _execute_trade_authoritatively(
        trade_data=trade_data,
        background_tasks=background_tasks,
        db=db,
        context=context,
        edge_received_at=edge_received_at,
    )


@router.post("/internal/execute", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def execute_trade_internal(
    internal_data: InternalTradeExecuteRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal trade signature")

    offer = await db.get(Offer, internal_data.offer_id)
    if offer and normalize_server(offer.home_server) != current_server():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع آفر نیست.")

    responder = await db.get(User, internal_data.responder_user_id)
    if not responder or responder.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر درخواست‌دهنده یافت نشد")

    actor_user = responder
    if internal_data.actor_user_id and internal_data.actor_user_id != responder.id:
        actor_user = await db.get(User, internal_data.actor_user_id)
        if not actor_user or actor_user.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر اجراکننده یافت نشد")

    return await _execute_trade_authoritatively(
        trade_data=TradeCreate(
            offer_id=internal_data.offer_id,
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
    )


@router.get("/my", response_model=List[TradeResponse])
async def get_my_trades(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات کاربر
    """
    from sqlalchemy import or_

    owner_user = context.owner_user
    
    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    ).where(
        or_(
            Trade.offer_user_id == owner_user.id,
            Trade.responder_user_id == owner_user.id,
            Trade.actor_user_id == owner_user.id,
        )
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
            selectinload(Trade.commodity)
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
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات با یک کاربر خاص
    """
    from sqlalchemy import and_, or_

    owner_user = context.owner_user
    
    # کاربر نمی‌تواند معاملات خودش با خودش را بگیرد (که منطقاً وجود ندارد)
    if other_user_id == owner_user.id:
        return []

    target_customer_relation = await _resolve_viewable_customer_history_relation(
        db,
        customer_user_id=other_user_id,
        context=context,
    )

    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    )

    if target_customer_relation is not None or _is_super_admin_trade_history_viewer(context):
        query = query.where(
            or_(
                Trade.offer_user_id == other_user_id,
                Trade.responder_user_id == other_user_id,
                Trade.actor_user_id == other_user_id,
            )
        )
    else:
        query = query.where(
            or_(
                and_(Trade.offer_user_id == owner_user.id, Trade.responder_user_id == other_user_id),
                and_(Trade.offer_user_id == other_user_id, Trade.responder_user_id == owner_user.id)
            )
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
