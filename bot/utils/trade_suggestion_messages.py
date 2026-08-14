import asyncio
import hashlib
import json
import logging
import time
from datetime import timedelta
from typing import Optional

import redis.asyncio as redis
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.repeat_offer import refresh_repeat_offer_menu_for_expired_offer
from core.db import AsyncSessionLocal
from core.offer_quantity import coalesce_offer_remaining_quantity
from core.redis import pool
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
)
from core.telegram_trade_callbacks import build_channel_trade_callback_data
from core.server_routing import current_server
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.utils import utc_now
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)

TRADE_SUGGESTION_TTL_SECONDS = 15
PRIVATE_SUGGESTION_CONFIRM_TIMEOUT = 3.0

_memory_suggestions: dict[int, dict[str, dict]] = {}


def _decode_record(raw_value) -> dict | None:
    if not raw_value:
        return None
    if isinstance(raw_value, bytes):
        try:
            raw_value = raw_value.decode("utf-8")
        except Exception:
            return None
    try:
        record = json.loads(raw_value)
    except Exception:
        return None
    return record if isinstance(record, dict) else None


def _record_requested_amount(record: dict | None) -> int | None:
    if not record:
        return None
    try:
        requested_amount = int(record.get("requested_amount") or 0)
    except (TypeError, ValueError):
        return None
    return requested_amount if requested_amount > 0 else None


