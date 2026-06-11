#!/usr/bin/env python3
"""Prepare and clean isolated data for the Stage P8 frontend UX benchmark.

The worker runs inside the app container. It creates a synthetic owner/admin
profile plus bounded customers, accountants, coworkers, offers, trades, and
notifications so the browser benchmark can exercise non-messenger pages without
depending on the current production data shape. Rows are identified by one
unique prefix and are removed by the cleanup command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from sqlalchemy import delete, func, or_, select, text


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db import AsyncSessionLocal
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.redis import pool
from core.security import create_access_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.chat_member import ChatMember
from models.commodity import Commodity
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.notification import Notification
from models.offer import Offer, OfferStatus, OfferType
from models.session import SessionLoginRequest, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.user import User, UserRole


class FrontendProbeError(RuntimeError):
    pass


def json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def relation_token(prefix: str, kind: str, index: int) -> str:
    return f"{prefix}{kind}_{index:03d}"


async def collect_prefixed_user_ids(db, prefix: str) -> list[int]:
    rows = await db.execute(select(User.id).where(User.account_name.like(f"{prefix}%")))
    return [int(value) for value in rows.scalars().all()]


async def cleanup(prefix: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user_ids = await collect_prefixed_user_ids(db, prefix)
        deleted: dict[str, int] = {}
        prefix_like = f"%{prefix}%"

        offer_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Offer.id).where(
                        or_(
                            Offer.idempotency_key.like(f"{prefix}%"),
                            Offer.notes.like(f"{prefix}%"),
                            Offer.user_id.in_(user_ids or [-1]),
                            Offer.actor_user_id.in_(user_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        trade_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Trade.id).where(
                        or_(
                            Trade.idempotency_key.like(f"{prefix}%"),
                            Trade.offer_id.in_(offer_ids or [-1]),
                            Trade.offer_user_id.in_(user_ids or [-1]),
                            Trade.responder_user_id.in_(user_ids or [-1]),
                            Trade.actor_user_id.in_(user_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        relation_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(AccountantRelation.id).where(
                        or_(
                            AccountantRelation.invitation_token.like(f"{prefix}%"),
                            AccountantRelation.global_account_name.like(f"{prefix}%"),
                            AccountantRelation.owner_user_id.in_(user_ids or [-1]),
                            AccountantRelation.accountant_user_id.in_(user_ids or [-1]),
                            AccountantRelation.created_by_user_id.in_(user_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        customer_relation_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(CustomerRelation.id).where(
                        or_(
                            CustomerRelation.invitation_token.like(f"{prefix}%"),
                            CustomerRelation.management_name.like(f"{prefix}%"),
                            CustomerRelation.owner_user_id.in_(user_ids or [-1]),
                            CustomerRelation.customer_user_id.in_(user_ids or [-1]),
                            CustomerRelation.created_by_user_id.in_(user_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        notification_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Notification.id).where(
                        or_(
                            Notification.user_id.in_(user_ids or [-1]),
                            Notification.message.like(prefix_like),
                        )
                    )
                )
            ).scalars().all()
        ]

        change_log_result = await db.execute(
            text(
                """
                DELETE FROM change_log
                WHERE
                    (table_name = 'trades' AND record_id = ANY(:trade_ids))
                    OR (table_name = 'offers' AND record_id = ANY(:offer_ids))
                    OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                    OR (table_name = 'accountant_relations' AND record_id = ANY(:accountant_relation_ids))
                    OR (table_name = 'customer_relations' AND record_id = ANY(:customer_relation_ids))
                    OR (table_name = 'users' AND record_id = ANY(:user_ids))
                    OR (data::text LIKE :prefix_like)
                """
            ),
            {
                "trade_ids": trade_ids or [-1],
                "offer_ids": offer_ids or [-1],
                "notification_ids": notification_ids or [-1],
                "accountant_relation_ids": relation_ids or [-1],
                "customer_relation_ids": customer_relation_ids or [-1],
                "user_ids": user_ids or [-1],
                "prefix_like": prefix_like,
            },
        )
        deleted["change_logs"] = int(change_log_result.rowcount or 0)

        if user_ids:
            deleted["chat_members"] = int(
                (await db.execute(delete(ChatMember).where(ChatMember.user_id.in_(user_ids)))).rowcount or 0
            )
            deleted["notifications"] = int(
                (await db.execute(delete(Notification).where(Notification.user_id.in_(user_ids)))).rowcount or 0
            )
            deleted["session_login_requests"] = int(
                (await db.execute(delete(SessionLoginRequest).where(SessionLoginRequest.user_id.in_(user_ids)))).rowcount or 0
            )
            deleted["user_sessions"] = int(
                (await db.execute(delete(UserSession).where(UserSession.user_id.in_(user_ids)))).rowcount or 0
            )
        if trade_ids:
            deleted["trades"] = int((await db.execute(delete(Trade).where(Trade.id.in_(trade_ids)))).rowcount or 0)
        if offer_ids:
            deleted["offers"] = int((await db.execute(delete(Offer).where(Offer.id.in_(offer_ids)))).rowcount or 0)
        if relation_ids:
            deleted["accountant_relations"] = int(
                (await db.execute(delete(AccountantRelation).where(AccountantRelation.id.in_(relation_ids)))).rowcount or 0
            )
        if customer_relation_ids:
            deleted["customer_relations"] = int(
                (await db.execute(delete(CustomerRelation).where(CustomerRelation.id.in_(customer_relation_ids)))).rowcount or 0
            )
        if user_ids:
            deleted["users"] = int((await db.execute(delete(User).where(User.id.in_(user_ids)))).rowcount or 0)

        await db.commit()

    redis_deleted = await cleanup_sync_queue_entries(prefix)

    return {
        "status": "ok",
        "prefix": prefix,
        "user_ids": len(user_ids),
        "deleted": deleted,
        "redis_deleted": redis_deleted,
    }


async def cleanup_sync_queue_entries(prefix: str) -> dict[str, int]:
    deleted: dict[str, int] = {}
    client = redis.Redis(connection_pool=pool)
    try:
        for key in ("sync:outbound", "sync:retry"):
            removed = 0
            items = await client.lrange(key, 0, -1)
            for item in items:
                if prefix in str(item):
                    removed += int(await client.lrem(key, 0, item) or 0)
            deleted[key] = removed
    finally:
        await client.aclose()
    return deleted


def make_user(prefix: str, index: int, *, role: UserRole, label: str) -> User:
    mobile = f"0998{index:07d}"[-11:]
    return User(
        account_name=f"{prefix}{label}_{index:03d}",
        mobile_number=mobile,
        telegram_id=None,
        username=f"{prefix}{label}_{index:03d}",
        full_name=f"کاربر تست {label} {index}",
        address=f"آدرس تست P8 شماره {index}",
        role=role,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        has_bot_access=True,
        must_change_password=False,
        max_sessions=3,
        max_accountants=200,
        max_customers=200,
        can_block_users=True,
        max_blocked_users=100,
        home_server="iran",
    )


async def prepare(prefix: str, *, coworkers: int, accountants: int, customers: int, offers: int, trades: int, notifications: int) -> dict[str, Any]:
    await cleanup(prefix)
    async with AsyncSessionLocal() as db:
        owner = make_user(prefix, 1, role=UserRole.SUPER_ADMIN, label="owner")
        public_target = make_user(prefix, 2, role=UserRole.STANDARD, label="public")
        db.add_all([owner, public_target])
        await db.flush()

        coworker_rows = [make_user(prefix, 100 + index, role=UserRole.STANDARD, label="coworker") for index in range(coworkers)]
        accountant_rows = [make_user(prefix, 300 + index, role=UserRole.STANDARD, label="accountant") for index in range(accountants)]
        customer_rows = [make_user(prefix, 500 + index, role=UserRole.STANDARD, label="customer") for index in range(customers)]
        db.add_all(coworker_rows + accountant_rows + customer_rows)
        await db.flush()

        expiry = now_utc() + timedelta(days=14)
        for index, accountant in enumerate(accountant_rows):
            db.add(
                AccountantRelation(
                    owner_user_id=owner.id,
                    accountant_user_id=accountant.id,
                    created_by_user_id=owner.id,
                    invitation_token=relation_token(prefix, "acct", index),
                    global_account_name=accountant.account_name,
                    relation_display_name=f"حسابدار تست {index + 1}",
                    duty_description=f"شرح وظیفه حسابدار تست {index + 1}",
                    mobile_number=accountant.mobile_number,
                    status=AccountantRelationStatus.ACTIVE,
                    expires_at=expiry,
                    activated_at=now_utc(),
                )
            )

        for index, customer in enumerate(customer_rows):
            db.add(
                CustomerRelation(
                    owner_user_id=owner.id,
                    customer_user_id=customer.id,
                    created_by_user_id=owner.id,
                    invitation_token=relation_token(prefix, "cust", index),
                    management_name=f"مشتری تست {index + 1}",
                    customer_tier=CustomerTier.TIER_1 if index % 2 == 0 else CustomerTier.TIER_2,
                    status=CustomerRelationStatus.ACTIVE,
                    activated_at=now_utc(),
                )
            )

        commodity = (await db.execute(select(Commodity).order_by(Commodity.id.asc()).limit(1))).scalar_one_or_none()
        created_offer_ids: list[int] = []
        if commodity is not None:
            offer_users = coworker_rows[: max(1, min(len(coworker_rows), offers))]
            for index, user in enumerate(offer_users):
                offer = Offer(
                    user_id=user.id,
                    actor_user_id=user.id,
                    home_server="iran",
                    offer_type=OfferType.BUY if index % 2 == 0 else OfferType.SELL,
                    commodity_id=commodity.id,
                    quantity=50 + index,
                    remaining_quantity=50 + index,
                    price=100000 + (index * 100),
                    is_wholesale=True,
                    lot_sizes=None,
                    original_lot_sizes=None,
                    status=OfferStatus.ACTIVE,
                    notes=f"{prefix}market_offer_{index}",
                    idempotency_key=f"{prefix}offer_{index:03d}",
                )
                db.add(offer)
                await db.flush()
                created_offer_ids.append(int(offer.id))

            max_trade_number = int(await db.scalar(select(func.max(Trade.trade_number))) or 9999)
            for index in range(trades):
                counterparty = coworker_rows[index % max(1, len(coworker_rows))]
                offer = Offer(
                    user_id=counterparty.id,
                    actor_user_id=counterparty.id,
                    home_server="iran",
                    offer_type=OfferType.SELL,
                    commodity_id=commodity.id,
                    quantity=10 + index,
                    remaining_quantity=0,
                    price=110000 + (index * 100),
                    is_wholesale=True,
                    status=OfferStatus.COMPLETED,
                    notes=f"{prefix}history_offer_{index}",
                    idempotency_key=f"{prefix}history_offer_{index:03d}",
                )
                db.add(offer)
                await db.flush()
                created_offer_ids.append(int(offer.id))
                db.add(
                    Trade(
                        trade_number=max_trade_number + index + 1,
                        offer_id=offer.id,
                        offer_user_id=counterparty.id,
                        offer_user_mobile=counterparty.mobile_number,
                        responder_user_id=owner.id,
                        responder_user_mobile=owner.mobile_number,
                        actor_user_id=owner.id,
                        commodity_id=commodity.id,
                        trade_type=TradeType.BUY,
                        quantity=10 + index,
                        price=offer.price,
                        status=TradeStatus.COMPLETED,
                        note=f"{prefix}history_trade_{index}",
                        idempotency_key=f"{prefix}trade_{index:03d}",
                        confirmed_at=now_utc(),
                        completed_at=now_utc(),
                    )
                )

        for index in range(notifications):
            db.add(
                Notification(
                    user_id=owner.id,
                    message=f"اعلان تست P8 {index + 1}\nشناسه: {prefix}{index}",
                    is_read=index % 2 == 0,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
            )

        owner_id = int(owner.id)
        owner_account_name = owner.account_name
        owner_full_name = owner.full_name
        owner_role = owner.role.value
        owner_account_status = owner.account_status.value
        public_target_id = int(public_target.id)
        public_target_account_name = public_target.account_name
        customer_target_id = int(customer_rows[0].id) if customer_rows else None
        customer_target_account_name = customer_rows[0].account_name if customer_rows else None
        current_user_summary = {
            "id": owner_id,
            "role": owner_role,
            "full_name": owner_full_name,
            "account_name": owner_account_name,
            "account_status": owner_account_status,
            "is_accountant": False,
            "is_customer": False,
        }
        await db.commit()

        token = create_access_token(subject=owner_id, expires_delta=timedelta(minutes=45))

    return {
        "status": "ok",
        "prefix": prefix,
        "token": token,
        "current_user_summary": current_user_summary,
        "owner": {
            "id": current_user_summary["id"],
            "account_name": current_user_summary["account_name"],
        },
        "public_profile": {
            "id": public_target_id,
            "account_name": public_target_account_name,
        },
        "customer_profile": {
            "id": customer_target_id,
            "account_name": customer_target_account_name,
        },
        "expected": {
            "coworkers": coworkers,
            "accountants": accountants,
            "customers": customers,
            "offers": len(created_offer_ids),
            "trades": trades if commodity is not None else 0,
            "notifications": notifications,
            "project_users_page_size": 25,
        },
        "commodity_available": commodity is not None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage P8 frontend UX synthetic data helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--prefix", required=True)
    prepare_parser.add_argument("--coworkers", type=int, default=60)
    prepare_parser.add_argument("--accountants", type=int, default=12)
    prepare_parser.add_argument("--customers", type=int, default=24)
    prepare_parser.add_argument("--offers", type=int, default=18)
    prepare_parser.add_argument("--trades", type=int, default=8)
    prepare_parser.add_argument("--notifications", type=int, default=12)

    cleanup_parser = sub.add_parser("cleanup")
    cleanup_parser.add_argument("--prefix", required=True)
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        json_dump(
            await prepare(
                args.prefix,
                coworkers=max(0, args.coworkers),
                accountants=max(0, args.accountants),
                customers=max(0, args.customers),
                offers=max(0, args.offers),
                trades=max(0, args.trades),
                notifications=max(0, args.notifications),
            )
        )
        return 0
    if args.command == "cleanup":
        json_dump(await cleanup(args.prefix))
        return 0
    raise FrontendProbeError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
