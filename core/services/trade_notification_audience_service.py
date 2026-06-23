"""Read-only trade-completion notification audience builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.accountant_chat_contract import AccountantChatIdentity, load_accountant_chat_identity_map
from core.services.accountant_relation_service import build_trade_notification_audience_user_ids
from core.services.bot_access_policy import evaluate_bot_access
from core.utils import to_jalali_str, unique_user_ids
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.trade import Trade, TradeStatus, TradeType
from models.user import User


TRADE_COMPLETED_EVENT_TYPE = "trade_completed"
WEBAPP_CHANNEL = "webapp"
TELEGRAM_CHANNEL = "telegram"
WEBAPP_DESTINATION_SERVER = "iran"
TELEGRAM_DESTINATION_SERVER = "foreign"


@dataclass(frozen=True)
class TradeNotificationChannelRequirement:
    channel: str
    destination_server: str
    required: bool
    reason: str
    telegram_id: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class TradeNotificationAudienceRecipient:
    recipient_user_id: int
    recipient_role: str
    principal_user_id: int
    side: str
    counterparty_user_id: int | None
    webapp_message: str
    extra_payload: dict[str, object | None]
    channel_requirements: tuple[TradeNotificationChannelRequirement, ...]


@dataclass(frozen=True)
class TradeNotificationAudience:
    event_type: str
    trade_id: int | None
    trade_number: int | None
    offer_id: int | None
    offer_home_server: str | None
    trade_path_kind: str | None
    trade_path_summary: str | None
    recipients: tuple[TradeNotificationAudienceRecipient, ...]
    skipped_reason: str | None = None


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _coerce_user_id(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _normalize_customer_tier_value(value: object) -> str | None:
    normalized = _enum_value(value)
    if normalized in {CustomerTier.TIER_1.value, CustomerTier.TIER_2.value}:
        return normalized
    return None


def _normalize_offer_notes_for_notification(offer_notes: str | None) -> str | None:
    normalized = " ".join(str(offer_notes or "").split())
    return normalized or None


def _trade_is_completed(trade: Trade | object) -> bool:
    return _enum_value(getattr(trade, "status", None)) == TradeStatus.COMPLETED.value


def _trade_type_value(trade: Trade | object) -> str:
    return _enum_value(getattr(trade, "trade_type", None))


def _trade_labels(trade: Trade | object) -> tuple[str, str, str, str]:
    if _trade_type_value(trade) == TradeType.BUY.value:
        return "🟢", "خرید", "🔴", "فروش"
    return "🔴", "فروش", "🟢", "خرید"


def _user_display_name(user: object | None) -> str:
    return getattr(user, "account_name", None) or getattr(user, "full_name", None) or "نامشخص"


def _commodity_name(trade: Trade | object) -> str:
    commodity = getattr(trade, "commodity", None)
    return getattr(commodity, "name", None) or getattr(trade, "commodity_name", None) or "نامشخص"


def _offer_notes(trade: Trade | object) -> str | None:
    offer = getattr(trade, "offer", None)
    return getattr(offer, "notes", None) or getattr(trade, "offer_notes", None)


def _offer_home_server(trade: Trade | object) -> str | None:
    offer = getattr(trade, "offer", None)
    return getattr(offer, "home_server", None) or getattr(trade, "offer_home_server", None)


async def _load_users_by_ids(db: AsyncSession, user_ids: Sequence[object]) -> dict[int, User]:
    normalized_user_ids = unique_user_ids(user_ids)
    if not normalized_user_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(normalized_user_ids)))
    return {
        user.id: user
        for user in result.scalars().all()
        if _coerce_user_id(getattr(user, "id", None)) is not None
    }


async def _load_trade_customer_relation_map_for_user_ids(
    db: AsyncSession,
    user_ids: Sequence[object],
) -> dict[int, CustomerRelation]:
    participant_ids = unique_user_ids(user_ids)
    if not participant_ids:
        return {}
    result = await db.execute(
        select(CustomerRelation).where(
            CustomerRelation.customer_user_id.in_(participant_ids),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    return {
        relation.customer_user_id: relation
        for relation in result.scalars().all()
        if _coerce_user_id(getattr(relation, "customer_user_id", None)) is not None
    }


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

    normalized_offer_user_id = _coerce_user_id(offer_user_id)
    normalized_responder_user_id = _coerce_user_id(responder_user_id)
    if normalized_offer_user_id is None or normalized_responder_user_id is None:
        return payload

    relation = customer_relation_map.get(normalized_offer_user_id)
    if relation is None or _coerce_user_id(getattr(relation, "owner_user_id", None)) != normalized_responder_user_id:
        relation = customer_relation_map.get(normalized_responder_user_id)
        if relation is None or _coerce_user_id(getattr(relation, "owner_user_id", None)) != normalized_offer_user_id:
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
    normalized_user_id = _coerce_user_id(user_id)
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
        _coerce_user_id(getattr(identity, "profile_user_id", None))
        or normalized_user_id
    )
    payload[f"{field_prefix}_profile_account_name"] = (
        getattr(identity, "profile_account_name", None)
        or fallback_name
    )
    payload[f"{field_prefix}_resolved_from_accountant_id"] = _coerce_user_id(
        getattr(identity, "resolved_from_accountant_id", None)
    )
    payload[f"{field_prefix}_highlight_accountant_user_id"] = _coerce_user_id(
        getattr(identity, "highlight_accountant_user_id", None)
    )
    payload[f"{field_prefix}_highlight_accountant_relation_display_name"] = getattr(
        identity,
        "highlight_accountant_relation_display_name",
        None,
    )
    return payload


def _build_trade_profile_route_from_payload(
    field_prefix: str,
    participant_payload: Mapping[str, object | None],
) -> str | None:
    profile_user_id = _coerce_user_id(participant_payload.get(f"{field_prefix}_profile_user_id"))
    if profile_user_id is None:
        return None
    query_params: dict[str, object] = {}
    profile_account_name = participant_payload.get(f"{field_prefix}_profile_account_name")
    if isinstance(profile_account_name, str) and profile_account_name.strip():
        query_params["account_name"] = profile_account_name
    highlight_accountant_user_id = _coerce_user_id(
        participant_payload.get(f"{field_prefix}_highlight_accountant_user_id")
    )
    if highlight_accountant_user_id is not None:
        query_params["highlight_accountant_user_id"] = highlight_accountant_user_id
    highlight_relation_display_name = participant_payload.get(
        f"{field_prefix}_highlight_accountant_relation_display_name"
    )
    if isinstance(highlight_relation_display_name, str) and highlight_relation_display_name.strip():
        query_params["highlight_accountant_relation_display_name"] = highlight_relation_display_name

    query_string = urlencode(query_params)
    if query_string:
        return f"/users/{profile_user_id}?{query_string}"
    return f"/users/{profile_user_id}"


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
    return _coerce_user_id(getattr(relation, "owner_user_id", None))


def _should_hide_counterparty_for_recipient(
    *,
    audience_user_id: int | None,
    counterparty_user_id: int | None,
    customer_relation_map: Mapping[int, CustomerRelation | object] | None,
) -> bool:
    owner_user_id = _recipient_customer_owner_user_id(audience_user_id, customer_relation_map)
    if owner_user_id is None:
        return False
    normalized_counterparty_user_id = _coerce_user_id(counterparty_user_id)
    if normalized_counterparty_user_id is None:
        return True
    return normalized_counterparty_user_id != owner_user_id


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
) -> str:
    lines = [
        f"{trade_emoji} {trade_type_label}",
        f"💰 فی: {trade_price:,}",
        f"📦 تعداد: {trade_quantity}",
        f"🏷️ کالا: {commodity_name}",
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


def _build_trade_telegram_message(
    *,
    trade_emoji: str,
    trade_type_label: str,
    trade_price: int,
    trade_quantity: int,
    commodity_name: str,
    trade_number: int,
    trade_datetime: str,
    counterparty_name: str | None,
    hide_counterparty: bool = False,
    trade_path_summary: str | None = None,
    offer_notes: str | None = None,
) -> str:
    lines = [
        f"{trade_emoji} <b>{trade_type_label}</b>",
        "",
        f"💰 فی: {trade_price:,}",
        f"📦 تعداد: {trade_quantity}",
        f"🏷️ کالا: {commodity_name}",
    ]
    if counterparty_name and not hide_counterparty:
        lines.append(f"👤 طرف معامله: {counterparty_name}")
    lines.append(f"🔢 شماره معامله: {trade_number}")
    lines.append(f"🕐 زمان معامله: {trade_datetime}")
    if trade_path_summary:
        lines.append(f"🧭 مسیر: {trade_path_summary}")
    normalized_offer_notes = _normalize_offer_notes_for_notification(offer_notes)
    if normalized_offer_notes:
        lines.append(f"📝 توضیحات: {normalized_offer_notes}")
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
) -> tuple[str, str]:
    responder_msg = _build_trade_telegram_message(
        trade_emoji=responder_trade_emoji,
        trade_type_label=responder_trade_label,
        trade_price=trade_price,
        trade_quantity=trade_quantity,
        commodity_name=commodity_name,
        trade_number=trade_number,
        trade_datetime=trade_datetime,
        counterparty_name=offer_user_name,
        trade_path_summary=trade_path_summary,
        offer_notes=offer_notes,
    )
    offer_owner_msg = _build_trade_telegram_message(
        trade_emoji=offer_trade_emoji,
        trade_type_label=offer_trade_label,
        trade_price=trade_price,
        trade_quantity=trade_quantity,
        commodity_name=commodity_name,
        trade_number=trade_number,
        trade_datetime=trade_datetime,
        counterparty_name=responder_user_name,
        trade_path_summary=trade_path_summary,
        offer_notes=offer_notes,
    )
    return responder_msg, offer_owner_msg


def _build_trade_notification_extra_payload(
    field_prefix: str,
    participant_payload: Mapping[str, object | None],
    *,
    trade: Trade | object,
    recipient_role: str,
    recipient_user_id: int,
    principal_user_id: int,
    side: str,
    trade_path_payload: Mapping[str, object | None],
) -> dict[str, object | None]:
    trade_number = getattr(trade, "trade_number", None)
    return {
        "trade_id": getattr(trade, "id", None),
        "trade_number": trade_number,
        "offer_id": getattr(trade, "offer_id", None),
        "offer_home_server": _offer_home_server(trade),
        "route": _build_trade_profile_route_from_payload(field_prefix, participant_payload),
        "counterparty_profile_user_id": _coerce_user_id(
            participant_payload.get(f"{field_prefix}_profile_user_id")
        ),
        "counterparty_profile_account_name": participant_payload.get(f"{field_prefix}_profile_account_name"),
        "highlight_accountant_user_id": _coerce_user_id(
            participant_payload.get(f"{field_prefix}_highlight_accountant_user_id")
        ),
        "highlight_accountant_relation_display_name": participant_payload.get(
            f"{field_prefix}_highlight_accountant_relation_display_name"
        ),
        "recipient_role": recipient_role,
        "recipient_user_id": recipient_user_id,
        "principal_user_id": principal_user_id,
        "side": side,
        "delivery_receipt_id": None,
        **trade_path_payload,
    }


async def _telegram_requirement_for_recipient(
    db: AsyncSession,
    *,
    user: User | object | None,
    recipient_role: str,
) -> TradeNotificationChannelRequirement:
    if recipient_role == "accountant":
        return TradeNotificationChannelRequirement(
            channel=TELEGRAM_CHANNEL,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            required=False,
            reason="accountant_webapp_only",
        )
    if user is None:
        return TradeNotificationChannelRequirement(
            channel=TELEGRAM_CHANNEL,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            required=False,
            reason="user_not_found",
        )
    telegram_id = _coerce_user_id(getattr(user, "telegram_id", None))
    if telegram_id is None:
        return TradeNotificationChannelRequirement(
            channel=TELEGRAM_CHANNEL,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            required=False,
            reason="telegram_unlinked",
        )

    decision = await evaluate_bot_access(db, user)
    if not decision.allowed:
        return TradeNotificationChannelRequirement(
            channel=TELEGRAM_CHANNEL,
            destination_server=TELEGRAM_DESTINATION_SERVER,
            required=False,
            reason=decision.reason or "bot_access_denied",
            telegram_id=telegram_id,
        )

    return TradeNotificationChannelRequirement(
        channel=TELEGRAM_CHANNEL,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        required=True,
        reason="telegram_required",
        telegram_id=telegram_id,
    )


async def build_trade_completion_notification_audience(
    db: AsyncSession,
    trade: Trade | object,
) -> TradeNotificationAudience:
    """Derive required WebApp and Telegram recipients for a committed completed trade."""
    trade_id = getattr(trade, "id", None)
    trade_number = getattr(trade, "trade_number", None)
    offer_id = getattr(trade, "offer_id", None)
    offer_home_server = _offer_home_server(trade)
    if not _trade_is_completed(trade):
        return TradeNotificationAudience(
            event_type=TRADE_COMPLETED_EVENT_TYPE,
            trade_id=trade_id,
            trade_number=trade_number,
            offer_id=offer_id,
            offer_home_server=offer_home_server,
            trade_path_kind=None,
            trade_path_summary=None,
            recipients=(),
            skipped_reason="trade_not_completed",
        )

    offer_user_id = _coerce_user_id(getattr(trade, "offer_user_id", None))
    responder_user_id = _coerce_user_id(getattr(trade, "responder_user_id", None))
    participant_ids = unique_user_ids([offer_user_id, responder_user_id])
    customer_relation_map = await _load_trade_customer_relation_map_for_user_ids(db, participant_ids)
    identity_map = await load_accountant_chat_identity_map(db, participant_ids)
    trade_path_payload = _build_trade_path_payload(
        offer_user_id=offer_user_id,
        responder_user_id=responder_user_id,
        customer_relation_map=customer_relation_map,
    )

    responder_audience_ids = await build_trade_notification_audience_user_ids(db, [responder_user_id])
    offer_owner_audience_ids = await build_trade_notification_audience_user_ids(db, [offer_user_id])
    all_user_ids = unique_user_ids([*participant_ids, *responder_audience_ids, *offer_owner_audience_ids])
    user_map = await _load_users_by_ids(db, all_user_ids)

    offer_user = getattr(trade, "offer_user", None) or (user_map.get(offer_user_id) if offer_user_id else None)
    responder_user = getattr(trade, "responder_user", None) or (
        user_map.get(responder_user_id) if responder_user_id else None
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

    responder_emoji, responder_label, offer_emoji, offer_label = _trade_labels(trade)
    trade_datetime = to_jalali_str(getattr(trade, "created_at", None), "%Y/%m/%d   %H:%M") or ""
    commodity_name = _commodity_name(trade)
    trade_quantity = int(getattr(trade, "quantity", 0) or 0)
    trade_price = int(getattr(trade, "price", 0) or 0)
    responder_telegram_message, offer_owner_telegram_message = _build_trade_message_bundle(
        responder_trade_emoji=responder_emoji,
        responder_trade_label=responder_label,
        offer_trade_emoji=offer_emoji,
        offer_trade_label=offer_label,
        trade_price=trade_price,
        trade_quantity=trade_quantity,
        commodity_name=commodity_name,
        trade_number=int(trade_number or 0),
        trade_datetime=trade_datetime,
        offer_user_name=str(offer_user_payload.get("offer_user_name") or _user_display_name(offer_user)),
        responder_user_name=str(responder_user_payload.get("responder_user_name") or _user_display_name(responder_user)),
        customer_relation_map=customer_relation_map,
        trade_path_summary=trade_path_payload.get("trade_path_summary"),
        offer_notes=_offer_notes(trade),
    )

    side_specs = (
        {
            "side": "responder",
            "principal_user_id": responder_user_id,
            "audience_user_ids": responder_audience_ids,
            "trade_emoji": responder_emoji,
            "trade_label": responder_label,
            "counterparty_payload_prefix": "offer_user",
            "counterparty_payload": offer_user_payload,
            "counterparty_name": str(offer_user_payload.get("offer_user_name") or _user_display_name(offer_user)),
            "counterparty_user_id": offer_user_id,
            "telegram_message": responder_telegram_message,
        },
        {
            "side": "offer_owner",
            "principal_user_id": offer_user_id,
            "audience_user_ids": offer_owner_audience_ids,
            "trade_emoji": offer_emoji,
            "trade_label": offer_label,
            "counterparty_payload_prefix": "responder_user",
            "counterparty_payload": responder_user_payload,
            "counterparty_name": str(responder_user_payload.get("responder_user_name") or _user_display_name(responder_user)),
            "counterparty_user_id": responder_user_id,
            "telegram_message": offer_owner_telegram_message,
        },
    )

    recipients: list[TradeNotificationAudienceRecipient] = []
    seen_recipient_channels: set[tuple[int, str]] = set()
    for side_spec in side_specs:
        principal_user_id = _coerce_user_id(side_spec["principal_user_id"])
        if principal_user_id is None:
            continue
        for audience_user_id in unique_user_ids(side_spec["audience_user_ids"]):
            recipient_role = str(side_spec["side"]) if audience_user_id == principal_user_id else "accountant"
            webapp_key = (audience_user_id, WEBAPP_CHANNEL)
            telegram_key = (audience_user_id, TELEGRAM_CHANNEL)
            if webapp_key in seen_recipient_channels and telegram_key in seen_recipient_channels:
                continue

            recipient_user = user_map.get(audience_user_id)
            webapp_message = _build_trade_notification_message(
                trade_emoji=str(side_spec["trade_emoji"]),
                trade_type_label=str(side_spec["trade_label"]),
                trade_price=trade_price,
                trade_quantity=trade_quantity,
                commodity_name=commodity_name,
                trade_number=int(trade_number or 0),
                trade_datetime=trade_datetime,
                counterparty_name=str(side_spec["counterparty_name"]),
                counterparty_user_id=_coerce_user_id(side_spec["counterparty_user_id"]),
                audience_user_id=audience_user_id,
                customer_relation_map=customer_relation_map,
                trade_path_summary=trade_path_payload.get("trade_path_summary"),
                offer_notes=_offer_notes(trade),
            )
            webapp_requirement = TradeNotificationChannelRequirement(
                channel=WEBAPP_CHANNEL,
                destination_server=WEBAPP_DESTINATION_SERVER,
                required=True,
                reason="webapp_required",
                message=webapp_message,
            )
            telegram_requirement = await _telegram_requirement_for_recipient(
                db,
                user=recipient_user,
                recipient_role=recipient_role,
            )
            if telegram_requirement.required:
                telegram_message = str(side_spec["telegram_message"])
                if _recipient_is_customer(audience_user_id, customer_relation_map):
                    hide_counterparty = _should_hide_counterparty_for_recipient(
                        audience_user_id=audience_user_id,
                        counterparty_user_id=_coerce_user_id(side_spec["counterparty_user_id"]),
                        customer_relation_map=customer_relation_map,
                    )
                    telegram_message = _build_trade_telegram_message(
                        trade_emoji=str(side_spec["trade_emoji"]),
                        trade_type_label=str(side_spec["trade_label"]),
                        trade_price=trade_price,
                        trade_quantity=trade_quantity,
                        commodity_name=commodity_name,
                        trade_number=int(trade_number or 0),
                        trade_datetime=trade_datetime,
                        counterparty_name=str(side_spec["counterparty_name"]),
                        hide_counterparty=hide_counterparty,
                        trade_path_summary=trade_path_payload.get("trade_path_summary"),
                        offer_notes=_offer_notes(trade),
                    )
                telegram_requirement = TradeNotificationChannelRequirement(
                    channel=telegram_requirement.channel,
                    destination_server=telegram_requirement.destination_server,
                    required=True,
                    reason=telegram_requirement.reason,
                    telegram_id=telegram_requirement.telegram_id,
                    message=telegram_message,
                )

            channel_requirements = []
            if webapp_key not in seen_recipient_channels:
                channel_requirements.append(webapp_requirement)
                seen_recipient_channels.add(webapp_key)
            if telegram_key not in seen_recipient_channels:
                channel_requirements.append(telegram_requirement)
                seen_recipient_channels.add(telegram_key)
            if not channel_requirements:
                continue

            recipients.append(
                TradeNotificationAudienceRecipient(
                    recipient_user_id=audience_user_id,
                    recipient_role=recipient_role,
                    principal_user_id=principal_user_id,
                    side=str(side_spec["side"]),
                    counterparty_user_id=_coerce_user_id(side_spec["counterparty_user_id"]),
                    webapp_message=webapp_message,
                    extra_payload=_build_trade_notification_extra_payload(
                        str(side_spec["counterparty_payload_prefix"]),
                        side_spec["counterparty_payload"],
                        trade=trade,
                        recipient_role=recipient_role,
                        recipient_user_id=audience_user_id,
                        principal_user_id=principal_user_id,
                        side=str(side_spec["side"]),
                        trade_path_payload=trade_path_payload,
                    ),
                    channel_requirements=tuple(channel_requirements),
                )
            )

    return TradeNotificationAudience(
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        trade_id=trade_id,
        trade_number=trade_number,
        offer_id=offer_id,
        offer_home_server=offer_home_server,
        trade_path_kind=trade_path_payload.get("trade_path_kind"),
        trade_path_summary=trade_path_payload.get("trade_path_summary"),
        recipients=tuple(recipients),
    )
