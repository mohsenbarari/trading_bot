import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

import schemas
from api.deps import get_effective_owner_actor_context
from core.audit_logger import audit_log
from core.config import settings
from core.db import get_db
from core.utils import utc_now_naive
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.services.customer_relation_service import (
    create_owner_customer_relation,
    is_user_customer,
    list_owner_customer_relations,
    load_customer_relation_invitation_map,
    unlink_owner_customer_relation,
    update_owner_customer_relation,
)
from core.services.session_service import get_active_sessions, logout_session
from core.sms import send_customer_invitation_sms
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.trade import Trade, TradeStatus
from models.session import UserSession


router = APIRouter()
CUSTOMER_STATS_PERIOD_DAYS = {1, 3, 7, 30, 90, 180}
CUSTOMER_COMMISSION_PRICE_UNIT_TOMAN = 1000


def build_customer_registration_link(invitation_token: str) -> str | None:
    frontend_url = (getattr(settings, "frontend_url", "") or "").strip()
    if not frontend_url:
        return None
    return f"{frontend_url}/register?token={invitation_token}"


def get_loaded_relation_customer_user(relation):
    if hasattr(relation, "__dict__"):
        return relation.__dict__.get("customer_user")
    return getattr(relation, "customer_user", None)


def serialize_customer_relation(relation, invitation=None) -> dict:
    customer_user = get_loaded_relation_customer_user(relation)
    return {
        "id": relation.id,
        "owner_user_id": relation.owner_user_id,
        "customer_user_id": relation.customer_user_id,
        "customer_account_name": getattr(customer_user, "account_name", None),
        "invitation_account_name": getattr(invitation, "account_name", None),
        "mobile_number": getattr(invitation, "mobile_number", None),
        "management_name": relation.management_name,
        "customer_tier": relation.customer_tier,
        "commission_rate": relation.commission_rate,
        "min_trade_quantity": relation.min_trade_quantity,
        "max_trade_quantity": relation.max_trade_quantity,
        "max_daily_trades": relation.max_daily_trades,
        "max_daily_commodity_volume": relation.max_daily_commodity_volume,
        "status": relation.status,
        "invitation_token": relation.invitation_token,
        "registration_link": build_customer_registration_link(relation.invitation_token),
        "expires_at": relation.expires_at,
        "activated_at": relation.activated_at,
        "deleted_at": relation.deleted_at,
        "created_at": relation.created_at,
    }


def serialize_customer_session(session: UserSession) -> schemas.CustomerSessionRead:
    return schemas.CustomerSessionRead(
        id=str(session.id),
        device_name=session.device_name,
        device_ip=session.device_ip,
        platform=session.platform.value if hasattr(session.platform, "value") else str(session.platform),
        home_server=session.home_server or "foreign",
        is_primary=session.is_primary,
        is_active=session.is_active,
        created_at=session.created_at,
        last_active_at=session.last_active_at,
    )


def audit_actor_context(context: EffectiveOwnerActor) -> dict:
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    return {
        "actor_id": getattr(actor_user, "id", None),
        "actor_role": getattr(getattr(actor_user, "role", None), "value", getattr(actor_user, "role", None)),
    }


def _coerce_optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trade_price(trade) -> int | None:
    return _coerce_optional_int(getattr(trade, "price", None))


def _trade_user_id(trade, field_name: str) -> int | None:
    return _coerce_optional_int(getattr(trade, field_name, None))


def _same_trade_context(left_trade, right_trade) -> bool:
    if _coerce_optional_int(getattr(left_trade, "commodity_id", None)) != _coerce_optional_int(
        getattr(right_trade, "commodity_id", None)
    ):
        return False
    if _coerce_optional_int(getattr(left_trade, "quantity", None)) != _coerce_optional_int(
        getattr(right_trade, "quantity", None)
    ):
        return False

    left_actor_id = _coerce_optional_int(getattr(left_trade, "actor_user_id", None))
    right_actor_id = _coerce_optional_int(getattr(right_trade, "actor_user_id", None))
    if left_actor_id is not None and right_actor_id is not None and left_actor_id != right_actor_id:
        return False
    return True


