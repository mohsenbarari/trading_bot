"""Shared command surface for authoritative offer creation."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import SettlementType
from core.offer_identity import generate_offer_public_id
from core.offer_settlement import normalize_settlement_type
from core.offer_source import OfferSourceSurface, normalize_offer_source_surface, offer_home_server_for_source
from core.services.market_transition_service import acquire_market_offer_admission_fence
from models.offer import Offer, OfferStatus, OfferType
from models.user import User


class OfferCreationValidationError(ValueError):
    """Raised when a shared offer creation command violates market rules."""


class OfferCreationAdmissionError(RuntimeError):
    """Base error for final local offer-quota admission failures."""

    def __init__(self, reason: str, detail: str):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class OfferCreationLimitExceededError(OfferCreationAdmissionError):
    """Raised when a final user or active-offer limit rejects creation."""


class OfferCreationQuotaUnavailableError(OfferCreationAdmissionError):
    """Raised when the local per-user quota lock cannot be acquired safely."""


class OfferCreationIdempotencyConflictError(OfferCreationAdmissionError):
    """Raised when one idempotency key is reused for a different intent."""


OFFER_CREATION_FINGERPRINT_VERSION = 1
OFFER_CREATION_IDEMPOTENCY_CONFLICT_DETAIL = (
    "شناسه یکتای این درخواست قبلاً با مشخصات متفاوت استفاده شده است. "
    "لطفاً فهرست بازار را تازه‌سازی و درخواست جدیدی ثبت کنید."
)


@dataclass(frozen=True)
class OfferCreationCommand:
    source_surface: OfferSourceSurface | str
    owner_user_id: int | None
    actor_user_id: int | None
    offer_type: OfferType | str
    commodity_id: int
    quantity: int
    price: int
    settlement_type: SettlementType | str = SettlementType.CASH
    is_wholesale: bool = True
    lot_sizes: Sequence[int] | None = None
    original_lot_sizes: Sequence[int] | None = None
    notes: str | None = None
    idempotency_key: str | None = None
    status: OfferStatus | str = OfferStatus.ACTIVE
    remaining_quantity: int | None = None
    exclude_from_competitive_price: bool = False
    price_warning_type: str | None = None
    incoming_home_server: str | None = None
    offer_public_id: str | None = None
    republished_from_offer_public_id: str | None = None
    idempotency_fingerprint_version: int | None = None
    idempotency_fingerprint: str | None = None


@dataclass(frozen=True)
class OfferCreationQuotaPolicy:
    """Local quota policy applied in the authoritative creation transaction."""

    max_active_offers: int
    enforce_user_limits: bool = True


@dataclass(frozen=True)
class OfferCreationOutcome:
    offer: Offer
    created: bool
    active_offer_count: int | None = None


def _normalize_offer_type(value: OfferType | str) -> OfferType:
    if isinstance(value, OfferType):
        return value
    return OfferType(str(value).strip().lower())


def _normalize_offer_status(value: OfferStatus | str) -> OfferStatus:
    if isinstance(value, OfferStatus):
        return value
    return OfferStatus(str(value).strip().lower())


def _list_or_none(value: Sequence[int] | None) -> list[int] | None:
    return list(value) if value is not None else None


def _canonical_lot_sizes(value: Sequence[int] | None) -> list[int]:
    if not value:
        return []
    return sorted(int(item) for item in value)


def _canonical_notes(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def canonical_offer_creation_payload(command: OfferCreationCommand) -> dict[str, object]:
    """Return the versioned economic intent used by offer idempotency."""
    source_surface = normalize_offer_source_surface(command.source_surface)
    home_server = offer_home_server_for_source(
        source_surface,
        incoming_home_server=command.incoming_home_server,
    )
    return {
        "version": OFFER_CREATION_FINGERPRINT_VERSION,
        "source_surface": source_surface.value,
        "home_server": home_server,
        "owner_user_id": (
            int(command.owner_user_id) if command.owner_user_id is not None else None
        ),
        "actor_user_id": (
            int(command.actor_user_id) if command.actor_user_id is not None else None
        ),
        "offer_type": _normalize_offer_type(command.offer_type).value,
        "settlement_type": normalize_settlement_type(command.settlement_type).value,
        "commodity_id": int(command.commodity_id),
        "quantity": int(command.quantity),
        "price": int(command.price),
        "is_wholesale": bool(command.is_wholesale),
        "lot_sizes": _canonical_lot_sizes(command.lot_sizes),
        "notes": _canonical_notes(command.notes),
        "republished_from_offer_public_id": (
            str(command.republished_from_offer_public_id).strip() or None
            if command.republished_from_offer_public_id is not None
            else None
        ),
    }


def offer_creation_fingerprint(command: OfferCreationCommand) -> str:
    canonical = canonical_offer_creation_payload(command)
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_offer_creation_command(offer: Offer | object) -> OfferCreationCommand:
    home_server = str(getattr(offer, "home_server", "") or "").strip().lower()
    if home_server == "iran":
        source_surface = OfferSourceSurface.WEBAPP
    elif home_server == "foreign":
        source_surface = OfferSourceSurface.TELEGRAM_BOT
    else:
        raise ValueError("legacy offer has no reconstructable source surface")

    original_lot_sizes = getattr(offer, "original_lot_sizes", None)
    if original_lot_sizes is None:
        original_lot_sizes = getattr(offer, "lot_sizes", None)
    return OfferCreationCommand(
        source_surface=source_surface,
        owner_user_id=getattr(offer, "user_id", None),
        actor_user_id=getattr(offer, "actor_user_id", None),
        offer_type=getattr(offer, "offer_type"),
        settlement_type=getattr(offer, "settlement_type", SettlementType.CASH),
        commodity_id=getattr(offer, "commodity_id"),
        quantity=getattr(offer, "quantity"),
        price=getattr(offer, "price"),
        is_wholesale=bool(getattr(offer, "is_wholesale", True)),
        lot_sizes=original_lot_sizes,
        notes=getattr(offer, "notes", None),
        republished_from_offer_public_id=getattr(
            offer,
            "republished_from_offer_public_id",
            None,
        ),
    )


def ensure_offer_idempotency_replay_matches(
    offer: Offer | object,
    command: OfferCreationCommand,
) -> None:
    """Fail closed unless an existing offer represents exactly this intent."""
    expected = offer_creation_fingerprint(command)
    stored_version = getattr(offer, "idempotency_fingerprint_version", None)
    stored_fingerprint = getattr(offer, "idempotency_fingerprint", None)

    if stored_version is None and stored_fingerprint is None:
        try:
            stored_fingerprint = offer_creation_fingerprint(
                _legacy_offer_creation_command(offer)
            )
        except (AttributeError, TypeError, ValueError):
            stored_fingerprint = None
    elif (
        stored_version != OFFER_CREATION_FINGERPRINT_VERSION
        or not isinstance(stored_fingerprint, str)
        or len(stored_fingerprint) != 64
    ):
        stored_fingerprint = None

    if not stored_fingerprint or not hmac.compare_digest(stored_fingerprint, expected):
        raise OfferCreationIdempotencyConflictError(
            "offer_idempotency_payload_mismatch",
            OFFER_CREATION_IDEMPOTENCY_CONFLICT_DETAIL,
        )


async def validate_offer_creation_command(db: AsyncSession, command: OfferCreationCommand) -> None:
    source_surface = normalize_offer_source_surface(command.source_surface)
    if source_surface == OfferSourceSurface.INTERNAL_SYNC:
        return

    from core.services import trade_service

    is_valid_qty, err_qty = trade_service.validate_quantity(command.quantity)
    if not is_valid_qty:
        raise OfferCreationValidationError(err_qty)

    is_valid_price, err_price = trade_service.validate_price(command.price)
    if not is_valid_price:
        raise OfferCreationValidationError(err_price)

    if not command.is_wholesale:
        if not command.lot_sizes:
            raise OfferCreationValidationError("برای آفر خُرد باید لات‌ها مشخص شوند.")
        is_valid_lots, err_lots, _suggested_lots = trade_service.validate_lot_sizes(command.quantity, command.lot_sizes)
        if not is_valid_lots:
            raise OfferCreationValidationError(err_lots)

    is_valid_comp, err_comp = await trade_service.validate_competitive_price(
        db=db,
        offer_type=_normalize_offer_type(command.offer_type).value,
        settlement_type=normalize_settlement_type(command.settlement_type),
        commodity_id=command.commodity_id,
        quantity=command.quantity,
        proposed_price=command.price,
        user_id=command.owner_user_id,
    )
    if not is_valid_comp:
        raise OfferCreationValidationError(err_comp)


def build_authoritative_offer(command: OfferCreationCommand) -> Offer:
    source_surface = normalize_offer_source_surface(command.source_surface)
    home_server = offer_home_server_for_source(
        source_surface,
        incoming_home_server=command.incoming_home_server,
    )
    lot_sizes = _list_or_none(command.lot_sizes)
    original_lot_sizes = _list_or_none(command.original_lot_sizes)
    if original_lot_sizes is None:
        original_lot_sizes = _list_or_none(command.lot_sizes)

    offer_public_id = (command.offer_public_id or "").strip() or generate_offer_public_id()
    if source_surface == OfferSourceSurface.INTERNAL_SYNC and not command.offer_public_id:
        raise ValueError("internal_sync offers require incoming offer_public_id")

    normalized_idempotency_key = (command.idempotency_key or "").strip() or None
    idempotency_fingerprint_version = command.idempotency_fingerprint_version
    idempotency_fingerprint = command.idempotency_fingerprint
    if normalized_idempotency_key and source_surface != OfferSourceSurface.INTERNAL_SYNC:
        idempotency_fingerprint_version = OFFER_CREATION_FINGERPRINT_VERSION
        idempotency_fingerprint = offer_creation_fingerprint(command)

    return Offer(
        offer_public_id=offer_public_id,
        user_id=command.owner_user_id,
        actor_user_id=command.actor_user_id,
        home_server=home_server,
        offer_type=_normalize_offer_type(command.offer_type),
        settlement_type=normalize_settlement_type(command.settlement_type),
        commodity_id=command.commodity_id,
        quantity=command.quantity,
        remaining_quantity=command.remaining_quantity if command.remaining_quantity is not None else command.quantity,
        price=command.price,
        exclude_from_competitive_price=bool(command.exclude_from_competitive_price),
        price_warning_type=command.price_warning_type,
        is_wholesale=bool(command.is_wholesale),
        lot_sizes=lot_sizes,
        original_lot_sizes=original_lot_sizes,
        notes=command.notes,
        idempotency_key=normalized_idempotency_key,
        idempotency_fingerprint_version=idempotency_fingerprint_version,
        idempotency_fingerprint=idempotency_fingerprint,
        status=_normalize_offer_status(command.status),
        republished_from_offer_public_id=(
            str(command.republished_from_offer_public_id).strip()
            if command.republished_from_offer_public_id
            else None
        ),
    )


async def _lock_offer_owner(db: AsyncSession, owner_user_id: int) -> User:
    try:
        result = await db.execute(
            select(User).where(User.id == owner_user_id).with_for_update()
        )
    except DBAPIError as exc:
        raise OfferCreationQuotaUnavailableError(
            "offer_quota_lock_unavailable",
            "درخواست قبلی شما هنوز در حال پردازش است. لطفاً چند لحظه دیگر دوباره تلاش کنید.",
        ) from exc

    owner = result.scalar_one_or_none()
    if owner is None:
        raise OfferCreationAdmissionError(
            "offer_owner_not_found",
            "کاربر ثبت‌کننده آفر یافت نشد.",
        )
    return owner


async def _load_local_idempotency_replay(
    db: AsyncSession,
    *,
    command: OfferCreationCommand,
) -> Offer | None:
    normalized_key = (command.idempotency_key or "").strip()
    if not normalized_key:
        return None
    result = await db.execute(
        select(Offer).where(Offer.idempotency_key == normalized_key)
    )
    replay = result.scalar_one_or_none()
    if replay is not None:
        ensure_offer_idempotency_replay_matches(replay, command)
    return replay


async def _admit_local_offer_quota(
    db: AsyncSession,
    command: OfferCreationCommand,
    policy: OfferCreationQuotaPolicy,
) -> tuple[User, Offer | None, int]:
    if command.owner_user_id is None:
        raise OfferCreationAdmissionError(
            "offer_owner_required",
            "کاربر ثبت‌کننده آفر مشخص نیست.",
        )
    if int(policy.max_active_offers) < 1:
        raise ValueError("max_active_offers must be positive")

    owner = await _lock_offer_owner(db, int(command.owner_user_id))
    replay = await _load_local_idempotency_replay(
        db,
        command=command,
    )
    if replay is not None:
        return owner, replay, -1

    if policy.enforce_user_limits:
        from core.utils import check_user_limits

        for action_type, quantity in (
            ("channel_message", 1),
            ("trade", command.quantity),
        ):
            allowed, error_message = check_user_limits(owner, action_type, quantity)
            if not allowed:
                raise OfferCreationLimitExceededError(
                    f"offer_user_limit_exceeded:{action_type}",
                    error_message or "محدودیت ثبت آفر برای حساب شما فعال است.",
                )

    active_offer_count = int(
        await db.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == owner.id,
                Offer.status == OfferStatus.ACTIVE,
            )
        )
        or 0
    )
    if active_offer_count >= int(policy.max_active_offers):
        raise OfferCreationLimitExceededError(
            "offer_active_limit_exceeded",
            (
                f"شما حداکثر {policy.max_active_offers} لفظ فعال دارید. "
                "لطفاً ابتدا یکی را منقضی کنید."
            ),
        )
    return owner, None, active_offer_count


async def create_authoritative_offer_with_outcome(
    db: AsyncSession,
    command: OfferCreationCommand,
    *,
    commit: bool = True,
    refresh: bool = True,
    validate_market: bool = True,
    enforce_market_admission: bool = False,
    quota_policy: OfferCreationQuotaPolicy | None = None,
) -> OfferCreationOutcome:
    if validate_market:
        await validate_offer_creation_command(db, command)
    if enforce_market_admission:
        await acquire_market_offer_admission_fence(db)

    locked_owner: User | None = None
    active_offer_count: int | None = None
    if quota_policy is not None:
        locked_owner, replay, active_offer_count = await _admit_local_offer_quota(
            db,
            command,
            quota_policy,
        )
        if replay is not None:
            if commit:
                await db.commit()
            return OfferCreationOutcome(
                offer=replay,
                created=False,
                active_offer_count=None,
            )

    offer = build_authoritative_offer(command)
    db.add(offer)
    if locked_owner is not None:
        from core.user_counter_sync import increment_user_counters

        increment_user_counters(locked_owner, channel_messages=1)
    if commit:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            replay = await _load_local_idempotency_replay(db, command=command)
            if replay is not None:
                return OfferCreationOutcome(
                    offer=replay,
                    created=False,
                    active_offer_count=None,
                )
            raise
        if refresh:
            await db.refresh(offer)
    return OfferCreationOutcome(
        offer=offer,
        created=True,
        active_offer_count=(active_offer_count + 1 if active_offer_count is not None else None),
    )


async def create_authoritative_offer(
    db: AsyncSession,
    command: OfferCreationCommand,
    *,
    commit: bool = True,
    refresh: bool = True,
    validate_market: bool = True,
    enforce_market_admission: bool = False,
    quota_policy: OfferCreationQuotaPolicy | None = None,
) -> Offer:
    outcome = await create_authoritative_offer_with_outcome(
        db,
        command,
        commit=commit,
        refresh=refresh,
        validate_market=validate_market,
        enforce_market_admission=enforce_market_admission,
        quota_policy=quota_policy,
    )
    return outcome.offer
