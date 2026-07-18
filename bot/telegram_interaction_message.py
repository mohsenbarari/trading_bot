"""Runtime-owned adapter for durable replies to authenticated Bot messages."""
from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_interaction_once,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultRequirement,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFlowExit,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


_SOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_UNSET = object()


class TelegramInteractionMessageRouteError(ValueError):
    """Raised before persistence when an authenticated private route is unsafe."""


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _private_message_identity(message: Any, user: Any) -> tuple[int, int, int, int]:
    user_id = _positive_int(getattr(user, "id", None))
    telegram_id = _positive_int(getattr(user, "telegram_id", None))
    sync_version = _positive_int(getattr(user, "sync_version", None))
    message_id = _positive_int(getattr(message, "message_id", None))
    chat = getattr(message, "chat", None)
    chat_id = _positive_int(getattr(chat, "id", None))
    if None in {user_id, telegram_id, sync_version, message_id, chat_id}:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_message_identity_invalid"
        )
    if chat_id != telegram_id:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_message_route_mismatch"
        )
    return user_id, telegram_id, sync_version, message_id


def _serialized_reply_markup(value: Any) -> Mapping[str, Any] | None:
    if value is _UNSET or value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TelegramInteractionMessageRouteError(
        "telegram_interaction_message_reply_markup_invalid"
    )


def _normalized_source_key(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if _SOURCE_KEY_PATTERN.fullmatch(normalized) is None:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_message_source_key_invalid"
        )
    return normalized


def _callback_event_key(callback: Any) -> str:
    callback_id = str(getattr(callback, "id", "") or "").strip()
    if not callback_id or len(callback_id) > 256:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_callback_identity_invalid"
        )
    digest = sha256(callback_id.encode("utf-8")).hexdigest()[:32]
    return f"cb-{digest}"


async def _enqueue_interaction_message(
    message: Any,
    user: Any,
    text: str,
    *,
    source_key: str,
    event_key: str,
    action: TelegramDeliveryAction | str,
    parse_mode: Any,
    reply_markup: Any,
    set_persistent_anchor: bool,
    temporary_context_keyboard: bool,
    flow_exit: TelegramFlowExit | str | None,
    session: Any,
    commit: bool,
):
    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, _ = _private_message_identity(
        message,
        user,
    )
    normalized_markup = _serialized_reply_markup(reply_markup)
    source_id = f"interaction:{normalized_source_key}:{user_id}:{event_key}"
    logical_key = f"private:{user_id}:{normalized_source_key}:{event_key}"
    result_requirement = (
        TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
        if set_persistent_anchor
        else TelegramInteractionResultRequirement.NONE
    )
    anchor_effect = (
        TelegramInteractionAnchorEffect.SET_CURRENT
        if set_persistent_anchor
        else TelegramInteractionAnchorEffect.PRESERVE_CURRENT
    )

    async def _enqueue(db):
        result = await enqueue_private_interaction_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=source_id,
            logical_message_key=logical_key,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
            result_requirement=result_requirement,
            anchor_effect=anchor_effect,
            temporary_context_keyboard=temporary_context_keyboard,
            flow_exit=flow_exit,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def answer_incoming_message_via_runtime(
    message: Any,
    user: Any,
    text: str,
    *,
    source_key: str,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    set_persistent_anchor: bool = False,
    temporary_context_keyboard: bool = False,
    flow_exit: TelegramFlowExit | str | None = None,
    session: Any = None,
    commit: bool = True,
):
    """Preserve legacy ``message.answer`` or persist one private interaction.

    This adapter is intentionally limited to replies whose caller does not
    need an immediate aiogram ``Message`` result.  Result-dependent edits use
    the separate receipt/dependency contract.
    """
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.answer(text, **kwargs)

    _, _, _, message_id = _private_message_identity(message, user)
    return await _enqueue_interaction_message(
        message,
        user,
        text,
        source_key=source_key,
        event_key=str(message_id),
        action=action,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        set_persistent_anchor=set_persistent_anchor,
        temporary_context_keyboard=temporary_context_keyboard,
        flow_exit=flow_exit,
        session=session,
        commit=commit,
    )


async def answer_callback_message_via_runtime(
    callback: Any,
    user: Any,
    text: str,
    *,
    source_key: str,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    set_persistent_anchor: bool = False,
    temporary_context_keyboard: bool = False,
    flow_exit: TelegramFlowExit | str | None = None,
    session: Any = None,
    commit: bool = True,
):
    """Reply below a callback's private message without callback collisions.

    Queue identity hashes ``callback.id`` so two callback updates on the same
    Telegram message remain distinct while the raw provider identifier never
    enters durable storage.
    """
    message = getattr(callback, "message", None)
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.answer(text, **kwargs)

    return await _enqueue_interaction_message(
        message,
        user,
        text,
        source_key=source_key,
        event_key=_callback_event_key(callback),
        action=action,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        set_persistent_anchor=set_persistent_anchor,
        temporary_context_keyboard=temporary_context_keyboard,
        flow_exit=flow_exit,
        session=session,
        commit=commit,
    )