def _nearest_chain_trade_price(
    trade,
    *,
    owner_user_id: int,
    after_current_trade: bool,
    expected_owner_field: str,
    chain_candidates: list[Trade],
) -> int | None:
    trade_number = _coerce_optional_int(getattr(trade, "trade_number", None))
    if trade_number is None:
        return None

    matches = []
    for candidate in chain_candidates:
        candidate_trade_number = _coerce_optional_int(getattr(candidate, "trade_number", None))
        if candidate_trade_number is None or candidate_trade_number == trade_number:
            continue
        if after_current_trade and candidate_trade_number <= trade_number:
            continue
        if not after_current_trade and candidate_trade_number >= trade_number:
            continue
        if _trade_user_id(candidate, expected_owner_field) != owner_user_id:
            continue
        if not _same_trade_context(trade, candidate):
            continue
        candidate_price = _trade_price(candidate)
        if candidate_price is not None:
            matches.append((candidate_trade_number, candidate_price))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=not after_current_trade)
    return matches[0][1]


def _historical_customer_base_price(
    trade,
    *,
    owner_user_id: int,
    customer_user_id: int,
    chain_candidates: list[Trade],
) -> int | None:
    offer_price = _coerce_optional_int(getattr(getattr(trade, "offer", None), "price", None))
    if offer_price is not None:
        return offer_price

    offer_user_id = _trade_user_id(trade, "offer_user_id")
    responder_user_id = _trade_user_id(trade, "responder_user_id")
    if offer_user_id == owner_user_id and responder_user_id == customer_user_id:
        return _nearest_chain_trade_price(
            trade,
            owner_user_id=owner_user_id,
            after_current_trade=False,
            expected_owner_field="responder_user_id",
            chain_candidates=chain_candidates,
        )
    if offer_user_id == customer_user_id and responder_user_id == owner_user_id:
        return _nearest_chain_trade_price(
            trade,
            owner_user_id=owner_user_id,
            after_current_trade=True,
            expected_owner_field="offer_user_id",
            chain_candidates=chain_candidates,
        )
    return None


def calculate_customer_trade_commission_profit(
    trade,
    *,
    owner_user_id: int,
    customer_user_id: int,
    chain_candidates: list[Trade],
) -> int:
    quantity = _coerce_optional_int(getattr(trade, "quantity", None)) or 0
    trade_price = _trade_price(trade)
    base_price = _historical_customer_base_price(
        trade,
        owner_user_id=owner_user_id,
        customer_user_id=customer_user_id,
        chain_candidates=chain_candidates,
    )
    if quantity <= 0 or trade_price is None or base_price is None:
        return 0
    return abs(trade_price - base_price) * quantity * CUSTOMER_COMMISSION_PRICE_UNIT_TOMAN


def _normalize_customer_history_bound_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _customer_relation_trade_stats_bounds(relation: CustomerRelation | object) -> tuple[datetime | None, datetime | None]:
    start_at = _normalize_customer_history_bound_datetime(
        getattr(relation, "activated_at", None) or getattr(relation, "created_at", None)
    )
    raw_status_value = getattr(relation, "status", None)
    status_value = getattr(raw_status_value, "value", raw_status_value)
    end_at = _normalize_customer_history_bound_datetime(getattr(relation, "deleted_at", None))
    if end_at is None and status_value in {
        CustomerRelationStatus.EXPIRED.value,
        CustomerRelationStatus.REVOKED.value,
        CustomerRelationStatus.DELETED.value,
    }:
        end_at = _normalize_customer_history_bound_datetime(
            getattr(relation, "expires_at", None) or getattr(relation, "updated_at", None)
        )
    return start_at, end_at


async def ensure_owner_context(context: EffectiveOwnerActor, db: AsyncSession) -> None:
    if context.is_accountant_context:
        raise HTTPException(status_code=403, detail="Accountants cannot manage owner customers")
    actor_user = getattr(context, "actor_user", None) or context.owner_user
    if hasattr(db, "execute") and await is_user_customer(db, actor_user.id):
        raise HTTPException(status_code=403, detail="Customers cannot manage owner customers")


async def get_active_owner_customer_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> CustomerRelation:
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.id == relation_id,
            CustomerRelation.owner_user_id == owner_user_id,
        )
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if relation is None:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")
    if relation.deleted_at is not None or relation.status != CustomerRelationStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="فقط مشتری فعال نشست قابل مدیریت دارد")
    customer_user = relation.customer_user
    if relation.customer_user_id is None or customer_user is None or customer_user.is_deleted:
        raise HTTPException(status_code=400, detail="برای این مشتری نشست فعالی قابل مدیریت نیست")
    return relation


async def get_owner_customer_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> CustomerRelation:
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.id == relation_id,
            CustomerRelation.owner_user_id == owner_user_id,
        )
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if relation is None:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")
    return relation


