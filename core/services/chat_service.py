"""Direct-chat service helpers for the legacy Conversation-based messenger flow."""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from core.enums import MessageType
from models.conversation import Conversation
from models.message import Message
from models.user import User


def get_direct_conversation_key(user1_id: int, user2_id: int) -> tuple[int, int]:
    """Return the canonical ordering for a two-user direct conversation."""
    return (min(user1_id, user2_id), max(user1_id, user2_id))


def build_direct_conversation_projection_stmt(current_user_id: int):
    """Project only the fields needed by direct-chat list and poll endpoints."""
    user1_alias = aliased(User)
    user2_alias = aliased(User)
    last_message_alias = aliased(Message)

    other_user_id = case(
        (Conversation.user1_id == current_user_id, Conversation.user2_id),
        else_=Conversation.user1_id,
    ).label("other_user_id")
    other_user_name = case(
        (Conversation.user1_id == current_user_id, user2_alias.account_name),
        else_=user1_alias.account_name,
    ).label("other_user_name")
    other_user_is_deleted = case(
        (Conversation.user1_id == current_user_id, user2_alias.is_deleted),
        else_=user1_alias.is_deleted,
    ).label("other_user_is_deleted")
    other_user_last_seen_at = case(
        (Conversation.user1_id == current_user_id, user2_alias.last_seen_at),
        else_=user1_alias.last_seen_at,
    ).label("other_user_last_seen_at")
    unread_count = case(
        (Conversation.user1_id == current_user_id, Conversation.unread_count_user1),
        else_=Conversation.unread_count_user2,
    ).label("unread_count")
    last_message_content = case(
        (last_message_alias.is_deleted.is_(True), "پیام حذف شد"),
        (last_message_alias.message_type == MessageType.TEXT, last_message_alias.content),
        else_=None,
    ).label("last_message_content")

    stmt = (
        select(
            Conversation.id.label("id"),
            other_user_id,
            other_user_name,
            other_user_is_deleted,
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            Conversation.last_message_at.label("last_message_at"),
            unread_count,
            other_user_last_seen_at,
        )
        .select_from(Conversation)
        .join(user1_alias, Conversation.user1_id == user1_alias.id)
        .join(user2_alias, Conversation.user2_id == user2_alias.id)
        .outerjoin(last_message_alias, Conversation.last_message_id == last_message_alias.id)
    )
    return stmt, unread_count


async def get_or_create_direct_conversation(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Conversation:
    """Load or create the legacy Conversation row for a direct chat pair."""
    ordered_user1_id, ordered_user2_id = get_direct_conversation_key(user1_id, user2_id)

    stmt = select(Conversation).where(
        Conversation.user1_id == ordered_user1_id,
        Conversation.user2_id == ordered_user2_id,
    )
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()

    if conversation is None:
        conversation = Conversation(
            user1_id=ordered_user1_id,
            user2_id=ordered_user2_id,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        db.add(conversation)
        await db.flush()

    return conversation