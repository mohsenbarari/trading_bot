"""Private admin monitoring-channel projection for market offers."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import telegram_gateway
from core.config import settings
from core.offer_identity import ensure_offer_public_id
from core.offer_settlement import build_offer_summary_text
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.offer_publication_state_service import (
    apply_publication_state_update,
    build_offer_publication_state,
    normalize_publication_status,
    publication_dedupe_key,
)
from core.utils import utc_now_naive
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)


MONITORING_SURFACE = OfferPublicationSurface.TELEGRAM_MONITORING_CHANNEL
SENT_MONITORING_STATUSES = {
    OfferPublicationStatus.SENT,
    OfferPublicationStatus.VISIBLE,
    OfferPublicationStatus.DISABLED,
}


@dataclass(frozen=True, slots=True)
class MonitoringCustomerOwner:
    user_id: int | None
    display_name: str
    telegram_username: str
    mobile_number: str


@dataclass(frozen=True, slots=True)
class MonitoringOfferPresenter:
    user_id: int | None
    account_name: str
    telegram_username: str
    mobile_number: str
    role: str
    customer_owner: MonitoringCustomerOwner | None = None


@dataclass(frozen=True, slots=True)
class MonitoringChannelApplyResult:
    ok: bool
    response_class: str
    status_code: int | None = None
    reason: str = "unknown"
    retry_after_seconds: int | None = None
    error: str | None = None


def monitoring_enqueue_enabled() -> bool:
    return bool(getattr(settings, "telegram_monitoring_channel_enabled", False))


def monitoring_delivery_enabled() -> bool:
    return bool(
        monitoring_enqueue_enabled()
        and getattr(settings, "telegram_monitoring_bot_token", None)
        and getattr(settings, "telegram_monitoring_channel_id", None)
    )


def monitoring_enabled() -> bool:
    """Backward-compatible name for the foreign delivery guard."""
    return monitoring_delivery_enabled()


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    numeric_value = _coerce_int(value)
    if numeric_value is None or numeric_value <= 0:
        return None
    return numeric_value


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _username(value: Any) -> str:
    normalized = str(value or "").strip().lstrip("@")
    return f"@{normalized}" if normalized else ""


def normalize_mobile_number(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits


def _display_name(value: Any) -> str:
    return str(value or "").strip()


def _status_label(value: Any) -> str:
    normalized = _value(value).lower()
    if normalized == OfferStatus.ACTIVE.value:
        return "فعال"
    if normalized == OfferStatus.COMPLETED.value:
        return "معامله‌شده"
    if normalized == OfferStatus.CANCELLED.value:
        return "لغوشده"
    if normalized == OfferStatus.EXPIRED.value:
        return "منقضی"
    return normalized or "نامشخص"


def _gateway_status_code(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, Mapping):
        status_code = _positive_int(result.response_json.get("error_code"))
    return status_code


def _retry_after_from_result(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    raw_retry_after = None
    if isinstance(result.response_json, Mapping):
        parameters = result.response_json.get("parameters")
        if isinstance(parameters, Mapping):
            raw_retry_after = parameters.get("retry_after")
    retry_after = _positive_int(raw_retry_after)
    if retry_after is None:
        return None
    return min(120, max(1, retry_after))


def _classify_gateway_result(result: telegram_gateway.TelegramGatewayResult) -> MonitoringChannelApplyResult:
    status_code = _gateway_status_code(result)
    if result.ok:
        return MonitoringChannelApplyResult(ok=True, response_class="2xx", status_code=status_code, reason="ok")
    if status_code == 429:
        return MonitoringChannelApplyResult(
            ok=False,
            response_class="429",
            status_code=status_code,
            reason="telegram_rate_limited",
            retry_after_seconds=_retry_after_from_result(result),
            error=result.error,
        )
    if status_code == 400:
        return MonitoringChannelApplyResult(
            ok=False,
            response_class="400",
            status_code=status_code,
            reason="telegram_bad_request",
            error=result.error,
        )
    if status_code is not None and 400 <= status_code <= 499:
        return MonitoringChannelApplyResult(
            ok=False,
            response_class="4xx",
            status_code=status_code,
            reason="telegram_client_error",
            error=result.error,
        )
    if status_code is not None and 500 <= status_code <= 599:
        return MonitoringChannelApplyResult(
            ok=False,
            response_class="5xx",
            status_code=status_code,
            reason="telegram_server_error",
            error=result.error,
        )
    if result.error:
        return MonitoringChannelApplyResult(
            ok=False,
            response_class="transport",
            status_code=status_code,
            reason="telegram_transport_error",
            error=result.error,
        )
    return MonitoringChannelApplyResult(ok=False, response_class="unknown", status_code=status_code)


def build_monitoring_offer_presenter(
    user: Any,
    *,
    customer_owner: Any | None = None,
) -> MonitoringOfferPresenter:
    owner = None
    if customer_owner is not None:
        owner = MonitoringCustomerOwner(
            user_id=_coerce_int(getattr(customer_owner, "id", None)),
            display_name=(
                _display_name(getattr(customer_owner, "full_name", None))
                or _display_name(getattr(customer_owner, "account_name", None))
            ),
            telegram_username=_username(getattr(customer_owner, "username", None)),
            mobile_number=normalize_mobile_number(getattr(customer_owner, "mobile_number", None)),
        )
    return MonitoringOfferPresenter(
        user_id=_coerce_int(getattr(user, "id", None)),
        account_name=_display_name(getattr(user, "account_name", None)),
        telegram_username=_username(getattr(user, "username", None)),
        mobile_number=normalize_mobile_number(getattr(user, "mobile_number", None)),
        role=_value(getattr(user, "role", None)),
        customer_owner=owner,
    )


def build_monitoring_offer_message(offer: Any, presenter: MonitoringOfferPresenter) -> str:
    commodity = getattr(offer, "commodity", None)
    offer_summary = build_offer_summary_text(
        offer_type=getattr(offer, "offer_type", None),
        settlement_type=getattr(offer, "settlement_type", None),
        commodity_name=getattr(commodity, "name", None) or "نامشخص",
        quantity=_coerce_int(getattr(offer, "quantity", None)) or 0,
        price=_coerce_int(getattr(offer, "price", None)) or 0,
    )
    lines = [
        "رصد بازار",
        offer_summary,
        "",
        f"وضعیت: {_status_label(getattr(offer, 'status', None))}",
        f"ارسال شده از: {getattr(offer, 'home_server', '') or '-'}",
        f"نام کاربری آفر‌دهنده: {presenter.account_name or '-'}",
        f"یوزرنیم تلگرام: {presenter.telegram_username}",
        f"موبایل: {presenter.mobile_number or '-'}",
        f"نقش: {presenter.role or '-'}",
    ]

    if presenter.customer_owner is not None:
        owner = presenter.customer_owner
        lines.extend(
            [
                "",
                "مالک مشتری:",
                f"نام سرگروه: {owner.display_name or '-'}",
                f"کاربر: #{owner.user_id or '-'}",
                f"یوزرنیم تلگرام: {owner.telegram_username}",
                f"موبایل: {owner.mobile_number or '-'}",
            ]
        )

    notes = str(getattr(offer, "notes", None) or "").strip()
    if notes:
        lines.extend(["", f"توضیحات: {notes}"])
    return "\n".join(lines)


async def load_customer_owner_for_offer(db: AsyncSession, offer: Offer) -> Any | None:
    user_id = _coerce_int(getattr(offer, "user_id", None))
    if not user_id:
        return None
    result = await db.execute(
        select(CustomerRelation)
        .options(selectinload(CustomerRelation.owner_user))
        .where(
            and_(
                CustomerRelation.customer_user_id == user_id,
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                CustomerRelation.deleted_at.is_(None),
            )
        )
        .limit(1)
    )
    relation = result.scalar_one_or_none()
    return getattr(relation, "owner_user", None) if relation is not None else None


async def load_monitoring_publication_state_for_update(
    db: AsyncSession,
    offer: Any,
) -> OfferPublicationState | None:
    offer_public_id = ensure_offer_public_id(offer)
    dedupe_key = publication_dedupe_key(offer_public_id, MONITORING_SURFACE)
    result = await db.execute(
        select(OfferPublicationState)
        .where(OfferPublicationState.dedupe_key == dedupe_key)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_or_create_monitoring_publication_state(
    db: AsyncSession,
    offer: Any,
) -> OfferPublicationState:
    state = await load_monitoring_publication_state_for_update(db, offer)
    if state is not None:
        return state

    state = build_offer_publication_state(
        offer,
        MONITORING_SURFACE,
        status=OfferPublicationStatus.PENDING,
    )
    try:
        async with db.begin_nested():
            db.add(state)
            await db.flush()
            return state
    except IntegrityError:
        state = await load_monitoring_publication_state_for_update(db, offer)
        if state is not None:
            return state
        raise


async def enqueue_offer_monitoring_publication(
    db: AsyncSession,
    offer: Any,
) -> OfferPublicationState | None:
    """Create the monitoring state for a newly accepted offer without Telegram I/O."""
    if not monitoring_enqueue_enabled():
        return None
    state = await get_or_create_monitoring_publication_state(db, offer)
    if normalize_publication_status(state.status) in SENT_MONITORING_STATUSES:
        return state
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=OfferPublicationStatus.PENDING,
        now=utc_now_naive(),
    )
    return state


async def apply_monitoring_channel_state_with_result(
    db: AsyncSession,
    offer: Offer,
    *,
    publication_state: OfferPublicationState,
    timeout: float = 10,
) -> MonitoringChannelApplyResult:
    if current_server() != SERVER_FOREIGN:
        return MonitoringChannelApplyResult(ok=False, response_class="skipped", reason="non_foreign_server")
    if not monitoring_delivery_enabled():
        return MonitoringChannelApplyResult(ok=False, response_class="skipped", reason="monitoring_disabled")

    customer_owner = await load_customer_owner_for_offer(db, offer)
    presenter = build_monitoring_offer_presenter(getattr(offer, "user", None), customer_owner=customer_owner)
    text = build_monitoring_offer_message(offer, presenter)
    channel_id = _coerce_int(settings.telegram_monitoring_channel_id)
    message_id = _coerce_int(getattr(publication_state, "telegram_message_id", None))
    bot_token = settings.telegram_monitoring_bot_token

    if not channel_id or not bot_token:
        return MonitoringChannelApplyResult(ok=False, response_class="skipped", reason="missing_monitoring_config")

    if message_id:
        result = await telegram_gateway.edit_message_text(
            channel_id,
            message_id,
            text,
            timeout=timeout,
            bot_token=bot_token,
            idempotency_key=(
                f"offer-monitoring-edit:{getattr(offer, 'offer_public_id', '')}:"
                f"{getattr(offer, 'version_id', '')}"
            ),
        )
    else:
        result = await telegram_gateway.send_message(
            channel_id,
            text,
            timeout=timeout,
            bot_token=bot_token,
            idempotency_key=f"offer-monitoring-send:{getattr(offer, 'offer_public_id', '')}",
        )
    classified = _classify_gateway_result(result)
    if classified.ok:
        apply_publication_state_update(
            publication_state,
            offer_status=getattr(offer, "status", None),
            offer_version_id=getattr(offer, "version_id", None),
            requested_status=(
                OfferPublicationStatus.DISABLED
                if _value(getattr(offer, "status", None)).lower() in {
                    OfferStatus.COMPLETED.value,
                    OfferStatus.CANCELLED.value,
                    OfferStatus.EXPIRED.value,
                }
                else OfferPublicationStatus.SENT
            ),
            now=utc_now_naive(),
            surface_resource_id=str(message_id or result.message_id or ""),
            telegram_chat_id=channel_id,
            telegram_message_id=message_id or result.message_id,
        )
        return classified

    apply_publication_state_update(
        publication_state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=OfferPublicationStatus.FAILED,
        now=utc_now_naive(),
        error_code=classified.reason,
        error_message=classified.error,
    )
    return classified
