"""Queue-owned Telegram responses before a local authenticated User exists."""
from __future__ import annotations

import inspect
import re
from hashlib import sha256
from typing import Any, Mapping

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.telegram_scheduled_operation_service import (
    enqueue_pre_auth_interaction_once,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.telegram_delivery_queue_contract import TelegramDestinationClass


_SOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_UNSET = object()


class TelegramPreAuthInteractionRouteError(ValueError):
    """Raised before an unsafe unauthenticated private-chat enqueue."""


def _automatic_source_key() -> str:
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame else None
        while caller is not None and caller.f_globals.get("__name__") == __name__:
            caller = caller.f_back
        if caller is None:
            raise TelegramPreAuthInteractionRouteError(
                "telegram_preauth_callsite_identity_missing"
            )
        identity = (
            f"preauth-callsite-v1:{caller.f_globals.get('__name__', '')}:"
            f"{caller.f_code.co_qualname}:{caller.f_lineno}"
        )
        return "auto-" + sha256(identity.encode("utf-8")).hexdigest()[:32]
    finally:
        del frame


def _source_key(value: str | None) -> str:
    normalized = _automatic_source_key() if value is None else str(value).strip().lower()
    if _SOURCE_KEY_PATTERN.fullmatch(normalized) is None:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_source_key_invalid"
        )
    return normalized


def _serialized_reply_markup(value: Any) -> Mapping[str, Any] | None:
    if value is _UNSET or value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_reply_markup_invalid"
        )
    serialized = dump(mode="json", exclude_none=True)
    if not isinstance(serialized, Mapping):
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_reply_markup_invalid"
        )
    return dict(serialized)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _nonzero_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed != 0 else None


def _event_route(event: Any) -> tuple[Any, int, str]:
    message = getattr(event, "message", None) or event
    chat = getattr(message, "chat", None)
    chat_id = _positive_int(getattr(chat, "id", None))
    if chat_id is None:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_private_chat_missing"
        )
    chat_type = str(
        getattr(getattr(chat, "type", None), "value", getattr(chat, "type", ""))
        or ""
    ).lower()
    if chat_type and chat_type != "private":
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_private_chat_required"
        )
    actor = getattr(event, "from_user", None) or getattr(message, "from_user", None)
    actor_id = _positive_int(getattr(actor, "id", None))
    if actor_id is not None and actor_id != chat_id:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_private_route_mismatch"
        )
    callback_id = str(getattr(event, "id", "") or "").strip()
    message_id = _positive_int(getattr(message, "message_id", None))
    raw_event_key = callback_id or str(message_id or "")
    if not raw_event_key:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_event_identity_missing"
        )
    event_hash = sha256(
        f"preauth-event-v1:{chat_id}:{raw_event_key}".encode("utf-8")
    ).hexdigest()[:24]
    return message, chat_id, event_hash


def _source_id(*, source_key: str, chat_id: int, event_key: str) -> str:
    route_hash = sha256(
        f"preauth-route-v1:{chat_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"preauth:{source_key}:{route_hash}:{event_key}"


def _non_private_event_route(message: Any) -> tuple[int, str]:
    chat = getattr(message, "chat", None)
    chat_id = _nonzero_int(getattr(chat, "id", None))
    chat_type = str(
        getattr(getattr(chat, "type", None), "value", getattr(chat, "type", ""))
        or ""
    ).lower()
    message_id = _positive_int(getattr(message, "message_id", None))
    if chat_id is None or chat_id > 0 or chat_type == "private" or message_id is None:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_non_private_route_invalid"
        )
    event_hash = sha256(
        f"preauth-public-event-v1:{chat_id}:{message_id}".encode("utf-8")
    ).hexdigest()[:24]
    return chat_id, event_hash


async def _enqueue(
    *,
    chat_id: int,
    event_key: str,
    source_key: str | None,
    text: str,
    method: str,
    message_id: int | None,
    parse_mode: Any,
    reply_markup: Any,
    destination_class: TelegramDestinationClass = TelegramDestinationClass.PRIVATE,
    session: Any,
    commit: bool,
):
    normalized_source_key = _source_key(source_key)
    markup = _serialized_reply_markup(reply_markup)

    async def _persist(db):
        result = await enqueue_pre_auth_interaction_once(
            db,
            current_server=current_server(),
            chat_id=chat_id,
            source_id=_source_id(
                source_key=normalized_source_key,
                chat_id=chat_id,
                event_key=event_key,
            ),
            text=text,
            method=method,
            destination_class=destination_class,
            message_id=message_id,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _persist(session)
    async with AsyncSessionLocal() as db:
        return await _persist(db)


async def answer_pre_auth_message_via_runtime(
    message: Any,
    text: str,
    *,
    source_key: str | None = None,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.answer(text, **kwargs)
    _message, chat_id, event_key = _event_route(message)
    return await _enqueue(
        chat_id=chat_id,
        event_key=event_key,
        source_key=source_key,
        text=text,
        method="sendMessage",
        message_id=None,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        session=session,
        commit=commit,
    )


async def answer_pre_auth_non_private_message_via_runtime(
    message: Any,
    text: str,
    *,
    source_key: str | None = None,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Preserve the normal group/supergroup security response durably."""

    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.answer(text, **kwargs)
    chat_id, event_key = _non_private_event_route(message)
    return await _enqueue(
        chat_id=chat_id,
        event_key=event_key,
        source_key=source_key,
        text=text,
        method="sendMessage",
        message_id=None,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        destination_class=TelegramDestinationClass.CHANNEL,
        session=session,
        commit=commit,
    )


async def answer_pre_auth_callback_message_via_runtime(
    callback: Any,
    text: str,
    *,
    source_key: str | None = None,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        legacy_kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            legacy_kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            legacy_kwargs["reply_markup"] = reply_markup
        return await callback.message.answer(text, **legacy_kwargs)
    _message, chat_id, event_key = _event_route(callback)
    return await _enqueue(
        chat_id=chat_id,
        event_key=event_key,
        source_key=source_key,
        text=text,
        method="sendMessage",
        message_id=None,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        session=session,
        commit=commit,
    )


async def edit_pre_auth_callback_message_via_runtime(
    callback: Any,
    text: str,
    *,
    source_key: str | None = None,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await callback.message.edit_text(text, **kwargs)
    message, chat_id, event_key = _event_route(callback)
    target_message_id = _positive_int(getattr(message, "message_id", None))
    if target_message_id is None:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_edit_target_missing"
        )
    return await _enqueue(
        chat_id=chat_id,
        event_key=event_key,
        source_key=source_key,
        text=text,
        method="editMessageText",
        message_id=target_message_id,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        session=session,
        commit=commit,
    )


async def answer_pre_auth_chat_via_runtime(
    bot: Any,
    chat_id: int,
    text: str,
    *,
    event_key: str,
    source_key: str | None = None,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    normalized_chat_id = _positive_int(chat_id)
    normalized_event_key = str(event_key or "").strip()
    if normalized_chat_id is None or not normalized_event_key:
        raise TelegramPreAuthInteractionRouteError(
            "telegram_preauth_explicit_route_invalid"
        )
    event_hash = sha256(
        f"preauth-explicit-event-v1:{normalized_chat_id}:{normalized_event_key}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return await _enqueue(
        chat_id=normalized_chat_id,
        event_key=event_hash,
        source_key=source_key,
        text=text,
        method="sendMessage",
        message_id=None,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        session=session,
        commit=commit,
    )