async def get_active_customer_session(
    db: AsyncSession,
    *,
    customer_user_id: int,
    session_id: uuid.UUID,
) -> UserSession:
    stmt = select(UserSession).where(
        and_(
            UserSession.id == session_id,
            UserSession.user_id == customer_user_id,
            UserSession.is_active == True,
        )
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="نشست یافت نشد")
    return session


@router.get("/owner-relations", response_model=list[schemas.CustomerRelationRead])
async def list_my_customers(
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relations = await list_owner_customer_relations(db, owner_user_id=context.owner_user.id)
    invitation_map = await load_customer_relation_invitation_map(
        db,
        [relation.invitation_token for relation in relations],
    )
    return [
        serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))
        for relation in relations
    ]


@router.post("/owner-relations", response_model=schemas.CustomerRelationRead)
async def create_my_customer(
    payload: schemas.CustomerRelationCreate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation, invitation = await create_owner_customer_relation(
        db,
        owner_user=context.owner_user,
        account_name=payload.account_name,
        management_name=payload.management_name,
        mobile_number=payload.mobile_number,
        customer_tier=payload.customer_tier,
        commission_rate=payload.commission_rate,
        min_trade_quantity=payload.min_trade_quantity,
        max_trade_quantity=payload.max_trade_quantity,
        max_daily_trades=payload.max_daily_trades,
        max_daily_commodity_volume=payload.max_daily_commodity_volume,
    )

    registration_link = build_customer_registration_link(relation.invitation_token)
    if registration_link:
        send_customer_invitation_sms(
            mobile=invitation.mobile_number,
            management_name=relation.management_name,
            web_link=registration_link,
        )

    audit_log(
        "customer.link",
        target_type="customer_relation",
        target_id=relation.id,
        after_summary={
            "owner_user_id": relation.owner_user_id,
            "customer_user_id": relation.customer_user_id,
            "customer_tier": relation.customer_tier,
            "status": relation.status,
        },
        **audit_actor_context(context),
    )

    return serialize_customer_relation(relation, invitation=invitation)


@router.delete("/owner-relations/{relation_id}", response_model=schemas.CustomerRelationRead)
async def unlink_my_customer(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await unlink_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    invitation_map = await load_customer_relation_invitation_map(db, [relation.invitation_token])
    audit_log(
        "customer.unlink",
        target_type="customer_relation",
        target_id=relation.id,
        before_summary={
            "owner_user_id": relation.owner_user_id,
            "customer_user_id": relation.customer_user_id,
            "customer_tier": relation.customer_tier,
            "status": relation.status,
        },
        after_summary={"deleted_at": relation.deleted_at, "status": relation.status},
        **audit_actor_context(context),
    )
    return serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))


@router.patch("/owner-relations/{relation_id}", response_model=schemas.CustomerRelationRead)
async def update_my_customer(
    relation_id: int,
    payload: schemas.CustomerRelationUpdate,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await update_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
        update_data=payload.model_dump(exclude_unset=True),
    )
    relation = await get_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation.id,
    )
    invitation_map = await load_customer_relation_invitation_map(db, [relation.invitation_token])
    audit_log(
        "customer.update",
        target_type="customer_relation",
        target_id=relation.id,
        after_summary={
            "updated_fields": sorted(payload.model_dump(exclude_unset=True).keys()),
            "customer_tier": relation.customer_tier,
            "status": relation.status,
        },
        **audit_actor_context(context),
    )
    return serialize_customer_relation(relation, invitation=invitation_map.get(relation.invitation_token))


