"""Durable private Bot interaction receipts and generation-fenced anchors."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationEnqueueResult,
    TelegramNotificationRecipient,
    enqueue_telegram_action_notification_once,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultContract,
    TelegramInteractionResultRequirement,
    TelegramInteractionTargetReference,
    build_known_message_target,
    build_interaction_result_contract,
    parse_interaction_target_reference,
    parse_interaction_result_contract,
)
from core.telegram_delivery_notification_action_contract import (
    telegram_notification_action_policy,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFlowExit,
)
from core.utils import utc_now
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState
from models.telegram_notification_outbox import TelegramNotificationOutbox


@dataclass(frozen=True, slots=True)
class TelegramInteractionEnqueueResult:
    notification: TelegramNotificationEnqueueResult
    contract: TelegramInteractionResultContract
    anchor_state: TelegramInteractionAnchorState | None


class TelegramInteractionOutboxSurfaceError(PermissionError):
    """Raised when foreign-local interaction state is touched on Iran."""


def _anchor_advisory_key(chat_id: int) -> int:
    return int.from_bytes(
        sha256(f"telegram-interaction-anchor:{chat_id}".encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=True,
    )


def _persistent_menu_present(reply_markup: Mapping[str, Any] | None) -> bool:
    if not isinstance(reply_markup, Mapping):
        return False
    keyboard = reply_markup.get("keyboard")
    return isinstance(keyboard, list) and bool(keyboard)


def _existing_contract(outbox: TelegramNotificationOutbox) -> TelegramInteractionResultContract:
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping):
        raise ValueError("telegram_interaction_existing_payload_invalid")
    raw_contract = extra_payload.get("interaction_result")
    if raw_contract is None:
        raise ValueError("telegram_interaction_existing_contract_missing")
    return parse_interaction_result_contract(raw_contract)


def _existing_target(
    outbox: TelegramNotificationOutbox,
) -> TelegramInteractionTargetReference | None:
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping):
        raise ValueError("telegram_interaction_existing_payload_invalid")
    raw_target = extra_payload.get("interaction_target")
    if raw_target is None:
        return None
    return parse_interaction_target_reference(raw_target)


def _validate_replayed_contract(
    contract: TelegramInteractionResultContract,
    *,
    method: str,
    logical_message_key: str,
    result_requirement: TelegramInteractionResultRequirement,
    anchor_effect: TelegramInteractionAnchorEffect,
    temporary_context_keyboard: bool,
    flow_exit: TelegramFlowExit | None,
    persistent_menu_present: bool,
) -> None:
    if (
        contract.method != method
        or contract.logical_message_key != logical_message_key
        or contract.result_requirement != result_requirement
        or contract.anchor_effect != anchor_effect
        or contract.temporary_context_keyboard != temporary_context_keyboard
        or contract.flow_exit != flow_exit
        or contract.persistent_menu_present != persistent_menu_present
    ):
        raise ValueError("telegram_interaction_replay_contract_conflict")


async def _find_existing(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> TelegramNotificationOutbox | None:
    return (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _return_existing(
    db: AsyncSession,
    *,
    existing: TelegramNotificationOutbox,
    recipient: TelegramNotificationRecipient,
    action: TelegramDeliveryAction,
    source_id: str,
    text: str,
    user_sync_version: int,
    parse_mode: str | None,
    reply_markup: Mapping[str, Any] | None,
    method: str,
    logical_message_key: str,
    result_requirement: TelegramInteractionResultRequirement,
    anchor_effect: TelegramInteractionAnchorEffect,
    temporary_context_keyboard: bool,
    flow_exit: TelegramFlowExit | None,
    persistent_menu_present: bool,
    interaction_target: TelegramInteractionTargetReference | None,
) -> TelegramInteractionEnqueueResult:
    contract = _existing_contract(existing)
    _validate_replayed_contract(
        contract,
        method=method,
        logical_message_key=logical_message_key,
        result_requirement=result_requirement,
        anchor_effect=anchor_effect,
        temporary_context_keyboard=temporary_context_keyboard,
        flow_exit=flow_exit,
        persistent_menu_present=persistent_menu_present,
    )
    if _existing_target(existing) != interaction_target:
        raise ValueError("telegram_interaction_replay_target_conflict")
    notification = await enqueue_telegram_action_notification_once(
        db,
        recipient=recipient,
        action=action,
        source_id=source_id,
        text=text,
        user_sync_version=user_sync_version,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        interaction_result=contract,
        interaction_target=interaction_target,
    )
    anchor = (
        await db.get(TelegramInteractionAnchorState, recipient.telegram_id)
        if anchor_effect == TelegramInteractionAnchorEffect.SET_CURRENT
        else None
    )
    return TelegramInteractionEnqueueResult(
        notification=notification,
        contract=contract,
        anchor_state=anchor,
    )


async def enqueue_private_interaction_once(
    db: AsyncSession,
    *,
    current_server: str,
    recipient: TelegramNotificationRecipient,
    action: TelegramDeliveryAction | str,
    source_id: str,
    logical_message_key: str,
    text: str,
    user_sync_version: int,
    parse_mode: str | None = None,
    reply_markup: Mapping[str, Any] | None = None,
    result_requirement: TelegramInteractionResultRequirement | str = (
        TelegramInteractionResultRequirement.NONE
    ),
    anchor_effect: TelegramInteractionAnchorEffect | str = (
        TelegramInteractionAnchorEffect.PRESERVE_CURRENT
    ),
    temporary_context_keyboard: bool = False,
    flow_exit: TelegramFlowExit | str | None = None,
) -> TelegramInteractionEnqueueResult:
    """Persist one interaction; allocate an anchor generation when requested."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramInteractionOutboxSurfaceError(
            "telegram_interaction_outbox_is_foreign_local"
        )
    if (
        isinstance(recipient.user_id, bool)
        or not isinstance(recipient.user_id, int)
        or recipient.user_id <= 0
        or isinstance(recipient.telegram_id, bool)
        or not isinstance(recipient.telegram_id, int)
        or recipient.telegram_id <= 0
    ):
        raise ValueError("telegram_interaction_recipient_invalid")
    policy = telegram_notification_action_policy(action)
    if policy.state_contract != "user_route":
        raise ValueError("telegram_interaction_action_requires_user_route")
    normalized_source_id = str(source_id or "").strip()
    logical_key = str(logical_message_key or "").strip()
    if not normalized_source_id or len(normalized_source_id) > 120:
        raise ValueError("telegram_interaction_source_id_invalid")
    if not logical_key or len(logical_key) > 192:
        raise ValueError("telegram_interaction_logical_message_key_invalid")
    try:
        requirement = TelegramInteractionResultRequirement(
            str(getattr(result_requirement, "value", result_requirement))
        )
        anchor = TelegramInteractionAnchorEffect(
            str(getattr(anchor_effect, "value", anchor_effect))
        )
        normalized_exit = (
            None
            if flow_exit is None
            else TelegramFlowExit(str(getattr(flow_exit, "value", flow_exit)))
        )
    except ValueError as exc:
        raise ValueError("telegram_interaction_enqueue_contract_enum_invalid") from exc
    menu_present = _persistent_menu_present(reply_markup)
    dedupe_key = telegram_notification_dedupe_key(
        source_type=policy.source_type,
        source_id=normalized_source_id,
        recipient_user_id=recipient.user_id,
    )
    existing = await _find_existing(db, dedupe_key=dedupe_key)
    if existing is not None:
        return await _return_existing(
            db,
            existing=existing,
            recipient=recipient,
            action=policy.action,
            source_id=normalized_source_id,
            text=text,
            user_sync_version=user_sync_version,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            method="sendMessage",
            logical_message_key=logical_key,
            result_requirement=requirement,
            anchor_effect=anchor,
            temporary_context_keyboard=bool(temporary_context_keyboard),
            flow_exit=normalized_exit,
            persistent_menu_present=menu_present,
            interaction_target=None,
        )

    if anchor != TelegramInteractionAnchorEffect.SET_CURRENT:
        contract = build_interaction_result_contract(
            logical_message_key=logical_key,
            method="sendMessage",
            destination_class=TelegramDestinationClass.PRIVATE,
            result_requirement=requirement,
            anchor_effect=anchor,
            authenticated=True,
            temporary_context_keyboard=bool(temporary_context_keyboard),
            flow_exit=normalized_exit,
            persistent_menu_present=menu_present,
        )
        notification = await enqueue_telegram_action_notification_once(
            db,
            recipient=recipient,
            action=policy.action,
            source_id=normalized_source_id,
            text=text,
            user_sync_version=user_sync_version,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            interaction_result=contract,
        )
        return TelegramInteractionEnqueueResult(notification, contract, None)

    if requirement != TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID:
        raise ValueError("telegram_interaction_anchor_requires_message_result")
    await db.execute(
        select(func.pg_advisory_xact_lock(_anchor_advisory_key(recipient.telegram_id)))
    )
    existing = await _find_existing(db, dedupe_key=dedupe_key)
    if existing is not None:
        return await _return_existing(
            db,
            existing=existing,
            recipient=recipient,
            action=policy.action,
            source_id=normalized_source_id,
            text=text,
            user_sync_version=user_sync_version,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            method="sendMessage",
            logical_message_key=logical_key,
            result_requirement=requirement,
            anchor_effect=anchor,
            temporary_context_keyboard=bool(temporary_context_keyboard),
            flow_exit=normalized_exit,
            persistent_menu_present=menu_present,
            interaction_target=None,
        )
    anchor_state = (
        await db.execute(
            select(TelegramInteractionAnchorState)
            .where(TelegramInteractionAnchorState.chat_id == recipient.telegram_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    next_generation = (
        int(anchor_state.desired_generation) + 1 if anchor_state is not None else 1
    )
    contract = build_interaction_result_contract(
        logical_message_key=logical_key,
        method="sendMessage",
        destination_class=TelegramDestinationClass.PRIVATE,
        result_requirement=requirement,
        anchor_effect=anchor,
        anchor_generation=next_generation,
        authenticated=True,
        temporary_context_keyboard=bool(temporary_context_keyboard),
        flow_exit=normalized_exit,
        persistent_menu_present=menu_present,
    )
    notification = await enqueue_telegram_action_notification_once(
        db,
        recipient=recipient,
        action=policy.action,
        source_id=normalized_source_id,
        text=text,
        user_sync_version=user_sync_version,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        interaction_result=contract,
    )
    outbox_id = getattr(notification.outbox, "id", None)
    if isinstance(outbox_id, bool) or not isinstance(outbox_id, int) or outbox_id <= 0:
        raise RuntimeError("telegram_interaction_outbox_id_missing")
    if anchor_state is None:
        anchor_state = TelegramInteractionAnchorState(
            chat_id=recipient.telegram_id,
            recipient_user_id=recipient.user_id,
            desired_generation=next_generation,
            desired_outbox_id=outbox_id,
            desired_logical_message_key=logical_key,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(anchor_state)
    else:
        anchor_state.recipient_user_id = recipient.user_id
        anchor_state.desired_generation = next_generation
        anchor_state.desired_outbox_id = outbox_id
        anchor_state.desired_logical_message_key = logical_key
        anchor_state.updated_at = utc_now()
    await db.flush()
    return TelegramInteractionEnqueueResult(
        notification=notification,
        contract=contract,
        anchor_state=anchor_state,
    )


async def enqueue_private_interaction_edit_once(
    db: AsyncSession,
    *,
    current_server: str,
    recipient: TelegramNotificationRecipient,
    action: TelegramDeliveryAction | str,
    source_id: str,
    logical_message_key: str,
    target_message_id: int,
    text: str,
    user_sync_version: int,
    parse_mode: str | None = None,
    reply_markup: Mapping[str, Any] | None = None,
) -> TelegramInteractionEnqueueResult:
    """Persist one private ``editMessageText`` with a known Telegram target."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramInteractionOutboxSurfaceError(
            "telegram_interaction_outbox_is_foreign_local"
        )
    if (
        isinstance(recipient.user_id, bool)
        or not isinstance(recipient.user_id, int)
        or recipient.user_id <= 0
        or isinstance(recipient.telegram_id, bool)
        or not isinstance(recipient.telegram_id, int)
        or recipient.telegram_id <= 0
    ):
        raise ValueError("telegram_interaction_recipient_invalid")
    policy = telegram_notification_action_policy(action)
    if policy.state_contract != "user_route":
        raise ValueError("telegram_interaction_action_requires_user_route")
    normalized_source_id = str(source_id or "").strip()
    logical_key = str(logical_message_key or "").strip()
    if not normalized_source_id or len(normalized_source_id) > 120:
        raise ValueError("telegram_interaction_source_id_invalid")
    if not logical_key or len(logical_key) > 192:
        raise ValueError("telegram_interaction_logical_message_key_invalid")
    target = build_known_message_target(
        chat_id=recipient.telegram_id,
        message_id=target_message_id,
    )
    menu_present = _persistent_menu_present(reply_markup)
    if menu_present:
        raise ValueError("telegram_interaction_edit_persistent_menu_forbidden")
    contract = build_interaction_result_contract(
        logical_message_key=logical_key,
        method="editMessageText",
        destination_class=TelegramDestinationClass.PRIVATE,
        result_requirement=TelegramInteractionResultRequirement.NONE,
        anchor_effect=TelegramInteractionAnchorEffect.PRESERVE_CURRENT,
        authenticated=True,
        temporary_context_keyboard=False,
        flow_exit=None,
        persistent_menu_present=False,
    )
    dedupe_key = telegram_notification_dedupe_key(
        source_type=policy.source_type,
        source_id=normalized_source_id,
        recipient_user_id=recipient.user_id,
    )
    existing = await _find_existing(db, dedupe_key=dedupe_key)
    if existing is not None:
        return await _return_existing(
            db,
            existing=existing,
            recipient=recipient,
            action=policy.action,
            source_id=normalized_source_id,
            text=text,
            user_sync_version=user_sync_version,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            method="editMessageText",
            logical_message_key=logical_key,
            result_requirement=TelegramInteractionResultRequirement.NONE,
            anchor_effect=TelegramInteractionAnchorEffect.PRESERVE_CURRENT,
            temporary_context_keyboard=False,
            flow_exit=None,
            persistent_menu_present=False,
            interaction_target=target,
        )
    notification = await enqueue_telegram_action_notification_once(
        db,
        recipient=recipient,
        action=policy.action,
        source_id=normalized_source_id,
        text=text,
        user_sync_version=user_sync_version,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        interaction_result=contract,
        interaction_target=target,
    )
    return TelegramInteractionEnqueueResult(notification, contract, None)