def build_trade_amount_buttons(
    offer_id: int,
    amounts: list[int],
    pending_amount: Optional[int] = None,
    *,
    offer_public_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    seen = set()
    unique_amounts = []
    for amount in amounts:
        if amount not in seen and amount > 0:
            seen.add(amount)
            unique_amounts.append(amount)

    buttons = []
    for amount in unique_amounts:
        label = f"تایید {amount} عدد؟" if pending_amount == amount else f"{amount} عدد"
        buttons.append(
            InlineKeyboardButton(
                text=label,
                callback_data=build_channel_trade_callback_data(
                    offer_id=offer_id,
                    offer_public_id=offer_public_id,
                    amount=amount,
                ),
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


def build_offer_trade_buttons(
    offer_id: int,
    quantity: int,
    remaining: int,
    is_wholesale: bool,
    lot_sizes: Optional[list[int]],
    pending_amount: Optional[int] = None,
    *,
    offer_public_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    all_amounts = get_available_trade_amounts(
        quantity=quantity,
        remaining_quantity=remaining,
        is_wholesale=is_wholesale,
        lot_sizes=sorted(lot_sizes, reverse=True) if lot_sizes else None,
    )
    return build_trade_amount_buttons(
        offer_id,
        all_amounts,
        pending_amount=pending_amount,
        offer_public_id=offer_public_id,
    )


def _record_field(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"


def _record_key(offer_id: int) -> str:
    return f"trade_suggestion:offer:{offer_id}"


async def upsert_trade_suggestion_record(
    offer_id: int,
    chat_id: int,
    message_id: int,
    requested_amount: int,
    ttl_seconds: int = TRADE_SUGGESTION_TTL_SECONDS,
    preserve_requested_amount: bool = False,
) -> None:
    now = time.time()
    field = _record_field(chat_id, message_id)
    stored_requested_amount = int(requested_amount)
    record_key = _record_key(offer_id)
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "requested_amount": stored_requested_amount,
        "expires_at": now + ttl_seconds,
        "offer_id": offer_id,
    }
    redis_client = None
    try:
        redis_client = redis.Redis(connection_pool=pool)
        if preserve_requested_amount:
            existing_amount = _record_requested_amount(_decode_record(await redis_client.hget(record_key, field)))
            if existing_amount is not None:
                stored_requested_amount = existing_amount
                payload["requested_amount"] = stored_requested_amount
        await redis_client.hset(record_key, field, json.dumps(payload, ensure_ascii=False))
        await redis_client.expire(record_key, ttl_seconds + 60)
        return
    except Exception as exc:
        logger.debug(f"Failed to persist trade suggestion record in Redis: {exc}")
    finally:
        if redis_client is not None:
            await redis_client.aclose()

    bucket = _memory_suggestions.setdefault(offer_id, {})
    if preserve_requested_amount:
        existing_amount = _record_requested_amount(bucket.get(field))
        if existing_amount is not None:
            stored_requested_amount = existing_amount
            payload["requested_amount"] = stored_requested_amount
    bucket[field] = payload


async def remove_trade_suggestion_record(offer_id: int, chat_id: int, message_id: int) -> None:
    field = _record_field(chat_id, message_id)
    redis_client = None
    try:
        redis_client = redis.Redis(connection_pool=pool)
        await redis_client.hdel(_record_key(offer_id), field)
    except Exception as exc:
        logger.debug(f"Failed to remove trade suggestion record from Redis: {exc}")
    finally:
        if redis_client is not None:
            await redis_client.aclose()

    bucket = _memory_suggestions.get(offer_id)
    if bucket:
        bucket.pop(field, None)
        if not bucket:
            _memory_suggestions.pop(offer_id, None)


async def get_trade_suggestion_records(offer_id: int) -> list[dict]:
    now = time.time()
    records: list[dict] = []
    redis_client = None
    try:
        redis_client = redis.Redis(connection_pool=pool)
        raw_records = await redis_client.hgetall(_record_key(offer_id))
        for field, raw_value in raw_records.items():
            try:
                record = json.loads(raw_value)
            except Exception:
                await redis_client.hdel(_record_key(offer_id), field)
                continue
            if record.get("expires_at", 0) <= now:
                await redis_client.hdel(_record_key(offer_id), field)
                continue
            records.append(record)
        return records
    except Exception as exc:
        logger.debug(f"Failed to read trade suggestion records from Redis: {exc}")
    finally:
        if redis_client is not None:
            await redis_client.aclose()

    bucket = _memory_suggestions.get(offer_id, {})
    stale_fields = []
    for field, record in bucket.items():
        if record.get("expires_at", 0) <= now:
            stale_fields.append(field)
            continue
        records.append(record)
    for field in stale_fields:
        bucket.pop(field, None)
    if not bucket and offer_id in _memory_suggestions:
        _memory_suggestions.pop(offer_id, None)
    return records


async def _enqueue_suggestion_markup_cleanup(
    *,
    chat_id: int,
    message_id: int,
    source_id: str,
    due_at,
) -> None:
    from core.services.telegram_scheduled_operation_service import (
        enqueue_cosmetic_markup_cleanup_once,
    )

    async with AsyncSessionLocal() as session:
        await enqueue_cosmetic_markup_cleanup_once(
            session,
            current_server=current_server(),
            chat_id=chat_id,
            message_id=message_id,
            source_id=source_id,
            due_at=due_at,
        )
        await session.commit()


async def _clear_suggestion_markup(
    bot: Bot,
    chat_id: int,
    message_id: int,
    *,
    source_id: str | None = None,
) -> None:
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        await _enqueue_suggestion_markup_cleanup(
            chat_id=chat_id,
            message_id=message_id,
            source_id=(
                source_id
                or f"trade-suggestion-clear:{chat_id}:{message_id}"
            ),
            due_at=utc_now(),
        )
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            logger.debug(f"Failed to clear suggestion markup chat={chat_id} message={message_id}: {exc}")


async def _enqueue_trade_suggestion_sync_operations(
    offer_id: int,
    *,
    due_at,
) -> None:
    """Persist exact known-target edits without waiting in process memory."""

    from core.services.telegram_scheduled_operation_service import (
        enqueue_pre_auth_interaction_once,
    )

    records = await get_trade_suggestion_records(offer_id)
    if not records:
        return
    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id)
        if offer:
            await session.refresh(offer, ["commodity"])
        if not offer or offer.status != OfferStatus.ACTIVE:
            for record in records:
                await _enqueue_suggestion_markup_cleanup(
                    chat_id=int(record["chat_id"]),
                    message_id=int(record["message_id"]),
                    source_id=(
                        f"trade-suggestion-state:{offer_id}:"
                        f"{record['chat_id']}:{record['message_id']}"
                    ),
                    due_at=due_at,
                )
            return

        available_amounts = get_available_trade_amounts(
            quantity=offer.quantity,
            remaining_quantity=offer.remaining_quantity,
            is_wholesale=offer.is_wholesale,
            lot_sizes=offer.lot_sizes,
        )
        for record in records:
            chat_id = int(record["chat_id"])
            message_id = int(record["message_id"])
            requested_amount = int(record.get("requested_amount") or 0)
            payload = build_lot_unavailable_suggestion_payload(
                offer_id=offer.id,
                offer_public_id=getattr(offer, "offer_public_id", None),
                requested_amount=requested_amount,
                offer_type=offer.offer_type,
                settlement_type=getattr(offer, "settlement_type", None),
                commodity_name=offer.commodity.name if offer.commodity else None,
                price=offer.price,
                remaining_quantity=coalesce_offer_remaining_quantity(
                    offer.remaining_quantity,
                    offer.quantity,
                ),
                available_amounts=available_amounts,
            )
            reply_markup = build_trade_amount_buttons(
                offer.id,
                available_amounts,
                offer_public_id=getattr(offer, "offer_public_id", None),
            )
            route_hash = hashlib.sha256(
                f"{offer_id}:{chat_id}:{message_id}:{due_at.isoformat()}".encode(
                    "utf-8"
                )
            ).hexdigest()[:32]
            await enqueue_pre_auth_interaction_once(
                session,
                current_server=current_server(),
                chat_id=chat_id,
                message_id=message_id,
                source_id=f"trade-suggestion-sync:{route_hash}",
                text=payload["message"],
                method="editMessageText",
                reply_markup=(
                    reply_markup.model_dump(mode="json", exclude_none=True)
                    if reply_markup is not None
                    else {"inline_keyboard": []}
                ),
                due_at=due_at,
                ttl_seconds=30,
            )
            if not available_amounts:
                await remove_trade_suggestion_record(
                    offer_id,
                    chat_id,
                    message_id,
                )
        await session.commit()


async def sync_trade_suggestions_for_offer(bot: Bot, offer_id: int) -> None:
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        await _enqueue_trade_suggestion_sync_operations(
            offer_id,
            due_at=utc_now(),
        )
        return
    records = await get_trade_suggestion_records(offer_id)
    if not records:
        return

    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id)
        if offer:
            await session.refresh(offer, ["commodity"])

    if not offer or offer.status != OfferStatus.ACTIVE:
        for record in records:
            await _clear_suggestion_markup(
                bot,
                int(record["chat_id"]),
                int(record["message_id"]),
                source_id=(
                    f"trade-suggestion-state:{offer_id}:"
                    f"{record['chat_id']}:{record['message_id']}"
                ),
            )
            await remove_trade_suggestion_record(offer_id, int(record["chat_id"]), int(record["message_id"]))
        return

    available_amounts = get_available_trade_amounts(
        quantity=offer.quantity,
        remaining_quantity=offer.remaining_quantity,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
    )

    for record in records:
        chat_id = int(record["chat_id"])
        message_id = int(record["message_id"])
        requested_amount = int(record.get("requested_amount") or 0)

        payload = build_lot_unavailable_suggestion_payload(
            offer_id=offer.id,
            offer_public_id=getattr(offer, "offer_public_id", None),
            requested_amount=requested_amount,
            offer_type=offer.offer_type,
            settlement_type=getattr(offer, "settlement_type", None),
            commodity_name=offer.commodity.name if offer.commodity else None,
            price=offer.price,
            remaining_quantity=coalesce_offer_remaining_quantity(
                offer.remaining_quantity,
                offer.quantity,
            ),
            available_amounts=available_amounts,
        )
        reply_markup = build_trade_amount_buttons(
            offer.id,
            available_amounts,
            offer_public_id=getattr(offer, "offer_public_id", None),
        )

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=payload["message"],
                reply_markup=reply_markup,
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug(f"Failed to sync suggestion message chat={chat_id} message={message_id}: {exc}")
        if not available_amounts:
            await remove_trade_suggestion_record(offer_id, chat_id, message_id)


async def schedule_trade_suggestion_cleanup(
    bot: Bot,
    offer_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        await _enqueue_suggestion_markup_cleanup(
            chat_id=chat_id,
            message_id=message_id,
            source_id=(
                f"trade-suggestion-ttl:{offer_id}:{chat_id}:{message_id}"
            ),
            due_at=utc_now()
            + timedelta(seconds=TRADE_SUGGESTION_TTL_SECONDS + 0.2),
        )
        return

    async def _runner() -> None:
        await asyncio.sleep(TRADE_SUGGESTION_TTL_SECONDS + 0.2)
        records = await get_trade_suggestion_records(offer_id)
        for record in records:
            if int(record["chat_id"]) == chat_id and int(record["message_id"]) == message_id and float(record.get("expires_at", 0)) <= time.time():
                await _clear_suggestion_markup(
                    bot,
                    chat_id,
                    message_id,
                    source_id=(
                        f"trade-suggestion-ttl:{offer_id}:{chat_id}:{message_id}"
                    ),
                )
                await remove_trade_suggestion_record(offer_id, chat_id, message_id)
                break

    asyncio.create_task(_runner())


async def schedule_trade_suggestion_pending_reset(bot: Bot, offer_id: int) -> None:
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        due_at = utc_now() + timedelta(
            seconds=PRIVATE_SUGGESTION_CONFIRM_TIMEOUT + 0.2
        )

        await _enqueue_trade_suggestion_sync_operations(
            offer_id,
            due_at=due_at,
        )
        return

    async def _runner() -> None:
        await asyncio.sleep(PRIVATE_SUGGESTION_CONFIRM_TIMEOUT + 0.2)
        await sync_trade_suggestions_for_offer(bot, offer_id)

    asyncio.create_task(_runner())


async def listen_trade_suggestion_events(bot: Bot) -> None:
    redis_client = redis.Redis(connection_pool=pool)
    pubsub = redis_client.pubsub()
    channels = [
        "events:offer:updated",
        "events:offer:completed",
        "events:offer:expired",
        "events:offer:cancelled",
    ]
    await pubsub.subscribe(*channels)
    logger.info("🔔 Trade suggestion sync listener started")

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message or message.get("type") != "message":
                await asyncio.sleep(0.1)
                continue

            raw_data = message.get("data", "")
            data_str = raw_data.decode("utf-8") if isinstance(raw_data, bytes) else str(raw_data)
            try:
                payload = json.loads(data_str)
            except Exception:
                await asyncio.sleep(0.1)
                continue

            offer_id = payload.get("id") or payload.get("offer_id")
            if not offer_id:
                await asyncio.sleep(0.1)
                continue

            try:
                await sync_trade_suggestions_for_offer(bot, int(offer_id))
            except Exception as exc:
                logger.debug(f"Trade suggestion sync failed for offer={offer_id}: {exc}")
            raw_channel = message.get("channel", "")
            channel = raw_channel.decode("utf-8") if isinstance(raw_channel, bytes) else str(raw_channel)
            if channel == "events:offer:expired":
                try:
                    await refresh_repeat_offer_menu_for_expired_offer(bot, int(offer_id))
                except Exception as exc:
                    logger.debug(f"Repeat-offer menu refresh failed for offer={offer_id}: {exc}")
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        logger.info("🔕 Trade suggestion sync listener stopped")
        raise
    finally:
        await pubsub.unsubscribe(*channels)
        await pubsub.close()
        await redis_client.aclose()
