"""Shared customer-relation helpers for the phased customer rollout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import secrets
import string

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.utils import normalize_account_name, normalize_persian_numerals, utc_now
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation
from models.offer import OfferType
from models.user import User, UserRole


CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES = (
    CustomerRelationStatus.PENDING,
    CustomerRelationStatus.ACTIVE,
)
CUSTOMER_INVITATION_PREFIX = "CUST-"
CUSTOMER_PENDING_LIFETIME = timedelta(days=2)
PRICE_ROUNDING_UNIT = Decimal("100")


@dataclass(frozen=True)
class CustomerOfferReadModel:
    raw_price: int
    market_published_price: int
    viewer_effective_price: int
    customer_badge_visible: bool = False
    customer_management_name: str | None = None
    customer_tier: str | None = None


def _utcnow_naive():
    return utc_now().replace(tzinfo=None)


def _normalize_mobile_number(value: str) -> str:
    return normalize_persian_numerals((value or "").strip())


def is_customer_invitation_token(token: str | None) -> bool:
    return bool((token or "").startswith(CUSTOMER_INVITATION_PREFIX))


def generate_customer_invitation_token() -> str:
    return CUSTOMER_INVITATION_PREFIX + secrets.token_hex(16)


def generate_customer_short_code() -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(8))


def _normalize_offer_type(offer_type: OfferType | str) -> str:
    value = offer_type.value if hasattr(offer_type, "value") else str(offer_type or "")
    normalized = value.strip().lower()
    if normalized not in {OfferType.BUY.value, OfferType.SELL.value}:
        raise ValueError("offer_type must be 'buy' or 'sell'")
    return normalized


def _normalize_decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid") from exc


def _normalize_non_negative_int(value: object, *, name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid") from exc
    if normalized < 0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


def get_effective_max_customers(owner_user: User) -> int:
    raw_limit = getattr(owner_user, "max_customers", 5)
    try:
        normalized_limit = int(raw_limit)
    except (TypeError, ValueError):
        normalized_limit = 5
    return max(0, normalized_limit)


async def sweep_expired_pending_customer_relations(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    invitation_token: str | None = None,
) -> list[CustomerRelation]:
    now = _utcnow_naive()
    stmt = (
        select(CustomerRelation)
        .where(
            CustomerRelation.status == CustomerRelationStatus.PENDING,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.expires_at.is_not(None),
            CustomerRelation.expires_at <= now,
        )
        .order_by(CustomerRelation.id.asc())
    )
    if owner_user_id is not None:
        stmt = stmt.where(CustomerRelation.owner_user_id == owner_user_id)
    if invitation_token is not None:
        stmt = stmt.where(CustomerRelation.invitation_token == invitation_token)

    expired_relations = list((await db.execute(stmt)).scalars().all())
    for relation in expired_relations:
        relation.status = CustomerRelationStatus.EXPIRED
        relation.deleted_at = now

    return expired_relations


async def get_customer_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> CustomerRelation | None:
    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(CustomerRelation.invitation_token == invitation_token)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_pending_customer_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> CustomerRelation | None:
    expired_relations = await sweep_expired_pending_customer_relations(db, invitation_token=invitation_token)
    if expired_relations:
        await db.commit()

    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(
            CustomerRelation.invitation_token == invitation_token,
            CustomerRelation.status == CustomerRelationStatus.PENDING,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_customer_relation_for_customer(
    db: AsyncSession,
    customer_user_id: int,
) -> CustomerRelation | None:
    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(
            CustomerRelation.customer_user_id == customer_user_id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.id.asc())
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_customer_relation_for_user(
    db: AsyncSession,
    user_id: int,
) -> CustomerRelation | None:
    return await get_active_customer_relation_for_customer(db, user_id)


async def get_owner_for_customer(
    db: AsyncSession,
    customer_user_id: int,
) -> User | None:
    relation = await get_active_customer_relation_for_customer(db, customer_user_id)
    if relation is None:
        return None
    return relation.owner_user


async def is_user_customer(db: AsyncSession, user_id: int) -> bool:
    return await get_active_customer_relation_for_customer(db, user_id) is not None


async def list_active_customers_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> list[CustomerRelation]:
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == owner_user_id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.created_at.asc(), CustomerRelation.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_owner_customer_relations(
    db: AsyncSession,
    owner_user_id: int,
) -> list[CustomerRelation]:
    expired_relations = await sweep_expired_pending_customer_relations(db, owner_user_id=owner_user_id)
    if expired_relations:
        await db.commit()

    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == owner_user_id,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.status.in_(CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES),
        )
        .order_by(CustomerRelation.created_at.desc(), CustomerRelation.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def load_customer_relation_invitation_map(
    db: AsyncSession,
    invitation_tokens: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Invitation]:
    normalized_tokens = [token.strip() for token in invitation_tokens if isinstance(token, str) and token.strip()]
    if not normalized_tokens:
        return {}

    stmt = select(Invitation).where(Invitation.token.in_(normalized_tokens))
    invitations = list((await db.execute(stmt)).scalars().all())
    return {invitation.token: invitation for invitation in invitations if getattr(invitation, "token", None)}


def _normalize_customer_tier_input(value: CustomerTier | str | None) -> CustomerTier:
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value or CustomerTier.TIER_1.value).strip().lower()
    try:
        return CustomerTier(normalized)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="سطح مشتری نامعتبر است") from exc


def _normalize_customer_commission_rate(
    value: Decimal | float | int | str | None,
    *,
    customer_tier: CustomerTier,
) -> Decimal | None:
    if customer_tier != CustomerTier.TIER_2:
        return None
    if value is None or str(value).strip() == "":
        return None
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="نرخ کارمزد مشتری نامعتبر است") from exc
    if normalized < 0 or normalized > 100:
        raise HTTPException(status_code=400, detail="نرخ کارمزد مشتری باید بین ۰ تا ۱۰۰ باشد")
    return normalized.quantize(Decimal("0.01"))


def _normalize_optional_customer_limit(value: object, *, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{name} نامعتبر است") from exc
    if normalized < 0:
        raise HTTPException(status_code=400, detail=f"{name} باید صفر یا بیشتر باشد")
    return normalized


def _validate_customer_trade_limit_bounds(*, min_trade_quantity: int | None, max_trade_quantity: int | None) -> None:
    if (
        min_trade_quantity is not None
        and max_trade_quantity is not None
        and min_trade_quantity > max_trade_quantity
    ):
        raise HTTPException(status_code=400, detail="حداقل مقدار معامله نمی‌تواند از حداکثر بیشتر باشد")


async def create_owner_customer_relation(
    db: AsyncSession,
    *,
    owner_user: User,
    account_name: str,
    management_name: str,
    mobile_number: str,
    customer_tier: CustomerTier | str = CustomerTier.TIER_1,
    commission_rate: Decimal | float | int | str | None = None,
    min_trade_quantity: int | None = None,
    max_trade_quantity: int | None = None,
    max_daily_trades: int | None = None,
    max_daily_commodity_volume: int | None = None,
) -> tuple[CustomerRelation, Invitation]:
    normalized_account_name = normalize_account_name((account_name or "").strip())
    normalized_mobile = _normalize_mobile_number(mobile_number)
    normalized_management_name = (management_name or "").strip()
    normalized_tier = _normalize_customer_tier_input(customer_tier)
    normalized_min_trade = _normalize_optional_customer_limit(min_trade_quantity, name="حداقل مقدار معامله")
    normalized_max_trade = _normalize_optional_customer_limit(max_trade_quantity, name="حداکثر مقدار معامله")
    normalized_max_daily_trades = _normalize_optional_customer_limit(max_daily_trades, name="حداکثر تعداد معاملات روزانه")
    normalized_max_daily_volume = _normalize_optional_customer_limit(max_daily_commodity_volume, name="حداکثر حجم روزانه")
    normalized_commission_rate = _normalize_customer_commission_rate(
        commission_rate,
        customer_tier=normalized_tier,
    )

    if not normalized_account_name:
        raise HTTPException(status_code=400, detail="نام کاربری نامعتبر است")
    if not normalized_management_name:
        raise HTTPException(status_code=400, detail="نام مدیریتی مشتری الزامی است")
    if not normalized_mobile.startswith("09") or len(normalized_mobile) != 11:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است")
    _validate_customer_trade_limit_bounds(
        min_trade_quantity=normalized_min_trade,
        max_trade_quantity=normalized_max_trade,
    )

    await validate_customer_capacity(db, owner_user)

    existing_user_stmt = select(User).where(
        or_(
            User.account_name == normalized_account_name,
            User.mobile_number == normalized_mobile,
        )
    )
    existing_user = (await db.execute(existing_user_stmt)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

    duplicate_relation_stmt = (
        select(CustomerRelation)
        .join(Invitation, Invitation.token == CustomerRelation.invitation_token)
        .where(
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.status.in_(CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES),
            or_(
                Invitation.account_name == normalized_account_name,
                Invitation.mobile_number == normalized_mobile,
            ),
        )
    )
    duplicate_relation = (await db.execute(duplicate_relation_stmt)).scalar_one_or_none()
    if duplicate_relation:
        raise HTTPException(status_code=400, detail="یک مشتری pending یا active با این نام کاربری یا موبایل وجود دارد")

    duplicate_management_stmt = select(CustomerRelation).where(
        CustomerRelation.owner_user_id == owner_user.id,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.management_name == normalized_management_name,
    )
    duplicate_management = (await db.execute(duplicate_management_stmt)).scalar_one_or_none()
    if duplicate_management:
        raise HTTPException(status_code=400, detail="این نام مدیریتی قبلاً برای یکی از مشتریان این مالک استفاده شده است")

    invitation_token = generate_customer_invitation_token()
    short_code = generate_customer_short_code()
    expires_at = _utcnow_naive() + CUSTOMER_PENDING_LIFETIME

    invitation = Invitation(
        account_name=normalized_account_name,
        mobile_number=normalized_mobile,
        role=UserRole.STANDARD,
        token=invitation_token,
        short_code=short_code,
        created_by_id=owner_user.id,
        expires_at=expires_at,
    )
    relation = CustomerRelation(
        owner_user_id=owner_user.id,
        customer_user_id=None,
        created_by_user_id=owner_user.id,
        invitation_token=invitation_token,
        management_name=normalized_management_name,
        customer_tier=normalized_tier,
        commission_rate=normalized_commission_rate,
        min_trade_quantity=normalized_min_trade,
        max_trade_quantity=normalized_max_trade,
        max_daily_trades=normalized_max_daily_trades,
        max_daily_commodity_volume=normalized_max_daily_volume,
        status=CustomerRelationStatus.PENDING,
        expires_at=expires_at,
    )

    db.add(invitation)
    db.add(relation)
    await db.commit()
    await db.refresh(invitation)
    await db.refresh(relation)
    return relation, invitation


async def cancel_pending_customer_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
) -> CustomerRelation:
    stmt = select(CustomerRelation).where(
        CustomerRelation.id == relation_id,
        CustomerRelation.owner_user_id == owner_user_id,
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if not relation:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")
    if relation.deleted_at is not None or relation.status != CustomerRelationStatus.PENDING:
        raise HTTPException(status_code=400, detail="فقط دعوت مشتری pending قابل لغو است")

    now = _utcnow_naive()
    relation.status = CustomerRelationStatus.REVOKED
    relation.deleted_at = now

    invitation_stmt = select(Invitation).where(Invitation.token == relation.invitation_token)
    invitation = (await db.execute(invitation_stmt)).scalar_one_or_none()
    if invitation:
        invitation.is_used = True
        invitation.expires_at = now

    await db.commit()
    await db.refresh(relation)
    return relation


async def unlink_owner_customer_relation(
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
    if not relation:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")

    if relation.deleted_at is not None or relation.status in (
        CustomerRelationStatus.EXPIRED,
        CustomerRelationStatus.REVOKED,
        CustomerRelationStatus.DELETED,
    ):
        raise HTTPException(status_code=400, detail="این رابطه قبلاً بسته شده است")

    if relation.status == CustomerRelationStatus.PENDING:
        return await cancel_pending_customer_relation(
            db,
            owner_user_id=owner_user_id,
            relation_id=relation_id,
        )

    if relation.status != CustomerRelationStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="فقط مشتری pending یا active قابل قطع ارتباط است")

    from core.services.user_deletion_service import delete_user_account

    customer_user = relation.customer_user
    if customer_user and not customer_user.is_deleted:
        await delete_user_account(db, customer_user)

    now = _utcnow_naive()
    relation.status = CustomerRelationStatus.DELETED
    relation.deleted_at = now

    await db.commit()
    await db.refresh(relation)
    return relation


async def update_owner_customer_relation(
    db: AsyncSession,
    *,
    owner_user_id: int,
    relation_id: int,
    update_data: dict[str, object],
) -> CustomerRelation:
    stmt = select(CustomerRelation).where(
        CustomerRelation.id == relation_id,
        CustomerRelation.owner_user_id == owner_user_id,
    )
    relation = (await db.execute(stmt)).scalar_one_or_none()
    if not relation:
        raise HTTPException(status_code=404, detail="رابطه مشتری یافت نشد")
    if relation.deleted_at is not None or relation.status not in CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES:
        raise HTTPException(status_code=400, detail="فقط مشتری pending یا active قابل ویرایش است")

    current_tier = _normalize_customer_tier_input(getattr(relation, "customer_tier", CustomerTier.TIER_1))
    if "customer_tier" in update_data:
        current_tier = _normalize_customer_tier_input(update_data.get("customer_tier"))
        relation.customer_tier = current_tier

    if "commission_rate" in update_data or "customer_tier" in update_data:
        commission_input = update_data.get("commission_rate", getattr(relation, "commission_rate", None))
        relation.commission_rate = _normalize_customer_commission_rate(
            commission_input,
            customer_tier=current_tier,
        )

    if "min_trade_quantity" in update_data:
        relation.min_trade_quantity = _normalize_optional_customer_limit(
            update_data.get("min_trade_quantity"),
            name="حداقل مقدار معامله",
        )
    if "max_trade_quantity" in update_data:
        relation.max_trade_quantity = _normalize_optional_customer_limit(
            update_data.get("max_trade_quantity"),
            name="حداکثر مقدار معامله",
        )
    _validate_customer_trade_limit_bounds(
        min_trade_quantity=getattr(relation, "min_trade_quantity", None),
        max_trade_quantity=getattr(relation, "max_trade_quantity", None),
    )

    if "max_daily_trades" in update_data:
        relation.max_daily_trades = _normalize_optional_customer_limit(
            update_data.get("max_daily_trades"),
            name="حداکثر تعداد معاملات روزانه",
        )
    if "max_daily_commodity_volume" in update_data:
        relation.max_daily_commodity_volume = _normalize_optional_customer_limit(
            update_data.get("max_daily_commodity_volume"),
            name="حداکثر حجم روزانه",
        )

    await db.commit()
    await db.refresh(relation)
    return relation


async def count_capacity_tracked_customers_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> int:
    stmt = select(func.count(CustomerRelation.id)).where(
        CustomerRelation.owner_user_id == owner_user_id,
        CustomerRelation.status.in_(CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES),
        CustomerRelation.deleted_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def validate_customer_capacity(
    db: AsyncSession,
    owner_user: User,
    *,
    additional_slots: int = 1,
) -> tuple[int, int]:
    await sweep_expired_pending_customer_relations(db, owner_user_id=owner_user.id)
    current_count = await count_capacity_tracked_customers_for_owner(db, owner_user.id)
    max_customers = get_effective_max_customers(owner_user)
    requested_total = current_count + max(0, additional_slots)
    if requested_total > max_customers:
        raise HTTPException(
            status_code=400,
            detail="Owner has reached the maximum number of customers",
        )
    return current_count, max_customers


async def validate_owner_customer_capacity(
    db: AsyncSession,
    owner_user: User,
    *,
    additional_slots: int = 1,
) -> tuple[int, int]:
    return await validate_customer_capacity(db, owner_user, additional_slots=additional_slots)


def round_customer_price(adjusted_price: Decimal | int | str, offer_type: OfferType | str) -> int:
    normalized_price = _normalize_decimal(adjusted_price, name="adjusted_price")
    normalized_offer_type = _normalize_offer_type(offer_type)
    rounding_mode = ROUND_FLOOR if normalized_offer_type == OfferType.BUY.value else ROUND_CEILING
    rounded_units = (normalized_price / PRICE_ROUNDING_UNIT).to_integral_value(rounding=rounding_mode)
    return int(rounded_units * PRICE_ROUNDING_UNIT)


def apply_customer_commission(
    raw_price: Decimal | int | str,
    rate: Decimal | float | int | str | None,
    offer_type: OfferType | str,
) -> int:
    normalized_price = _normalize_decimal(raw_price, name="raw_price")
    normalized_offer_type = _normalize_offer_type(offer_type)
    if normalized_price <= 0:
        raise ValueError("raw_price must be positive")

    if rate is None:
        return int(normalized_price)

    normalized_rate = _normalize_decimal(rate, name="rate")
    if normalized_rate <= 0:
        return int(normalized_price)

    delta = normalized_price * normalized_rate / Decimal("100")
    adjusted_price = normalized_price - delta if normalized_offer_type == OfferType.BUY.value else normalized_price + delta
    return round_customer_price(adjusted_price, normalized_offer_type)


def build_customer_offer_read_model(
    *,
    raw_price: Decimal | int | str,
    offer_type: OfferType | str,
    viewer_user_id: int | None = None,
    offer_owner_relation: CustomerRelation | object | None = None,
    viewer_customer_relation: CustomerRelation | object | None = None,
) -> CustomerOfferReadModel:
    normalized_raw_price = int(_normalize_decimal(raw_price, name="raw_price"))
    if normalized_raw_price <= 0:
        raise ValueError("raw_price must be positive")

    normalized_offer_type = _normalize_offer_type(offer_type)

    owner_relation_status = getattr(getattr(offer_owner_relation, "status", None), "value", getattr(offer_owner_relation, "status", None))
    owner_relation_tier = getattr(getattr(offer_owner_relation, "customer_tier", None), "value", getattr(offer_owner_relation, "customer_tier", None))
    owner_user_id = getattr(offer_owner_relation, "owner_user_id", None)
    customer_badge_visible = (
        offer_owner_relation is not None
        and owner_relation_status == CustomerRelationStatus.ACTIVE.value
        and viewer_user_id is not None
        and owner_user_id == viewer_user_id
    )

    viewer_relation_status = getattr(getattr(viewer_customer_relation, "status", None), "value", getattr(viewer_customer_relation, "status", None))
    viewer_relation_tier = getattr(getattr(viewer_customer_relation, "customer_tier", None), "value", getattr(viewer_customer_relation, "customer_tier", None))
    viewer_rate = getattr(viewer_customer_relation, "commission_rate", None)
    if viewer_relation_status == CustomerRelationStatus.ACTIVE.value and viewer_relation_tier == "tier2":
        viewer_effective_price = apply_customer_commission(normalized_raw_price, viewer_rate, normalized_offer_type)
    else:
        viewer_effective_price = normalized_raw_price

    return CustomerOfferReadModel(
        raw_price=normalized_raw_price,
        market_published_price=normalized_raw_price,
        viewer_effective_price=viewer_effective_price,
        customer_badge_visible=customer_badge_visible,
        customer_management_name=getattr(offer_owner_relation, "management_name", None) if customer_badge_visible else None,
        customer_tier=owner_relation_tier if customer_badge_visible else None,
    )


async def load_offer_customer_read_context(
    db: AsyncSession,
    *,
    offer_owner_user_ids: list[int] | tuple[int, ...] | set[int],
    viewer_user_id: int | None,
) -> tuple[dict[int, CustomerRelation], CustomerRelation | None]:
    normalized_offer_owner_ids: list[int] = []
    seen_owner_ids: set[int] = set()
    for raw_user_id in offer_owner_user_ids:
        try:
            normalized_user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        if normalized_user_id <= 0 or normalized_user_id in seen_owner_ids:
            continue
        seen_owner_ids.add(normalized_user_id)
        normalized_offer_owner_ids.append(normalized_user_id)

    owner_relation_map: dict[int, CustomerRelation] = {}
    if normalized_offer_owner_ids:
        owner_relation_stmt = select(CustomerRelation).where(
            CustomerRelation.customer_user_id.in_(normalized_offer_owner_ids),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        owner_relations = list((await db.execute(owner_relation_stmt)).scalars().all())
        owner_relation_map = {
            relation.customer_user_id: relation
            for relation in owner_relations
            if relation.customer_user_id is not None
        }

    viewer_relation: CustomerRelation | None = None
    if viewer_user_id is not None:
        viewer_relation = owner_relation_map.get(viewer_user_id)
        if viewer_relation is None:
            viewer_relation = await get_active_customer_relation_for_customer(db, viewer_user_id)

    return owner_relation_map, viewer_relation


def validate_customer_trade_limits(
    relation: CustomerRelation | object,
    *,
    quantity: int | str,
    trades_today: int = 0,
    commodity_volume_today: int = 0,
    now=None,
) -> None:
    if relation is None:
        raise HTTPException(status_code=400, detail="Customer relation is required")

    relation_status = getattr(getattr(relation, "status", None), "value", getattr(relation, "status", None))
    if relation_status != CustomerRelationStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Customer relation is not active")

    normalized_quantity = _normalize_non_negative_int(quantity, name="quantity")
    if normalized_quantity <= 0:
        raise HTTPException(status_code=400, detail="Trade quantity must be positive")

    current_time = now or _utcnow_naive()
    restricted_until = getattr(relation, "trading_restricted_until", None)
    if restricted_until is not None and restricted_until > current_time:
        raise HTTPException(status_code=400, detail="Customer is temporarily restricted from trading")

    min_trade_quantity = getattr(relation, "min_trade_quantity", None)
    if min_trade_quantity is not None and normalized_quantity < int(min_trade_quantity):
        raise HTTPException(status_code=400, detail="Trade quantity is below the customer's minimum limit")

    max_trade_quantity = getattr(relation, "max_trade_quantity", None)
    if max_trade_quantity is not None and normalized_quantity > int(max_trade_quantity):
        raise HTTPException(status_code=400, detail="Trade quantity exceeds the customer's maximum limit")

    normalized_trades_today = _normalize_non_negative_int(trades_today, name="trades_today")
    max_daily_trades = getattr(relation, "max_daily_trades", None)
    if max_daily_trades is not None and normalized_trades_today >= int(max_daily_trades):
        raise HTTPException(status_code=400, detail="Customer has reached the daily trade limit")

    normalized_commodity_volume_today = _normalize_non_negative_int(commodity_volume_today, name="commodity_volume_today")
    max_daily_commodity_volume = getattr(relation, "max_daily_commodity_volume", None)
    if (
        max_daily_commodity_volume is not None
        and normalized_commodity_volume_today + normalized_quantity > int(max_daily_commodity_volume)
    ):
        raise HTTPException(status_code=400, detail="Customer has reached the daily commodity volume limit")


async def build_allowed_customer_chat_targets(
    db: AsyncSession,
    customer_user_id: int,
) -> list[int]:
    relation = await get_active_customer_relation_for_customer(db, customer_user_id)
    if relation is None:
        return []

    owner_user_id = relation.owner_user_id
    stmt = (
        select(User.id)
        .outerjoin(
            CustomerRelation,
            and_(
                CustomerRelation.customer_user_id == User.id,
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                CustomerRelation.deleted_at.is_(None),
            ),
        )
        .where(
            User.is_deleted.is_(False),
            User.id != customer_user_id,
            (
                (User.id == owner_user_id)
                | (
                    (User.role == UserRole.SUPER_ADMIN)
                    & CustomerRelation.id.is_(None)
                )
            ),
        )
        .order_by(User.id.asc())
    )
    allowed_ids = list((await db.execute(stmt)).scalars().all())

    accountant_stmt = (
        select(AccountantRelation.accountant_user_id)
        .join(User, User.id == AccountantRelation.accountant_user_id)
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.accountant_user_id.is_not(None),
            User.is_deleted.is_(False),
        )
        .order_by(AccountantRelation.accountant_user_id.asc())
    )
    accountant_ids = list((await db.execute(accountant_stmt)).scalars().all())

    return sorted({*allowed_ids, *accountant_ids})