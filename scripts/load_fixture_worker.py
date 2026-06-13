#!/usr/bin/env python3
"""Prepare and clean Stage L realistic-load synthetic fixtures.

This worker is intended to run inside the app container. It never touches real
users: every row is addressed by an explicit run prefix and cleanup only removes
matching synthetic rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from sqlalchemy import delete, false, func, or_, select, text, update

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings
from core.db import AsyncSessionLocal
from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType, NotificationCategory, NotificationLevel, UserAccountStatus
from core.events import setup_event_listeners
from core.redis import pool
from core.security import create_access_token
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.chat import Chat
from models.chat_file import ChatFile
from models.chat_member import ChatMember
from models.commodity import Commodity
from models.conversation import Conversation
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.message import Message
from models.notification import Notification
from models.offer import Offer, OfferStatus, OfferType
from models.session import SessionLoginRequest, SingleSessionRecoveryAdminTarget, SingleSessionRecoveryRequest, UserSession
from models.trade import Trade, TradeStatus, TradeType
from models.upload_session import UploadBatch, UploadSession
from models.user import User, UserRole


class LoadFixtureError(RuntimeError):
    pass


SEQUENCE_ALIGNMENT_TABLES = (
    "users",
    "accountant_relations",
    "customer_relations",
    "chats",
    "chat_members",
    "messages",
    "conversations",
    "offers",
    "trades",
    "notifications",
)
SEQUENCE_PREPARE_SAFETY_GAP = 10_000


@dataclass(frozen=True)
class FixturePlan:
    normal_users: int = 80
    middle_admins: int = 8
    accountants: int = 24
    customers: int = 48
    groups: int = 12
    channels: int = 3
    offers: int = 60
    trades: int = 20
    notifications: int = 40
    direct_pairs: int = 80
    token_minutes: int = 240


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def mobile_for(prefix: str, index: int) -> str:
    """Return a deterministic synthetic Iranian mobile number for a run prefix."""
    prefix_bucket = zlib.crc32(prefix.encode("utf-8")) % 10000
    return f"09{prefix_bucket:04d}{index:05d}"


def make_account_name(prefix: str, kind: str, index: int) -> str:
    return f"{prefix}{kind}_{index:03d}"


def make_user(prefix: str, kind: str, index: int, *, role: UserRole) -> User:
    account_name = make_account_name(prefix, kind, index)
    return User(
        account_name=account_name,
        mobile_number=mobile_for(prefix, index),
        telegram_id=None,
        username=account_name,
        full_name=f"لودتست {kind} {index}",
        address=f"نشانی synthetic load fixture {prefix}{index}",
        role=role,
        account_status=UserAccountStatus.ACTIVE,
        is_deleted=False,
        has_bot_access=True,
        must_change_password=False,
        max_sessions=20,
        max_accountants=200,
        max_customers=300,
        can_block_users=True,
        max_blocked_users=300,
        home_server=settings.server_mode,
    )


def token_entry(user: User, *, persona: str, expires_delta: timedelta) -> dict[str, Any]:
    return {
        "user_id": int(user.id),
        "account_name": str(user.account_name),
        "role": getattr(user.role, "value", str(user.role)),
        "persona": persona,
        "token_type": "bearer",
        "access_token": create_access_token(subject=int(user.id), expires_delta=expires_delta, server_id=settings.server_mode),
    }


async def collect_prefixed_user_ids(db, prefix: str) -> list[int]:
    rows = await db.execute(select(User.id).where(User.account_name.like(f"{prefix}%")))
    return [int(value) for value in rows.scalars().all()]


async def collect_prefixed_chat_ids(db, prefix: str, user_ids: list[int]) -> list[int]:
    member_chat_ids = []
    if user_ids:
        member_chat_ids = [
            int(value)
            for value in (
                await db.execute(select(ChatMember.chat_id).where(ChatMember.user_id.in_(user_ids)))
            ).scalars().all()
        ]
    rows = await db.execute(
        select(Chat.id).where(
            or_(
                Chat.title.like(f"{prefix}%"),
                Chat.created_by_id.in_(user_ids or [-1]),
                Chat.id.in_(member_chat_ids or [-1]),
            )
        )
    )
    return sorted({int(value) for value in rows.scalars().all()})


def normalize_record_ids(record_ids_by_table: dict[str, list[int]] | None) -> dict[str, set[int]]:
    normalized: dict[str, set[int]] = {}
    for table_name, raw_ids in (record_ids_by_table or {}).items():
        ids: set[int] = set()
        for raw_id in raw_ids:
            try:
                ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue
        if ids:
            normalized[table_name] = ids
    return normalized


def sync_queue_item_matches_cleanup(item: Any, prefix: str, record_ids_by_table: dict[str, set[int]]) -> bool:
    raw_item = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
    if prefix in raw_item:
        return True
    if not record_ids_by_table:
        return False
    try:
        payload = json.loads(raw_item)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    table_name = str(payload.get("table") or "")
    candidate_ids = record_ids_by_table.get(table_name)
    if not candidate_ids:
        return False
    for key in ("id", "record_id"):
        try:
            if int(payload.get(key)) in candidate_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def cleanup_sync_queue_entries(prefix: str, record_ids_by_table: dict[str, list[int]] | None = None) -> dict[str, int]:
    deleted: dict[str, int] = {}
    cleanup_ids = normalize_record_ids(record_ids_by_table)
    client = redis.Redis(connection_pool=pool)
    try:
        for key in ("sync:outbound", "sync:retry"):
            removed = 0
            items = await client.lrange(key, 0, -1)
            for item in items:
                if sync_queue_item_matches_cleanup(item, prefix, cleanup_ids):
                    removed += int(await client.lrem(key, 0, item) or 0)
            deleted[key] = removed
    finally:
        await client.aclose()
    return deleted


async def align_integer_sequences(db, *, safety_gap: int = 0) -> dict[str, int]:
    """Advance local sequences after cross-server sync imports explicit ids."""
    aligned: dict[str, int] = {}
    safe_gap = max(0, int(safety_gap))
    for table_name in SEQUENCE_ALIGNMENT_TABLES:
        sequence_result = await db.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table_name},
        )
        sequence_name = sequence_result.scalar_one_or_none()
        max_result = await db.execute(
            text(f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {table_name}")
        )
        max_id = int(max_result.scalar_one() or 0)
        next_value = max(max_id + 1 + safe_gap, 1)
        if sequence_name:
            await db.execute(
                text("SELECT setval(CAST(:sequence_name AS regclass), :next_value, false)"),
                {"sequence_name": str(sequence_name), "next_value": next_value},
            )
        aligned[table_name] = next_value
    return aligned


async def cleanup(prefix: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user_ids = await collect_prefixed_user_ids(db, prefix)
        chat_ids = await collect_prefixed_chat_ids(db, prefix, user_ids)
        prefix_like = f"%{prefix}%"
        deleted: dict[str, int] = {}

        message_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Message.id).where(
                        or_(
                            Message.sender_id.in_(user_ids or [-1]),
                            Message.receiver_id.in_(user_ids or [-1]),
                            Message.chat_id.in_(chat_ids or [-1]),
                            Message.content.like(prefix_like),
                        )
                    )
                )
            ).scalars().all()
        ]
        conversation_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Conversation.id).where(
                        or_(
                            Conversation.user1_id.in_(user_ids or [-1]),
                            Conversation.user2_id.in_(user_ids or [-1]),
                            Conversation.last_message_id.in_(message_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        offer_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(Offer.id).where(
                        or_(
                            Offer.idempotency_key.like(f"{prefix}%"),
                            Offer.notes.like(prefix_like),
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
                            Trade.note.like(prefix_like),
                            Trade.offer_id.in_(offer_ids or [-1]),
                            Trade.offer_user_id.in_(user_ids or [-1]),
                            Trade.responder_user_id.in_(user_ids or [-1]),
                            Trade.actor_user_id.in_(user_ids or [-1]),
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
        chat_member_ids = [
            int(value)
            for value in (
                await db.execute(
                    select(ChatMember.id).where(
                        or_(
                            ChatMember.user_id.in_(user_ids or [-1]),
                            ChatMember.chat_id.in_(chat_ids or [-1]),
                        )
                    )
                )
            ).scalars().all()
        ]
        accountant_relation_ids = [
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
        upload_batch_ids = [
            str(value)
            for value in (
                await db.execute(
                    select(UploadBatch.id).where(
                        or_(
                            UploadBatch.owner_user_id.in_(user_ids or [-1]),
                            UploadBatch.actor_user_id.in_(user_ids or [-1]),
                            UploadBatch.idempotency_key.like(f"{prefix}%"),
                        )
                    )
                )
            ).scalars().all()
        ]
        upload_session_ids = [
            str(value)
            for value in (
                await db.execute(
                    select(UploadSession.id).where(
                        or_(
                            UploadSession.owner_user_id.in_(user_ids or [-1]),
                            UploadSession.actor_user_id.in_(user_ids or [-1]),
                            UploadSession.batch_id.in_(upload_batch_ids or ["__none__"]),
                            UploadSession.original_file_name.like(f"{prefix}%"),
                            UploadSession.resume_token.like(f"{prefix}%"),
                        )
                    )
                )
            ).scalars().all()
        ]
        chat_file_ids = [
            str(value)
            for value in (
                await db.execute(
                    select(ChatFile.id).where(
                        or_(
                            ChatFile.uploader_id.in_(user_ids or [-1]),
                            ChatFile.s3_key.like(prefix_like),
                            ChatFile.file_name.like(f"{prefix}%"),
                        )
                    )
                )
            ).scalars().all()
        ]

        if chat_ids:
            await db.execute(
                update(Chat)
                .where(Chat.id.in_(chat_ids))
                .values(last_message_id=None, pinned_message_id=None, pinned_message_by_id=None)
            )
        if conversation_ids:
            await db.execute(
                update(Conversation)
                .where(Conversation.id.in_(conversation_ids))
                .values(last_message_id=None)
            )
        if user_ids:
            await db.execute(update(User).where(User.id.in_(user_ids)).values(avatar_file_id=None))

        if upload_session_ids:
            deleted["upload_sessions"] = int(
                (await db.execute(delete(UploadSession).where(UploadSession.id.in_(upload_session_ids)))).rowcount or 0
            )
        if upload_batch_ids:
            deleted["upload_batches"] = int(
                (await db.execute(delete(UploadBatch).where(UploadBatch.id.in_(upload_batch_ids)))).rowcount or 0
            )
        if user_ids:
            deleted["recovery_admin_targets"] = int(
                (
                    await db.execute(
                        delete(SingleSessionRecoveryAdminTarget).where(
                            SingleSessionRecoveryAdminTarget.admin_user_id.in_(user_ids)
                        )
                    )
                ).rowcount
                or 0
            )
            deleted["single_session_recovery_requests"] = int(
                (
                    await db.execute(
                        delete(SingleSessionRecoveryRequest).where(
                            or_(
                                SingleSessionRecoveryRequest.user_id.in_(user_ids),
                                SingleSessionRecoveryRequest.decided_by_user_id.in_(user_ids),
                            )
                        )
                    )
                ).rowcount
                or 0
            )
            deleted["session_login_requests"] = int(
                (await db.execute(delete(SessionLoginRequest).where(SessionLoginRequest.user_id.in_(user_ids)))).rowcount or 0
            )
            deleted["user_sessions"] = int(
                (await db.execute(delete(UserSession).where(UserSession.user_id.in_(user_ids)))).rowcount or 0
            )
        if notification_ids:
            deleted["notifications"] = int(
                (await db.execute(delete(Notification).where(Notification.id.in_(notification_ids)))).rowcount or 0
            )
        if chat_member_ids:
            deleted["chat_members"] = int(
                (await db.execute(delete(ChatMember).where(ChatMember.id.in_(chat_member_ids)))).rowcount or 0
            )
        if message_ids:
            deleted["messages"] = int((await db.execute(delete(Message).where(Message.id.in_(message_ids)))).rowcount or 0)
        if conversation_ids:
            deleted["conversations"] = int(
                (await db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))).rowcount or 0
            )
        if chat_ids:
            deleted["chats"] = int((await db.execute(delete(Chat).where(Chat.id.in_(chat_ids)))).rowcount or 0)
        if trade_ids:
            deleted["trades"] = int((await db.execute(delete(Trade).where(Trade.id.in_(trade_ids)))).rowcount or 0)
        if offer_ids:
            deleted["offers"] = int((await db.execute(delete(Offer).where(Offer.id.in_(offer_ids)))).rowcount or 0)
        if accountant_relation_ids:
            deleted["accountant_relations"] = int(
                (
                    await db.execute(delete(AccountantRelation).where(AccountantRelation.id.in_(accountant_relation_ids)))
                ).rowcount
                or 0
            )
        if customer_relation_ids:
            deleted["customer_relations"] = int(
                (await db.execute(delete(CustomerRelation).where(CustomerRelation.id.in_(customer_relation_ids)))).rowcount
                or 0
            )
        if chat_file_ids:
            deleted["chat_files"] = int((await db.execute(delete(ChatFile).where(ChatFile.id.in_(chat_file_ids)))).rowcount or 0)
        if user_ids:
            deleted["users"] = int((await db.execute(delete(User).where(User.id.in_(user_ids)))).rowcount or 0)

        change_log_result = await db.execute(
            text(
                """
                DELETE FROM change_log
                WHERE data::text LIKE :prefix_like
                   OR (table_name = 'users' AND record_id = ANY(:user_ids))
                   OR (table_name = 'chats' AND record_id = ANY(:chat_ids))
                   OR (table_name = 'chat_members' AND record_id = ANY(:chat_member_ids))
                   OR (table_name = 'messages' AND record_id = ANY(:message_ids))
                   OR (table_name = 'conversations' AND record_id = ANY(:conversation_ids))
                   OR (table_name = 'offers' AND record_id = ANY(:offer_ids))
                   OR (table_name = 'trades' AND record_id = ANY(:trade_ids))
                   OR (table_name = 'notifications' AND record_id = ANY(:notification_ids))
                   OR (table_name = 'accountant_relations' AND record_id = ANY(:accountant_relation_ids))
                   OR (table_name = 'customer_relations' AND record_id = ANY(:customer_relation_ids))
                """
            ),
            {
                "prefix_like": prefix_like,
                "user_ids": user_ids or [-1],
                "chat_ids": chat_ids or [-1],
                "chat_member_ids": chat_member_ids or [-1],
                "message_ids": message_ids or [-1],
                "conversation_ids": conversation_ids or [-1],
                "offer_ids": offer_ids or [-1],
                "trade_ids": trade_ids or [-1],
                "notification_ids": notification_ids or [-1],
                "accountant_relation_ids": accountant_relation_ids or [-1],
                "customer_relation_ids": customer_relation_ids or [-1],
            },
        )
        deleted["change_logs"] = int(change_log_result.rowcount or 0)
        sequence_next_values = await align_integer_sequences(db)
        await db.commit()

    redis_deleted = await cleanup_sync_queue_entries(
        prefix,
        {
            "users": user_ids,
            "chats": chat_ids,
            "chat_members": chat_member_ids,
            "messages": message_ids,
            "conversations": conversation_ids,
            "offers": offer_ids,
            "trades": trade_ids,
            "notifications": notification_ids,
            "accountant_relations": accountant_relation_ids,
            "customer_relations": customer_relation_ids,
        },
    )
    return {
        "status": "ok",
        "prefix": prefix,
        "matched": {
            "users": len(user_ids),
            "chats": len(chat_ids),
            "messages": len(message_ids),
            "conversations": len(conversation_ids),
            "offers": len(offer_ids),
            "trades": len(trade_ids),
        },
        "deleted": deleted,
        "redis_deleted": redis_deleted,
        "sequence_next_values": sequence_next_values,
    }


def pick_rotating(items: list[User], *, start: int, count: int) -> list[User]:
    if not items or count <= 0:
        return []
    return [items[(start + offset) % len(items)] for offset in range(count)]


async def prepare(prefix: str, plan: FixturePlan) -> dict[str, Any]:
    await cleanup(prefix)
    setup_event_listeners()
    now = utc_now()
    token_expiry = timedelta(minutes=max(30, plan.token_minutes))

    async with AsyncSessionLocal() as db:
        sequence_next_values = await align_integer_sequences(db, safety_gap=SEQUENCE_PREPARE_SAFETY_GAP)
        super_admin = make_user(prefix, "super_admin", 1, role=UserRole.SUPER_ADMIN)
        middle_admins = [
            make_user(prefix, "middle_admin", 100 + index, role=UserRole.MIDDLE_MANAGER)
            for index in range(plan.middle_admins)
        ]
        normal_users = [
            make_user(prefix, "normal", 1000 + index, role=UserRole.STANDARD)
            for index in range(plan.normal_users)
        ]
        accountant_users = [
            make_user(prefix, "accountant", 3000 + index, role=UserRole.STANDARD)
            for index in range(plan.accountants)
        ]
        customer_users = [
            make_user(prefix, "customer", 5000 + index, role=UserRole.STANDARD)
            for index in range(plan.customers)
        ]
        all_users = [super_admin, *middle_admins, *normal_users, *accountant_users, *customer_users]
        db.add_all(all_users)
        await db.flush()

        owners = [super_admin, *middle_admins, *normal_users[: max(1, min(24, len(normal_users)))]]
        relation_expiry = now + timedelta(days=14)
        for index, accountant in enumerate(accountant_users):
            owner = owners[index % len(owners)]
            db.add(
                AccountantRelation(
                    owner_user_id=owner.id,
                    accountant_user_id=accountant.id,
                    created_by_user_id=owner.id,
                    invitation_token=f"{prefix}acct_{index:04d}",
                    global_account_name=accountant.account_name,
                    relation_display_name=f"{prefix}حسابدار {index + 1}",
                    duty_description=f"Load fixture accountant {index + 1}",
                    mobile_number=accountant.mobile_number,
                    status=AccountantRelationStatus.ACTIVE,
                    expires_at=relation_expiry,
                    activated_at=now,
                )
            )
        for index, customer in enumerate(customer_users):
            owner = owners[index % len(owners)]
            db.add(
                CustomerRelation(
                    owner_user_id=owner.id,
                    customer_user_id=customer.id,
                    created_by_user_id=owner.id,
                    invitation_token=f"{prefix}cust_{index:04d}",
                    management_name=f"{prefix}مشتری {index + 1}",
                    customer_tier=CustomerTier.TIER_1 if index % 3 else CustomerTier.TIER_2,
                    commission_rate=Decimal("0.50") if index % 3 == 0 else None,
                    min_trade_quantity=1,
                    max_trade_quantity=500,
                    max_daily_trades=100,
                    max_daily_commodity_volume=5000,
                    status=CustomerRelationStatus.ACTIVE,
                    expires_at=relation_expiry,
                    activated_at=now,
                )
            )

        await db.flush()

        direct_pairs: list[dict[str, int]] = []
        direct_candidates = [*normal_users, *middle_admins, *accountant_users, *customer_users]
        for index in range(min(plan.direct_pairs, max(0, len(direct_candidates) - 1))):
            user_a = direct_candidates[index % len(direct_candidates)]
            user_b = direct_candidates[(index + 7) % len(direct_candidates)]
            if user_a.id == user_b.id:
                continue
            user1_id, user2_id = sorted([int(user_a.id), int(user_b.id)])
            existing = (
                await db.execute(
                    select(Conversation).where(Conversation.user1_id == user1_id, Conversation.user2_id == user2_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue
            message = Message(
                sender_id=user_a.id,
                receiver_id=user_b.id,
                actor_user_id=user_a.id,
                content=f"{prefix} direct seed {index}",
                message_type=MessageType.TEXT,
                is_read=False,
                created_at=now,
            )
            db.add(message)
            await db.flush()
            db.add(
                Conversation(
                    user1_id=user1_id,
                    user2_id=user2_id,
                    last_message_id=message.id,
                    last_message_at=now,
                    unread_count_user1=1 if user1_id == user_b.id else 0,
                    unread_count_user2=1 if user2_id == user_b.id else 0,
                )
            )
            direct_pairs.append({"sender_id": int(user_a.id), "receiver_id": int(user_b.id), "message_id": int(message.id)})

        chats: list[Chat] = []
        group_summaries: list[dict[str, Any]] = []
        group_member_source = [*normal_users, *middle_admins, *accountant_users, *customer_users]
        sequence_next_values.update(await align_integer_sequences(db, safety_gap=SEQUENCE_PREPARE_SAFETY_GAP))
        for index in range(plan.groups):
            creator = owners[index % len(owners)]
            chat = Chat(
                type=ChatType.GROUP,
                title=f"{prefix}group_{index:03d}",
                description=None,
                created_by_id=creator.id,
                is_system=False,
                is_mandatory=False,
                max_members=50,
                updated_at=now,
                created_at=now,
            )
            db.add(chat)
            await db.flush()
            members = [creator, *pick_rotating(group_member_source, start=index * 5, count=18)]
            seen: set[int] = set()
            for member in members:
                if int(member.id) in seen:
                    continue
                seen.add(int(member.id))
                db.add(
                    ChatMember(
                        chat_id=chat.id,
                        user_id=member.id,
                        role=ChatMemberRole.ADMIN if member.id == creator.id else ChatMemberRole.MEMBER,
                        membership_status=ChatMembershipStatus.ACTIVE,
                        joined_at=now,
                        updated_at=now,
                    )
                )
            message = Message(
                chat_id=chat.id,
                sender_id=creator.id,
                receiver_id=creator.id,
                actor_user_id=creator.id,
                content=f"{prefix} group seed {index}",
                message_type=MessageType.TEXT,
                is_read=True,
                created_at=now,
            )
            db.add(message)
            await db.flush()
            chat.last_message_id = message.id
            chat.last_message_at = now
            chats.append(chat)
            group_summaries.append({"chat_id": int(chat.id), "member_count": len(seen), "seed_message_id": int(message.id)})

        channel_summaries: list[dict[str, Any]] = []
        channel_member_source = [*normal_users, *middle_admins, *accountant_users, *customer_users]
        sequence_next_values.update(await align_integer_sequences(db, safety_gap=SEQUENCE_PREPARE_SAFETY_GAP))
        for index in range(plan.channels):
            chat = Chat(
                type=ChatType.CHANNEL,
                title=f"{prefix}channel_{index:03d}",
                description=f"{prefix} synthetic optional channel",
                created_by_id=super_admin.id,
                is_system=False,
                is_mandatory=False,
                updated_at=now,
                created_at=now,
            )
            db.add(chat)
            await db.flush()
            members = [super_admin, *pick_rotating(channel_member_source, start=index * 9, count=30)]
            seen = set()
            for member in members:
                if int(member.id) in seen:
                    continue
                seen.add(int(member.id))
                db.add(
                    ChatMember(
                        chat_id=chat.id,
                        user_id=member.id,
                        role=ChatMemberRole.ADMIN if member.id == super_admin.id else ChatMemberRole.MEMBER,
                        membership_status=ChatMembershipStatus.ACTIVE,
                        joined_at=now,
                        updated_at=now,
                    )
                )
            message = Message(
                chat_id=chat.id,
                sender_id=super_admin.id,
                receiver_id=super_admin.id,
                actor_user_id=super_admin.id,
                content=f"{prefix} channel seed {index}",
                message_type=MessageType.TEXT,
                is_read=True,
                created_at=now,
            )
            db.add(message)
            await db.flush()
            chat.last_message_id = message.id
            chat.last_message_at = now
            chats.append(chat)
            channel_summaries.append({"chat_id": int(chat.id), "member_count": len(seen), "seed_message_id": int(message.id)})

        commodity_rows = list((await db.execute(select(Commodity).order_by(Commodity.id.asc()).limit(5))).scalars().all())
        if not commodity_rows:
            raise LoadFixtureError("No commodity rows are available for Stage L2 fixtures")

        offer_summaries: list[dict[str, Any]] = []
        offer_makers = normal_users[: max(1, min(len(normal_users), 40))] or [super_admin]
        for index in range(plan.offers):
            user = offer_makers[index % len(offer_makers)]
            commodity = commodity_rows[index % len(commodity_rows)]
            offer = Offer(
                user_id=user.id,
                actor_user_id=user.id,
                home_server=settings.server_mode,
                offer_type=OfferType.BUY if index % 2 == 0 else OfferType.SELL,
                commodity_id=commodity.id,
                quantity=25 + (index % 20),
                remaining_quantity=25 + (index % 20),
                price=100000 + (index * 250),
                exclude_from_competitive_price=True,
                is_wholesale=index % 4 != 0,
                lot_sizes=None if index % 4 != 0 else [5, 10, 10],
                original_lot_sizes=None if index % 4 != 0 else [5, 10, 10],
                status=OfferStatus.ACTIVE,
                notes=f"{prefix} offer seed {index}",
                idempotency_key=f"{prefix}offer_{index:04d}",
            )
            db.add(offer)
            await db.flush()
            offer_summaries.append({"offer_id": int(offer.id), "owner_user_id": int(user.id), "commodity_id": int(commodity.id)})

        max_trade_number = int(await db.scalar(select(func.max(Trade.trade_number))) or 9999)
        trade_summaries: list[dict[str, Any]] = []
        trade_users = normal_users[40:] or normal_users or [super_admin]
        for index in range(plan.trades):
            maker = offer_makers[index % len(offer_makers)]
            responder = trade_users[index % len(trade_users)]
            commodity = commodity_rows[index % len(commodity_rows)]
            offer = Offer(
                user_id=maker.id,
                actor_user_id=maker.id,
                home_server=settings.server_mode,
                offer_type=OfferType.SELL,
                commodity_id=commodity.id,
                quantity=10 + index,
                remaining_quantity=0,
                price=110000 + (index * 200),
                exclude_from_competitive_price=True,
                is_wholesale=True,
                status=OfferStatus.COMPLETED,
                notes=f"{prefix} completed offer seed {index}",
                idempotency_key=f"{prefix}history_offer_{index:04d}",
            )
            db.add(offer)
            await db.flush()
            trade = Trade(
                trade_number=max_trade_number + index + 1,
                offer_id=offer.id,
                offer_user_id=maker.id,
                offer_user_mobile=maker.mobile_number,
                responder_user_id=responder.id,
                responder_user_mobile=responder.mobile_number,
                actor_user_id=responder.id,
                commodity_id=commodity.id,
                trade_type=TradeType.BUY,
                quantity=10 + index,
                price=offer.price,
                status=TradeStatus.COMPLETED,
                note=f"{prefix} trade seed {index}",
                idempotency_key=f"{prefix}trade_{index:04d}",
                confirmed_at=now,
                completed_at=now,
            )
            db.add(trade)
            await db.flush()
            trade_summaries.append({"trade_id": int(trade.id), "trade_number": int(trade.trade_number)})

        notification_targets = [super_admin, *middle_admins, *normal_users, *accountant_users, *customer_users]
        for index in range(plan.notifications):
            user = notification_targets[index % len(notification_targets)]
            db.add(
                Notification(
                    user_id=user.id,
                    message=f"{prefix} notification seed {index}",
                    is_read=index % 3 == 0,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
            )

        await db.commit()

        auth_pool = {
            "prefix": prefix,
            "created_at": utc_now().isoformat().replace("+00:00", "Z"),
            "server_mode": settings.server_mode,
            "expires_in_minutes": max(30, plan.token_minutes),
            "personas": {
                "market_watchers": [token_entry(user, persona="market_watcher", expires_delta=token_expiry) for user in normal_users[:24]],
                "offer_makers": [token_entry(user, persona="offer_maker", expires_delta=token_expiry) for user in offer_makers[:20]],
                "trade_takers": [token_entry(user, persona="trade_taker", expires_delta=token_expiry) for user in (normal_users[20:40] or normal_users[:20])],
                "chat_texters": [
                    token_entry(user, persona="chat_texter", expires_delta=token_expiry)
                    for user in [*normal_users[:20], *accountant_users[:10], *customer_users[:10]]
                ],
                "chat_media_senders": [token_entry(user, persona="chat_media_sender", expires_delta=token_expiry) for user in normal_users[40:52]],
                "profile_browsers": [
                    token_entry(user, persona="profile_browser", expires_delta=token_expiry)
                    for user in [*middle_admins[:4], *normal_users[52:68], *customer_users[:8]]
                ],
                "notification_users": [token_entry(user, persona="notification_user", expires_delta=token_expiry) for user in notification_targets[:24]],
                "admin_light_read": [
                    token_entry(user, persona="admin_light_read", expires_delta=token_expiry)
                    for user in [super_admin]
                ],
            },
            "targets": {
                "commodities": [{"id": int(item.id), "name": item.name} for item in commodity_rows],
                "offers": offer_summaries,
                "trades": trade_summaries,
                "direct_pairs": direct_pairs,
                "groups": group_summaries,
                "channels": channel_summaries,
                "public_profile_user_ids": [int(user.id) for user in normal_users[:20]],
                "customer_relation_owner_ids": [int(user.id) for user in owners[:20]],
            },
        }

        return {
            "status": "ok",
            "prefix": prefix,
            "counts": {
                "users": len(all_users),
                "normal_users": len(normal_users),
                "middle_admins": len(middle_admins),
                "accountants": len(accountant_users),
                "customers": len(customer_users),
                "groups": len(group_summaries),
                "channels": len(channel_summaries),
                "direct_pairs": len(direct_pairs),
                "offers": len(offer_summaries),
                "trades": len(trade_summaries),
                "notifications": plan.notifications,
            },
            "sequence_next_values": sequence_next_values,
            "sequence_safety_gap": SEQUENCE_PREPARE_SAFETY_GAP,
            "auth_pool": auth_pool,
        }


def redact_auth_pool(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    for entries in redacted.get("personas", {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and "access_token" in entry:
                entry["access_token"] = "[REDACTED]"
    return redacted


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage L2 realistic-load fixture worker.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--prefix", required=True)
    prepare_parser.add_argument("--normal-users", type=int, default=FixturePlan.normal_users)
    prepare_parser.add_argument("--middle-admins", type=int, default=FixturePlan.middle_admins)
    prepare_parser.add_argument("--accountants", type=int, default=FixturePlan.accountants)
    prepare_parser.add_argument("--customers", type=int, default=FixturePlan.customers)
    prepare_parser.add_argument("--groups", type=int, default=FixturePlan.groups)
    prepare_parser.add_argument("--channels", type=int, default=FixturePlan.channels)
    prepare_parser.add_argument("--offers", type=int, default=FixturePlan.offers)
    prepare_parser.add_argument("--trades", type=int, default=FixturePlan.trades)
    prepare_parser.add_argument("--notifications", type=int, default=FixturePlan.notifications)
    prepare_parser.add_argument("--direct-pairs", type=int, default=FixturePlan.direct_pairs)
    prepare_parser.add_argument("--token-minutes", type=int, default=FixturePlan.token_minutes)
    prepare_parser.add_argument("--redacted", action="store_true", help="Redact tokens from stdout.")

    cleanup_parser = sub.add_parser("cleanup")
    cleanup_parser.add_argument("--prefix", required=True)
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        payload = await prepare(
            args.prefix,
            FixturePlan(
                normal_users=max(0, args.normal_users),
                middle_admins=max(0, args.middle_admins),
                accountants=max(0, args.accountants),
                customers=max(0, args.customers),
                groups=max(0, args.groups),
                channels=max(0, args.channels),
                offers=max(0, args.offers),
                trades=max(0, args.trades),
                notifications=max(0, args.notifications),
                direct_pairs=max(0, args.direct_pairs),
                token_minutes=max(30, args.token_minutes),
            ),
        )
        if args.redacted:
            payload["auth_pool"] = redact_auth_pool(payload["auth_pool"])
        json_dump(payload)
        return 0
    if args.command == "cleanup":
        json_dump(await cleanup(args.prefix))
        return 0
    raise LoadFixtureError(f"Unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
