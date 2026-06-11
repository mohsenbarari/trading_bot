#!/usr/bin/env python3
"""Run the in-container Stage P7 trading-core benchmark helpers.

The worker intentionally uses synthetic users/offers/trades with a unique
prefix. It exercises the real router/service money path while replacing market
schedule, Telegram, and realtime network boundaries with local no-ops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import redis.asyncio as redis
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import delete, false, func, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.routers import offers as offers_router
from api.routers import realtime as realtime_router
from api.routers import trades as trades_router
from bot.handlers import trade_create as bot_trade_create
from bot.utils.offer_parser import parse_offer_text
from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.events import setup_event_listeners
from core.redis import pool
from core.services.accountant_relation_service import EffectiveOwnerActor
from core.utils import create_user_notification
from models.change_log import ChangeLog
from models.chat_member import ChatMember
from models.commodity import Commodity
from models.notification import Notification
from models.offer import Offer, OfferStatus
from models.trade import Trade
from models.user import User, UserRole


class TradingProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class FixtureUsers:
    seller_id: int
    responder_a_id: int
    responder_b_id: int

    @property
    def ids(self) -> list[int]:
        return [self.seller_id, self.responder_a_id, self.responder_b_id]


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_safe))


def percentile(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(float(item) for item in samples)
    index = max(0, min(len(ordered) - 1, math.ceil((p / 100.0) * len(ordered)) - 1))
    return round(ordered[index], 3)


def summarize_samples(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {
            "count": 0,
            "min_ms": None,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    normalized = [round(float(item), 3) for item in samples]
    return {
        "count": len(normalized),
        "min_ms": round(min(normalized), 3),
        "avg_ms": round(sum(normalized) / len(normalized), 3),
        "p50_ms": percentile(normalized, 50),
        "p95_ms": percentile(normalized, 95),
        "p99_ms": percentile(normalized, 99),
        "max_ms": round(max(normalized), 3),
    }


async def timed_ms(fn: Callable[[], Awaitable[Any]]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await fn()
    return result, round((time.perf_counter() - started) * 1000.0, 3)


def assert_race_acceptance(*, winner_count: int, trade_count: int, remaining_quantity: int | None, status: str | None) -> None:
    if winner_count != 1:
        raise TradingProbeError(f"race expected exactly one winner, got {winner_count}")
    if trade_count != 1:
        raise TradingProbeError(f"race expected exactly one persisted trade, got {trade_count}")
    if remaining_quantity != 0:
        raise TradingProbeError(f"race expected remaining_quantity=0, got {remaining_quantity}")
    if status != OfferStatus.COMPLETED.value:
        raise TradingProbeError(f"race expected offer status completed, got {status}")


async def cleanup_redis_for_user_ids(user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    deleted = 0
    client = redis.Redis(connection_pool=pool)
    try:
        keys: list[str] = []
        today = datetime.utcnow().strftime("%Y-%m-%d")
        for user_id in user_ids:
            keys.extend(
                [
                    f"user:{user_id}:active_offer_count",
                    f"user:{user_id}:unread_count",
                    f"daily_expire:{user_id}:{today}",
                ]
            )
            async for key in client.scan_iter(match=f"expire_rate:{user_id}:*"):
                keys.append(str(key))
        if keys:
            deleted = int(await client.delete(*keys) or 0)
    finally:
        await client.aclose()
    return deleted


async def cleanup_prefix(prefix: str) -> dict[str, Any]:
    pattern = f"{prefix}%"
    contains_pattern = f"%{prefix}%"
    async with AsyncSessionLocal() as db:
        user_ids = [
            int(item)
            for item in (
                await db.execute(select(User.id).where(User.account_name.like(pattern)))
            ).scalars().all()
        ]
        offer_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Offer.id).where(
                        (Offer.notes.like(contains_pattern))
                        | (Offer.idempotency_key.like(pattern))
                        | (Offer.user_id.in_(user_ids) if user_ids else false())
                    )
                )
            ).scalars().all()
        ]
        trade_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Trade.id).where(
                        (Trade.idempotency_key.like(pattern))
                        | (Trade.offer_id.in_(offer_ids) if offer_ids else false())
                        | (Trade.offer_user_id.in_(user_ids) if user_ids else false())
                        | (Trade.responder_user_id.in_(user_ids) if user_ids else false())
                    )
                )
            ).scalars().all()
        ]
        notification_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(Notification.id).where(
                        (Notification.user_id.in_(user_ids) if user_ids else false())
                        | Notification.message.like(contains_pattern)
                    )
                )
            ).scalars().all()
        ]
        chat_member_ids = [
            int(item)
            for item in (
                await db.execute(
                    select(ChatMember.id).where(ChatMember.user_id.in_(user_ids) if user_ids else false())
                )
            ).scalars().all()
        ]

        deleted_notifications = 0
        deleted_chat_members = 0
        deleted_trades = 0
        deleted_offers = 0
        deleted_users = 0
        if notification_ids:
            deleted_notifications = int(
                (await db.execute(delete(Notification).where(Notification.id.in_(notification_ids)))).rowcount or 0
            )
        if chat_member_ids:
            deleted_chat_members = int(
                (await db.execute(delete(ChatMember).where(ChatMember.id.in_(chat_member_ids)))).rowcount or 0
            )
        if trade_ids:
            deleted_trades = int((await db.execute(delete(Trade).where(Trade.id.in_(trade_ids)))).rowcount or 0)
        if offer_ids:
            deleted_offers = int((await db.execute(delete(Offer).where(Offer.id.in_(offer_ids)))).rowcount or 0)
        if user_ids:
            deleted_users = int((await db.execute(delete(User).where(User.id.in_(user_ids)))).rowcount or 0)

        change_log_result = await db.execute(
            text(
                """
                DELETE FROM change_log
                WHERE data::text LIKE :pattern
                   OR (table_name = 'users' AND record_id = ANY(:user_ids))
                   OR (table_name = 'offers' AND record_id = ANY(:offer_ids))
                   OR (table_name = 'trades' AND record_id = ANY(:trade_ids))
                   OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                   OR (table_name = 'chat_members' AND record_id = ANY(:chat_member_ids))
                """
            ),
            {
                "pattern": contains_pattern,
                "user_ids": user_ids or [-1],
                "offer_ids": offer_ids or [-1],
                "trade_ids": trade_ids or [-1],
                "notification_ids": notification_ids or [-1],
                "chat_member_ids": chat_member_ids or [-1],
            },
        )
        await db.commit()

    deleted_redis_keys = await cleanup_redis_for_user_ids(user_ids)
    return {
        "status": "ok",
        "prefix": prefix,
        "deleted_users": deleted_users,
        "deleted_chat_members": deleted_chat_members,
        "deleted_offers": deleted_offers,
        "deleted_trades": deleted_trades,
        "deleted_notifications": deleted_notifications,
        "deleted_change_logs": int(change_log_result.rowcount or 0),
        "deleted_redis_keys": deleted_redis_keys,
    }


async def resolve_commodity() -> tuple[int, str]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Commodity.id, Commodity.name).order_by(Commodity.id.asc()).limit(1))
        row = result.first()
    if row is None:
        raise TradingProbeError("No commodity is available for trading benchmark")
    return int(row.id), str(row.name)


async def create_fixture_users(prefix: str) -> FixtureUsers:
    setup_event_listeners()
    users = [
        User(
            account_name=f"{prefix}seller",
            mobile_number=f"09991{abs(hash(prefix + 'seller')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Seller",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
        User(
            account_name=f"{prefix}responder_a",
            mobile_number=f"09992{abs(hash(prefix + 'responder_a')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Responder A",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
        User(
            account_name=f"{prefix}responder_b",
            mobile_number=f"09993{abs(hash(prefix + 'responder_b')) % 1000000:06d}",
            telegram_id=None,
            username=None,
            full_name="P7 Benchmark Responder B",
            address="P7 benchmark synthetic user",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            has_bot_access=True,
            home_server=settings.server_mode,
            max_sessions=1,
            max_accountants=0,
            max_customers=0,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
        ),
    ]
    async with AsyncSessionLocal() as db:
        db.add_all(users)
        await db.commit()
        for user in users:
            await db.refresh(user)
        return FixtureUsers(seller_id=users[0].id, responder_a_id=users[1].id, responder_b_id=users[2].id)


async def fake_market_open(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, next_transition_at=None)


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_send_offer(*_args: Any, **_kwargs: Any) -> None:
    return None


async def noop_register_market_offer_created(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(is_open=True, offers_since_last_open=0)


async def noop_update_channel_buttons(*_args: Any, **_kwargs: Any) -> bool:
    return True


@asynccontextmanager
async def patched_trading_boundaries():
    import core.services.trade_service as trade_service

    original = {
        "offers_evaluate": offers_router.evaluate_current_market_schedule,
        "trades_evaluate": trades_router.evaluate_current_market_schedule,
        "send_offer": offers_router.send_offer_to_channel,
        "register_market_offer_created": offers_router.register_market_offer_created,
        "update_channel_buttons": trades_router.update_channel_buttons,
        "send_telegram_message_sync": trades_router.send_telegram_message_sync,
        "realtime_publish": realtime_router.publish_event,
        "realtime_publish_user": realtime_router.publish_user_event,
        "bot_market_open": bot_trade_create._bot_market_is_open,
        "validate_competitive_price": trade_service.validate_competitive_price,
        "detect_offer_price_warning": trade_service.detect_offer_price_warning,
    }

    async def always_valid_price(**_kwargs: Any) -> tuple[bool, str]:
        return True, ""

    async def no_price_warning(**_kwargs: Any) -> None:
        return None

    offers_router.evaluate_current_market_schedule = fake_market_open
    trades_router.evaluate_current_market_schedule = fake_market_open
    offers_router.send_offer_to_channel = noop_send_offer
    offers_router.register_market_offer_created = noop_register_market_offer_created
    trades_router.update_channel_buttons = noop_update_channel_buttons
    trades_router.send_telegram_message_sync = lambda *_args, **_kwargs: None
    realtime_router.publish_event = noop_async
    realtime_router.publish_user_event = noop_async
    bot_trade_create._bot_market_is_open = lambda: asyncio.sleep(0, result=True)
    trade_service.validate_competitive_price = always_valid_price
    trade_service.detect_offer_price_warning = no_price_warning
    try:
        yield
    finally:
        offers_router.evaluate_current_market_schedule = original["offers_evaluate"]
        trades_router.evaluate_current_market_schedule = original["trades_evaluate"]
        offers_router.send_offer_to_channel = original["send_offer"]
        offers_router.register_market_offer_created = original["register_market_offer_created"]
        trades_router.update_channel_buttons = original["update_channel_buttons"]
        trades_router.send_telegram_message_sync = original["send_telegram_message_sync"]
        realtime_router.publish_event = original["realtime_publish"]
        realtime_router.publish_user_event = original["realtime_publish_user"]
        bot_trade_create._bot_market_is_open = original["bot_market_open"]
        trade_service.validate_competitive_price = original["validate_competitive_price"]
        trade_service.detect_offer_price_warning = original["detect_offer_price_warning"]


def owner_context(user: User) -> EffectiveOwnerActor:
    return EffectiveOwnerActor(owner_user=user, actor_user=user, relation=None, is_accountant_context=False)


async def load_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise TradingProbeError(f"synthetic user {user_id} disappeared")
    return user


async def create_offer_for_user(
    *,
    user_id: int,
    commodity_id: int,
    prefix: str,
    index: int,
    offer_type: str = "sell",
    quantity: int = 5,
    price: int = 100000,
) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        response = await offers_router.create_offer(
            offers_router.OfferCreate(
                offer_type=offer_type,
                commodity_id=commodity_id,
                quantity=quantity,
                price=price,
                is_wholesale=True,
                lot_sizes=None,
                notes=f"{prefix} offer {index}",
                warning_acknowledged=True,
            ),
            db=db,
            current_user=user,
            context=owner_context(user),
        )
        if not hasattr(response, "id"):
            raise TradingProbeError(f"create_offer returned unexpected response: {response!r}")
        return int(response.id)


async def expire_offer_for_user(*, user_id: int, offer_id: int) -> None:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        await offers_router.expire_offer(offer_id, db=db, context=owner_context(user))


async def list_active_offers_for_user(*, user_id: int, limit: int = 30) -> int:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        offers = await offers_router.get_active_offers(
            offer_type=None,
            commodity_id=None,
            skip=0,
            limit=limit,
            db=db,
            current_user=user,
            context=owner_context(user),
        )
        return len(offers)


async def execute_trade_for_user(
    *,
    user_id: int,
    offer_id: int,
    quantity: int,
    idempotency_key: str,
) -> Any:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        return await trades_router._execute_trade_authoritatively(
            trade_data=trades_router.TradeCreate(
                offer_id=offer_id,
                quantity=quantity,
                idempotency_key=idempotency_key,
            ),
            background_tasks=BackgroundTasks(),
            db=db,
            context=owner_context(user),
            edge_received_at=datetime.utcnow(),
        )


class FakeState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.state: Any = None

    async def get_state(self) -> Any:
        return self.state

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def set_state(self, state: Any) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


class FakeMessage:
    def __init__(self, text_value: str) -> None:
        self.text = text_value
        self.answers: list[dict[str, Any]] = []

    async def answer(self, text_value: str, **kwargs: Any) -> None:
        self.answers.append({"text": text_value, "kwargs": kwargs})


async def run_bot_text_handler_probe(*, user_id: int, text_value: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await load_user(db, user_id)
        state = FakeState()
        message = FakeMessage(text_value)
        await bot_trade_create.handle_text_offer(message, state, user, bot=None)
        return {
            "answers": len(message.answers),
            "state_set": state.state is not None,
            "parsed_trade_type": state.data.get("trade_type"),
            "parsed_commodity_id": state.data.get("commodity_id"),
        }


async def run_notification_fanout(*, user_ids: list[int], prefix: str, iterations: int) -> dict[str, Any]:
    samples: list[float] = []
    created = 0
    for index in range(iterations):
        async def _create_batch() -> int:
            async with AsyncSessionLocal() as db:
                count = 0
                for user_id in user_ids:
                    await create_user_notification(
                        db,
                        user_id,
                        f"{prefix} notification fanout {index}",
                        level=NotificationLevel.SUCCESS,
                        category=NotificationCategory.TRADE,
                        extra_payload={"benchmark": "p7", "prefix": prefix},
                    )
                    count += 1
                return count

        count, duration_ms = await timed_ms(_create_batch)
        created += int(count)
        samples.append(duration_ms)
    return {"created": created, "latency": summarize_samples(samples)}


async def run_race_probe(
    *,
    prefix: str,
    fixture: FixtureUsers,
    commodity_id: int,
    concurrency: int,
) -> dict[str, Any]:
    offer_id = await create_offer_for_user(
        user_id=fixture.seller_id,
        commodity_id=commodity_id,
        prefix=prefix,
        index=900,
        offer_type="sell",
        quantity=5,
        price=100000,
    )

    async def _attempt(index: int) -> dict[str, Any]:
        responder_id = fixture.responder_a_id if index % 2 == 0 else fixture.responder_b_id
        started = time.perf_counter()
        try:
            response = await execute_trade_for_user(
                user_id=responder_id,
                offer_id=offer_id,
                quantity=5,
                idempotency_key=f"{prefix}race-{index}",
            )
            return {
                "index": index,
                "status": "success",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "trade_id": getattr(response, "id", None),
            }
        except HTTPException as exc:
            return {
                "index": index,
                "status": "rejected",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "status_code": exc.status_code,
            }
        except Exception as exc:
            return {
                "index": index,
                "status": "error",
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "error_type": type(exc).__name__,
            }

    attempts = await asyncio.gather(*[_attempt(index) for index in range(concurrency)])
    winner_count = sum(1 for item in attempts if item["status"] == "success")
    async with AsyncSessionLocal() as db:
        trade_count = int(await db.scalar(select(func.count(Trade.id)).where(Trade.offer_id == offer_id)) or 0)
        offer = await db.get(Offer, offer_id)
        remaining_quantity = int(offer.remaining_quantity or 0) if offer else None
        status_value = getattr(getattr(offer, "status", None), "value", None)

    assert_race_acceptance(
        winner_count=winner_count,
        trade_count=trade_count,
        remaining_quantity=remaining_quantity,
        status=status_value,
    )
    return {
        "offer_id": offer_id,
        "attempts": attempts,
        "winner_count": winner_count,
        "persisted_trade_count": trade_count,
        "offer_remaining_quantity": remaining_quantity,
        "offer_status": status_value,
        "latency": summarize_samples([float(item["duration_ms"]) for item in attempts]),
    }


async def run_benchmark(args: argparse.Namespace) -> int:
    setup_event_listeners()
    prefix = args.prefix
    await cleanup_prefix(prefix)
    commodity_id, commodity_name = await resolve_commodity()
    async with patched_trading_boundaries():
        fixture = await create_fixture_users(prefix)
        parse_samples: list[float] = []
        bot_samples: list[float] = []
        create_samples: list[float] = []
        expire_samples: list[float] = []
        list_samples: list[float] = []
        trade_samples: list[float] = []
        created_offer_ids: list[int] = []

        parse_text = f"ف {commodity_name} {args.quantity} عدد {args.price}: {prefix} parser"
        for _ in range(args.parse_iterations):
            async def _parse() -> Any:
                return await parse_offer_text(parse_text)

            (parsed, error), duration_ms = await timed_ms(_parse)
            if error or parsed is None:
                raise TradingProbeError(f"parser failed: {getattr(error, 'message', None)}")
            parse_samples.append(duration_ms)

        for _ in range(args.bot_iterations):
            result, duration_ms = await timed_ms(
                lambda: run_bot_text_handler_probe(user_id=fixture.seller_id, text_value=parse_text)
            )
            if not result["state_set"]:
                raise TradingProbeError(f"bot text handler did not set confirmation state: {result}")
            bot_samples.append(duration_ms)

        for index in range(args.create_iterations):
            offer_id, duration_ms = await timed_ms(
                lambda index=index: create_offer_for_user(
                    user_id=fixture.seller_id,
                    commodity_id=commodity_id,
                    prefix=prefix,
                    index=index,
                    offer_type="sell",
                    quantity=args.quantity,
                    price=args.price,
                )
            )
            created_offer_ids.append(int(offer_id))
            create_samples.append(duration_ms)

        for _ in range(args.list_iterations):
            _count, duration_ms = await timed_ms(lambda: list_active_offers_for_user(user_id=fixture.responder_a_id))
            list_samples.append(duration_ms)

        for offer_id in created_offer_ids[: args.expire_iterations]:
            _result, duration_ms = await timed_ms(
                lambda offer_id=offer_id: expire_offer_for_user(user_id=fixture.seller_id, offer_id=offer_id)
            )
            expire_samples.append(duration_ms)

        for index in range(args.trade_iterations):
            trade_offer_id = await create_offer_for_user(
                user_id=fixture.seller_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=800 + index,
                offer_type="sell",
                quantity=args.quantity,
                price=args.price,
            )
            _trade_response, trade_duration = await timed_ms(
                lambda index=index, trade_offer_id=trade_offer_id: execute_trade_for_user(
                    user_id=fixture.responder_a_id,
                    offer_id=trade_offer_id,
                    quantity=args.quantity,
                    idempotency_key=f"{prefix}single-trade-{index}",
                )
            )
            trade_samples.append(trade_duration)

        notification_report = await run_notification_fanout(
            user_ids=fixture.ids,
            prefix=prefix,
            iterations=args.notification_iterations,
        )
        race_report = await run_race_probe(
            prefix=prefix,
            fixture=fixture,
            commodity_id=commodity_id,
            concurrency=args.race_concurrency,
        )

    async with AsyncSessionLocal() as db:
        synthetic_notification_count = int(
            await db.scalar(select(func.count(Notification.id)).where(Notification.user_id.in_(fixture.ids))) or 0
        )
        synthetic_trade_count = int(
            await db.scalar(
                select(func.count(Trade.id)).where(
                    (Trade.offer_user_id.in_(fixture.ids))
                    | (Trade.responder_user_id.in_(fixture.ids))
                    | (Trade.actor_user_id.in_(fixture.ids))
                )
            )
            or 0
        )
        synthetic_offer_count = int(
            await db.scalar(select(func.count(Offer.id)).where(Offer.user_id.in_(fixture.ids))) or 0
        )
        unsynced_count = int(await db.scalar(select(func.count(ChangeLog.id)).where(ChangeLog.synced == False)) or 0)

    payload = {
        "status": "ok",
        "server_mode": settings.server_mode,
        "prefix": prefix,
        "commodity_id": commodity_id,
        "commodity_name": commodity_name,
        "fixture_user_ids": fixture.ids,
        "operations": {
            "bot_text_offer_handler": summarize_samples(bot_samples),
            "parse_offer": summarize_samples(parse_samples),
            "offer_create": summarize_samples(create_samples),
            "offer_list": summarize_samples(list_samples),
            "offer_expire": summarize_samples(expire_samples),
            "trade_execute": summarize_samples(trade_samples),
            "notification_fanout": notification_report["latency"],
        },
        "notification_fanout": notification_report,
        "race": race_report,
        "synthetic_counts_before_cleanup": {
            "offers": synthetic_offer_count,
            "trades": synthetic_trade_count,
            "notifications": synthetic_notification_count,
            "unsynced_change_logs": unsynced_count,
        },
    }
    print_json(payload)
    return 0


async def cleanup_command(args: argparse.Namespace) -> int:
    print_json(await cleanup_prefix(args.prefix))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Stage P7 trading benchmark helpers inside an app container.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--prefix", required=True)

    bench_parser = subparsers.add_parser("run-benchmark")
    bench_parser.add_argument("--prefix", required=True)
    bench_parser.add_argument("--parse-iterations", type=int, default=30)
    bench_parser.add_argument("--bot-iterations", type=int, default=5)
    bench_parser.add_argument("--create-iterations", type=int, default=4)
    bench_parser.add_argument("--list-iterations", type=int, default=6)
    bench_parser.add_argument("--expire-iterations", type=int, default=2)
    bench_parser.add_argument("--trade-iterations", type=int, default=3)
    bench_parser.add_argument("--notification-iterations", type=int, default=4)
    bench_parser.add_argument("--race-concurrency", type=int, default=4)
    bench_parser.add_argument("--quantity", type=int, default=5)
    bench_parser.add_argument("--price", type=int, default=100000)

    return parser


async def dispatch(args: argparse.Namespace) -> int:
    if args.command == "cleanup":
        return await cleanup_command(args)
    if args.command == "run-benchmark":
        return await run_benchmark(args)
    raise TradingProbeError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(dispatch(args))
    except Exception as exc:
        print_json({"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