@router.get("/owner-relations/{relation_id}/trade-stats", response_model=schemas.CustomerTradeStatsRead)
async def get_my_customer_trade_stats(
    relation_id: int,
    days: int = Query(7, description="Allowed values: 1, 3, 7, 30, 90, 180"),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    if days not in CUSTOMER_STATS_PERIOD_DAYS:
        raise HTTPException(status_code=400, detail="بازه آمار مشتری نامعتبر است")

    relation = await get_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    if relation.customer_user_id is None:
        raise HTTPException(status_code=400, detail="آمار فقط برای مشتری ثبت‌شده قابل محاسبه است")

    to_date = utc_now_naive()
    from_date = to_date - timedelta(days=days)
    relation_start_at, relation_end_at = _customer_relation_trade_stats_bounds(relation)
    query_from_date = max(from_date, relation_start_at) if relation_start_at is not None else from_date
    query_to_date = min(to_date, relation_end_at) if relation_end_at is not None else to_date
    stmt = (
        select(Trade)
        .options(selectinload(Trade.offer), selectinload(Trade.commodity))
        .where(
            Trade.status == TradeStatus.COMPLETED,
            Trade.created_at >= query_from_date,
            Trade.created_at <= query_to_date,
            or_(
                Trade.offer_user_id == relation.customer_user_id,
                Trade.responder_user_id == relation.customer_user_id,
            ),
        )
        .order_by(Trade.created_at.desc(), Trade.id.desc())
    )
    trades = list((await db.execute(stmt)).scalars().all())
    trade_numbers = [
        trade_number
        for trade_number in (_coerce_optional_int(getattr(trade, "trade_number", None)) for trade in trades)
        if trade_number is not None
    ]
    chain_candidates: list[Trade] = []
    if trade_numbers:
        nearby_trade_numbers = {
            candidate_trade_number
            for trade_number in trade_numbers
            for candidate_trade_number in range(trade_number - 3, trade_number + 4)
            if candidate_trade_number > 0
        }
        chain_stmt = (
            select(Trade)
            .where(
                Trade.status == TradeStatus.COMPLETED,
                Trade.trade_number.in_(nearby_trade_numbers),
                or_(
                    Trade.offer_user_id == relation.owner_user_id,
                    Trade.responder_user_id == relation.owner_user_id,
                ),
            )
            .order_by(Trade.trade_number.asc())
        )
        chain_candidates = list((await db.execute(chain_stmt)).scalars().all())

    commodity_totals: dict[int, dict[str, int | str]] = {}
    total_quantity = 0
    commission_profit = 0
    for trade in trades:
        quantity = int(getattr(trade, "quantity", 0) or 0)
        total_quantity += quantity
        commodity_id = int(getattr(trade, "commodity_id", 0) or 0)
        commodity_name = getattr(getattr(trade, "commodity", None), "name", None) or "نامشخص"
        bucket = commodity_totals.setdefault(
            commodity_id,
            {
                "commodity_id": commodity_id,
                "commodity_name": commodity_name,
                "total_quantity": 0,
            },
        )
        bucket["total_quantity"] = int(bucket["total_quantity"]) + quantity

        commission_profit += calculate_customer_trade_commission_profit(
            trade,
            owner_user_id=relation.owner_user_id,
            customer_user_id=relation.customer_user_id,
            chain_candidates=chain_candidates,
        )

    commodities = sorted(
        commodity_totals.values(),
        key=lambda item: (-int(item["total_quantity"]), str(item["commodity_name"])),
    )
    return {
        "relation_id": relation.id,
        "customer_user_id": relation.customer_user_id,
        "period_days": days,
        "from_date": from_date,
        "to_date": to_date,
        "trade_count": len(trades),
        "total_quantity": total_quantity,
        "commission_profit_toman": commission_profit,
        "commodities": commodities,
        "profit_calculation_note": "سود از اختلاف قیمت ثبت‌شده در معامله مشتری و قیمت اصلی همان زنجیره، با تبدیل واحد قیمت بازار به تومان کامل محاسبه می‌شود.",
    }


@router.get("/owner-relations/{relation_id}/sessions", response_model=list[schemas.CustomerSessionRead])
async def list_my_customer_sessions(
    relation_id: int,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    relation = await get_active_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    sessions = await get_active_sessions(db, relation.customer_user_id)
    return [serialize_customer_session(session) for session in sessions]


@router.delete("/owner-relations/{relation_id}/sessions/{session_id}", response_model=schemas.CustomerSessionTerminateResponse)
async def terminate_my_customer_session(
    relation_id: int,
    session_id: str,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    db: AsyncSession = Depends(get_db),
):
    await ensure_owner_context(context, db)
    try:
        normalized_session_id = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="شناسه نشست نامعتبر است") from exc

    relation = await get_active_owner_customer_relation(
        db,
        owner_user_id=context.owner_user.id,
        relation_id=relation_id,
    )
    session = await get_active_customer_session(
        db,
        customer_user_id=relation.customer_user_id,
        session_id=normalized_session_id,
    )
    promoted_session = await logout_session(db, session)
    audit_log(
        "customer.session_terminate",
        target_type="customer_session",
        target_id=session.id,
        after_summary={
            "relation_id": relation.id,
            "customer_user_id": relation.customer_user_id,
            "promoted_primary_session_id": str(promoted_session.id) if promoted_session else None,
        },
        **audit_actor_context(context),
    )
    return {
        "detail": "نشست مشتری با موفقیت پایان یافت",
        "terminated_session_id": str(session.id),
        "promoted_primary_session_id": str(promoted_session.id) if promoted_session else None,
    }
