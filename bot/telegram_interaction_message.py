"""Runtime-owned adapter for durable replies to authenticated Bot messages."""
from __future__ import annotations

import base64
import re
import inspect
from collections.abc import Mapping
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from core.db import AsyncSessionLocal
from core.server_routing import current_server
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_document_interaction_once,
    enqueue_private_interaction_edit_once,
    enqueue_private_interaction_once,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_DOCUMENT_EXPORT_MAX_BYTES,
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
from core.utils import utc_now


_SOURCE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_UNSET = object()
_MARKUP_ONLY_RECEIPT_TEXT = "telegram-interaction-markup-v1"


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


def _automatic_source_key() -> str:
    """Build a privacy-safe stable key for one explicit adapter callsite."""

    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame else None
        while caller is not None and caller.f_globals.get("__name__") == __name__:
            caller = caller.f_back
        if caller is None:
            raise TelegramInteractionMessageRouteError(
                "telegram_interaction_callsite_identity_missing"
            )
        module = str(caller.f_globals.get("__name__", "") or "")
        qualname = str(caller.f_code.co_qualname or caller.f_code.co_name)
        line = int(caller.f_lineno)
        digest = sha256(
            f"telegram-interaction-callsite-v1:{module}:{qualname}:{line}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
        return f"auto-{digest}"
    finally:
        del frame


def _normalized_source_key(value: object | None) -> str:
    if value is None:
        return _automatic_source_key()
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
    source_key: str | None,
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
    source_key: str | None = None,
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
    source_key: str | None = None,
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


async def edit_callback_message_via_runtime(
    callback: Any,
    user: Any,
    text: str,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Edit one known private callback message or persist the exact edit target."""
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
        return await message.edit_text(text, **kwargs)

    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, message_id = _private_message_identity(
        message,
        user,
    )
    event_key = _callback_event_key(callback)
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=f"iedit:{normalized_source_key}:{user_id}:{event_key}",
            logical_message_key=(
                f"private-edit:{user_id}:{normalized_source_key}:{event_key}"
            ),
            target_message_id=message_id,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def edit_callback_reply_markup_via_runtime(
    callback: Any,
    user: Any,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Edit only the known callback message markup through queue ownership."""

    message = getattr(callback, "message", None)
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        kwargs: dict[str, Any] = {}
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.edit_reply_markup(**kwargs)

    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, message_id = _private_message_identity(
        message,
        user,
    )
    normalized_markup = (
        {"inline_keyboard": []}
        if reply_markup is _UNSET or reply_markup is None
        else _serialized_reply_markup(reply_markup)
    )

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=f"imark:{normalized_source_key}:{user_id}:{message_id}",
            logical_message_key=(
                f"private-markup:{user_id}:{normalized_source_key}:{message_id}"
            ),
            target_message_id=message_id,
            text=_MARKUP_ONLY_RECEIPT_TEXT,
            user_sync_version=sync_version,
            method="editMessageReplyMarkup",
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def edit_known_message_via_runtime(
    message: Any,
    user: Any,
    text: str,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Edit a known private message without requiring callback metadata."""

    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        kwargs: dict[str, Any] = {}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        return await message.edit_text(text, **kwargs)

    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, message_id = _private_message_identity(
        message,
        user,
    )
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=f"iedit:{normalized_source_key}:{user_id}:{message_id}",
            logical_message_key=(
                f"private-edit:{user_id}:{normalized_source_key}:{message_id}"
            ),
            target_message_id=message_id,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def edit_interaction_result_via_runtime(
    origin: Any,
    user: Any,
    interaction_result: Any,
    text: str,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Edit a send result after its real provider message id is available.

    Legacy mode edits the aiogram ``Message`` immediately. Queue mode persists
    a dependency on the parent outbox receipt and never fabricates a target id.
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
        return await interaction_result.edit_text(text, **kwargs)

    message = getattr(origin, "message", None) or origin
    normalized_source_key = _normalized_source_key(source_key)
    contract = getattr(interaction_result, "contract", None)
    if getattr(contract, "method", None) == "editMessageText":
        return await edit_callback_message_via_runtime(
            origin,
            user,
            text,
            source_key=normalized_source_key,
            action=action,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            session=session,
            commit=commit,
        )
    user_id, telegram_id, sync_version, _ = _private_message_identity(
        message,
        user,
    )
    notification = getattr(interaction_result, "notification", None)
    source_outbox = getattr(notification, "outbox", None)
    source_receipt_id = _positive_int(getattr(source_outbox, "id", None))
    if source_receipt_id is None:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_result_receipt_missing"
        )
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=(
                f"idep:{normalized_source_key}:{user_id}:{source_receipt_id}"
            ),
            logical_message_key=(
                f"private-result-edit:{user_id}:{normalized_source_key}:"
                f"{source_receipt_id}"
            ),
            source_receipt_id=source_receipt_id,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def edit_delivery_receipt_via_runtime(
    origin: Any,
    user: Any,
    source_receipt_id: int,
    text: str,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Persist an edit that depends on a previously stored send receipt."""

    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_receipt_edit_requires_queue_owner"
        )
    message = getattr(origin, "message", None) or origin
    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, _ = _private_message_identity(
        message,
        user,
    )
    normalized_receipt_id = _positive_int(source_receipt_id)
    if normalized_receipt_id is None:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_result_receipt_missing"
        )
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=(
                f"idep:{normalized_source_key}:{user_id}:{normalized_receipt_id}"
            ),
            logical_message_key=(
                f"private-result-edit:{user_id}:{normalized_source_key}:"
                f"{normalized_receipt_id}"
            ),
            source_receipt_id=normalized_receipt_id,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def schedule_delivery_receipt_markup_cleanup_via_runtime(
    origin: Any,
    user: Any,
    source_receipt_id: int,
    *,
    delay_seconds: float,
    source_key: str | None = None,
    session: Any = None,
    commit: bool = True,
):
    """Persist a delayed button removal against a queued send result."""

    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_receipt_cleanup_requires_queue_owner"
        )
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_receipt_cleanup_delay_invalid"
        )
    bounded_delay = max(0.0, min(float(delay_seconds), 300.0))
    message = getattr(origin, "message", None) or origin
    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, _ = _private_message_identity(
        message,
        user,
    )
    normalized_receipt_id = _positive_int(source_receipt_id)
    if normalized_receipt_id is None:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_result_receipt_missing"
        )
    due_at = utc_now() + timedelta(seconds=bounded_delay)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=(
                f"idep:{normalized_source_key}:{user_id}:{normalized_receipt_id}"
            ),
            logical_message_key=(
                f"private-result-markup:{user_id}:{normalized_source_key}:"
                f"{normalized_receipt_id}"
            ),
            source_receipt_id=normalized_receipt_id,
            text=_MARKUP_ONLY_RECEIPT_TEXT,
            user_sync_version=sync_version,
            method="editMessageReplyMarkup",
            reply_markup={"inline_keyboard": []},
        )
        outbox = result.notification.outbox
        if result.notification.created:
            outbox.next_retry_at = due_at
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def edit_explicit_private_message_via_runtime(
    origin: Any,
    user: Any,
    target_message_id: int,
    text: str,
    *,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.GENERAL_IMMEDIATE,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Persist a private edit when the target id came from durable FSM state."""

    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_explicit_edit_requires_queue_owner"
        )
    message = getattr(origin, "message", None) or origin
    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, _ = _private_message_identity(
        message,
        user,
    )
    normalized_target = _positive_int(target_message_id)
    if normalized_target is None:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_explicit_target_invalid"
        )
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_interaction_edit_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=(
                f"iedit:{normalized_source_key}:{user_id}:{normalized_target}"
            ),
            logical_message_key=(
                f"private-edit:{user_id}:{normalized_source_key}:"
                f"{normalized_target}"
            ),
            target_message_id=normalized_target,
            text=text,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)


async def send_private_document_via_runtime(
    origin: Any,
    user: Any,
    document: Any,
    *,
    caption: str,
    filename: str | None = None,
    bot: Any = None,
    source_key: str | None = None,
    action: TelegramDeliveryAction | str = TelegramDeliveryAction.TRADE_NONCRITICAL,
    parse_mode: Any = _UNSET,
    reply_markup: Any = _UNSET,
    session: Any = None,
    commit: bool = True,
):
    """Send a bounded generated document through the primary durable lane."""

    message = getattr(origin, "message", None) or origin
    if configured_telegram_delivery_runtime().mode != TelegramDeliveryRuntimeMode.QUEUE_V1:
        kwargs: dict[str, Any] = {"document": document, "caption": caption}
        if parse_mode is not _UNSET:
            kwargs["parse_mode"] = parse_mode
        if reply_markup is not _UNSET:
            kwargs["reply_markup"] = reply_markup
        if bot is not None:
            return await bot.send_document(
                chat_id=message.chat.id,
                **kwargs,
            )
        return await message.answer_document(**kwargs)

    normalized_source_key = _normalized_source_key(source_key)
    user_id, telegram_id, sync_version, message_id = _private_message_identity(
        message,
        user,
    )
    path_value = getattr(document, "path", document)
    try:
        path = Path(path_value)
        document_bytes = path.read_bytes()
    except (OSError, TypeError, ValueError) as exc:
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_document_read_failed"
        ) from exc
    if (
        not document_bytes
        or len(document_bytes) > TELEGRAM_DOCUMENT_EXPORT_MAX_BYTES
    ):
        raise TelegramInteractionMessageRouteError(
            "telegram_interaction_document_size_invalid"
        )
    resolved_filename = str(
        filename
        or getattr(document, "filename", None)
        or path.name
    ).strip()
    encoded = base64.b64encode(document_bytes).decode("ascii")
    digest = sha256(document_bytes).hexdigest()
    normalized_markup = _serialized_reply_markup(reply_markup)

    async def _enqueue(db):
        result = await enqueue_private_document_interaction_once(
            db,
            current_server=current_server(),
            recipient=TelegramNotificationRecipient(
                user_id=user_id,
                telegram_id=telegram_id,
            ),
            action=action,
            source_id=f"idoc:{normalized_source_key}:{user_id}:{message_id}",
            logical_message_key=(
                f"private-document:{user_id}:{normalized_source_key}:{message_id}"
            ),
            caption=caption,
            document_base64=encoded,
            document_filename=resolved_filename,
            document_sha256=digest,
            user_sync_version=sync_version,
            parse_mode=None if parse_mode is _UNSET else parse_mode,
            reply_markup=normalized_markup,
        )
        if commit:
            await db.commit()
        return result

    if session is not None:
        return await _enqueue(session)
    async with AsyncSessionLocal() as db:
        return await _enqueue(db)
