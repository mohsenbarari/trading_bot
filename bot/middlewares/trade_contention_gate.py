"""Pre-auth Redis gate for Telegram channel trade callbacks."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import redis.asyncio as redis
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update
from redis.asyncio import Redis

from bot.utils.trade_suggestion_messages import PRIVATE_SUGGESTION_CONFIRM_TIMEOUT
from core.config import settings
from core.redis import get_redis_client, pool
from core.services.trade_contention_gate import try_acquire_trade_contention_gate
from core.telegram_trade_callbacks import (
    CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX,
    CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX,
)


logger = logging.getLogger(__name__)

TELEGRAM_TRADE_CONFIRM_PREFIX = "trade:telegram-confirm"
TELEGRAM_TRADE_CONFIRM_MESSAGE = "برای تایید دوباره روی همان دکمه بزنید ☑️"
TELEGRAM_TRADE_BUSY_MESSAGE = "درخواست دیگری همزمان روی این لفظ در حال ثبت است. چند لحظه بعد دوباره تلاش کنید."


@dataclass(frozen=True)
class ParsedTelegramTradeCallback:
    raw_data: str
    amount: int
    offer_public_id: str | None = None
    offer_id: int | None = None


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_telegram_trade_callback_data(raw_data: str | None) -> ParsedTelegramTradeCallback | None:
    data = str(raw_data or "").strip()
    if not data:
        return None

    public_prefix = f"{CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX}:"
    if data.startswith(public_prefix):
        parts = data.split(":")
        if len(parts) != 3:
            return None
        public_id = parts[1].strip()
        amount = _positive_int(parts[2])
        if not public_id or amount is None:
            return None
        return ParsedTelegramTradeCallback(raw_data=data, offer_public_id=public_id, amount=amount)

    legacy_prefix = f"{CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX}:"
    if data.startswith(legacy_prefix):
        parts = data.split(":")
        if len(parts) != 3:
            return None
        offer_id = _positive_int(parts[1])
        amount = _positive_int(parts[2])
        if offer_id is None or amount is None:
            return None
        return ParsedTelegramTradeCallback(raw_data=data, offer_id=offer_id, amount=amount)

    return None


def _extract_callback_query(event: TelegramObject) -> CallbackQuery | None:
    if isinstance(event, CallbackQuery):
        return event
    if isinstance(event, Update):
        return event.callback_query
    if hasattr(event, "data") and callable(getattr(event, "answer", None)):
        return event  # type: ignore[return-value]
    return None


def _is_channel_callback(callback: CallbackQuery) -> bool:
    configured_channel_id = getattr(settings, "channel_id", None)
    if configured_channel_id is None:
        return False
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    try:
        return int(getattr(chat, "id", 0)) == int(configured_channel_id)
    except (TypeError, ValueError):
        return False


def _confirmation_key(*, telegram_id: int, parsed: ParsedTelegramTradeCallback) -> str:
    digest = hashlib.sha256(f"{telegram_id}:{parsed.raw_data}".encode("utf-8")).hexdigest()[:32]
    return f"{TELEGRAM_TRADE_CONFIRM_PREFIX}:{digest}"


def _confirmation_ttl_seconds() -> int:
    return max(1, int(PRIVATE_SUGGESTION_CONFIRM_TIMEOUT))


def _get_confirmation_client() -> tuple[Redis, bool]:
    try:
        return get_redis_client(), False
    except Exception:
        return redis.Redis(connection_pool=pool), True


async def claim_telegram_trade_confirmation(*, telegram_id: int, parsed: ParsedTelegramTradeCallback) -> bool | None:
    """Return True for confirmed second tap, False for first tap, None to fall back."""
    client: Redis | None = None
    owns_client = False
    key = _confirmation_key(telegram_id=telegram_id, parsed=parsed)
    try:
        client, owns_client = _get_confirmation_client()
        first_tap_stored = await client.set(key, "1", nx=True, ex=_confirmation_ttl_seconds())
        if first_tap_stored:
            return False
        return bool(await client.delete(key))
    except Exception as exc:
        logger.warning("Telegram trade pre-auth confirmation unavailable; falling back: %s", type(exc).__name__)
        return None
    finally:
        if owns_client and client is not None:
            await client.aclose()


class TradeContentionGateMiddleware(BaseMiddleware):
    """Reject hot-offer Telegram losers before auth/session DB checkout."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        callback = _extract_callback_query(event)
        parsed = parse_telegram_trade_callback_data(getattr(callback, "data", None) if callback else None)
        telegram_user = getattr(callback, "from_user", None) if callback else None
        telegram_id = _positive_int(getattr(telegram_user, "id", None))
        if callback is None or parsed is None or telegram_id is None or not _is_channel_callback(callback):
            return await handler(event, data)
        message_chat_id = getattr(getattr(getattr(callback, "message", None), "chat", None), "id", None)
        if not settings.channel_id or int(message_chat_id or 0) != int(settings.channel_id):
            return await handler(event, data)

        confirmed = await claim_telegram_trade_confirmation(telegram_id=telegram_id, parsed=parsed)
        if confirmed is False:
            await callback.answer(TELEGRAM_TRADE_CONFIRM_MESSAGE, show_alert=False)
            return None
        if confirmed is None:
            return await handler(event, data)

        lease = await try_acquire_trade_contention_gate(
            offer_public_id=parsed.offer_public_id,
            offer_id=parsed.offer_id,
        )
        if not lease.acquired:
            await callback.answer(TELEGRAM_TRADE_BUSY_MESSAGE, show_alert=False)
            return None

        data["trade_contention_preconfirmed"] = True
        try:
            return await handler(event, data)
        finally:
            await lease.release()
