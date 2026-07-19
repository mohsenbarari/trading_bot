"""Utilities for backfilling generic chat rows from legacy direct conversations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import ChatMembershipStatus, ChatMemberRole, ChatType
from models.chat import Chat
from models.chat_member import ChatMember
from models.conversation import Conversation
from models.message import Message
from models.user import User


@dataclass
class DirectChatBackfillStats:
    conversations_scanned: int = 0
    conversations_skipped: int = 0
    chats_created: int = 0
    chats_updated: int = 0
    members_created: int = 0
    members_updated: int = 0
    messages_linked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "conversations_scanned": self.conversations_scanned,
            "conversations_skipped": self.conversations_skipped,
            "chats_created": self.chats_created,
            "chats_updated": self.chats_updated,
            "members_created": self.members_created,
            "members_updated": self.members_updated,
            "messages_linked": self.messages_linked,
        }


def _membership_status_for_user(user: User) -> ChatMembershipStatus:
    return ChatMembershipStatus.INACTIVE if user.is_deleted else ChatMembershipStatus.ACTIVE


def _normalize_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _same_datetime(left: Optional[datetime], right: Optional[datetime]) -> bool:
    return _normalize_datetime(left) == _normalize_datetime(right)


async def _find_existing_direct_chat_id(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Optional[int]:
    member_ids = (user1_id, user2_id)
    stmt = (
        select(Chat.id)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(Chat.type == ChatType.DIRECT)
        .group_by(Chat.id)
        .having(
            func.count(sa.distinct(ChatMember.user_id)) == 2,
            func.count(sa.distinct(ChatMember.user_id)).filter(ChatMember.user_id.in_(member_ids)) == 2,
        )
        .order_by(Chat.id.asc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _load_existing_members(
    db: AsyncSession,
    chat_id: int,
    user1_id: int,
    user2_id: int,
) -> dict[int, ChatMember]:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id.in_((user1_id, user2_id)),
        )
        .order_by(ChatMember.id.asc())
    )
    result = await db.execute(stmt)
    members: dict[int, ChatMember] = {}
    for member in result.scalars().all():
        members.setdefault(member.user_id, member)
    return members


async def _count_unlinked_messages(db: AsyncSession, user1_id: int, user2_id: int) -> int:
    stmt = select(func.count(Message.id)).where(
        Message.chat_id.is_(None),
        or_(
            sa.and_(Message.sender_id == user1_id, Message.receiver_id == user2_id),
            sa.and_(Message.sender_id == user2_id, Message.receiver_id == user1_id),
        ),
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def backfill_direct_chats(
    db: AsyncSession,
    *,
    dry_run: bool = True,
    limit: Optional[int] = None,
    conversation_id: Optional[int] = None,
) -> DirectChatBackfillStats:
    """Create generic direct chats for legacy conversations and link messages.chat_id.

    This function is idempotent and intentionally does not commit. Callers decide
    whether to commit or roll back after inspecting the returned stats.
    """

    stats = DirectChatBackfillStats()

    conversation_stmt = select(Conversation).order_by(Conversation.id.asc())
    if conversation_id is not None:
        conversation_stmt = conversation_stmt.where(Conversation.id == conversation_id)
    if limit is not None:
        conversation_stmt = conversation_stmt.limit(limit)

    conversations = list((await db.execute(conversation_stmt)).scalars().all())
    if not conversations:
        return stats

    user_ids = {conversation.user1_id for conversation in conversations}
    user_ids.update(conversation.user2_id for conversation in conversations)

    user_stmt = select(User).where(User.id.in_(sorted(user_ids)))
    users = {user.id: user for user in (await db.execute(user_stmt)).scalars().all()}

    for conversation in conversations:
        stats.conversations_scanned += 1

        user1 = users.get(conversation.user1_id)
        user2 = users.get(conversation.user2_id)
        if user1 is None or user2 is None:
            stats.conversations_skipped += 1
            continue

        chat_id = await _find_existing_direct_chat_id(db, conversation.user1_id, conversation.user2_id)
        chat: Optional[Chat] = None

        if chat_id is None:
            stats.chats_created += 1
            if not dry_run:
                chat = Chat(
                    type=ChatType.DIRECT,
                    created_by_id=None,
                    last_message_id=conversation.last_message_id,
                    last_message_at=conversation.last_message_at,
                )
                db.add(chat)
                await db.flush()
                chat_id = chat.id
        else:
            chat = await db.get(Chat, chat_id)
            if chat is not None:
                needs_chat_sync = (
                    chat.last_message_id != conversation.last_message_id
                    or chat.last_message_at != conversation.last_message_at
                )
                if needs_chat_sync:
                    stats.chats_updated += 1
                    if not dry_run:
                        chat.last_message_id = conversation.last_message_id
                        chat.last_message_at = conversation.last_message_at

        if chat_id is None:
            # dry-run path for a newly-created chat does not have a real PK yet.
            stats.members_created += 2
        else:
            existing_members = await _load_existing_members(db, chat_id, conversation.user1_id, conversation.user2_id)
            for user in (user1, user2):
                expected_status = _membership_status_for_user(user)
                member = existing_members.get(user.id)
                if member is None:
                    stats.members_created += 1
                    if not dry_run:
                        db.add(
                            ChatMember(
                                chat_id=chat_id,
                                user_id=user.id,
                                role=ChatMemberRole.MEMBER,
                                membership_status=expected_status,
                                joined_at=conversation.created_at,
                                left_at=user.deleted_at if user.is_deleted else None,
                            )
                        )
                else:
                    needs_member_sync = False
                    if member.membership_status != expected_status:
                        needs_member_sync = True
                    if user.is_deleted and not _same_datetime(member.left_at, user.deleted_at):
                        needs_member_sync = True
                    if needs_member_sync:
                        stats.members_updated += 1
                        if not dry_run:
                            member.membership_status = expected_status
                            member.left_at = user.deleted_at if user.is_deleted else None

        if dry_run:
            stats.messages_linked += await _count_unlinked_messages(db, conversation.user1_id, conversation.user2_id)
            continue

        unlinked_messages = list(
            (
                await db.execute(
                    select(Message).where(
                        Message.chat_id.is_(None),
                        or_(
                            sa.and_(Message.sender_id == conversation.user1_id, Message.receiver_id == conversation.user2_id),
                            sa.and_(Message.sender_id == conversation.user2_id, Message.receiver_id == conversation.user1_id),
                        ),
                    )
                )
            ).scalars().all()
        )
        for message in unlinked_messages:
            message.chat_id = chat_id
        stats.messages_linked += len(unlinked_messages)

    return stats
