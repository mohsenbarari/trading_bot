"""Super Admin management messages for market and messenger broadcasts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, verify_super_admin
from api.routers.chat_schemas import MessageRead
from core.enums import NotificationCategory, NotificationLevel
from core.events import publish_event_sync
from core.services.admin_message_service import (
    MANAGEMENT_MESSAGE_TITLE,
    SUPPORTED_BROADCAST_TARGET_GROUPS,
    create_management_broadcast,
    create_market_management_message,
    deactivate_current_market_management_message,
    get_current_market_management_message,
    list_broadcast_recipient_user_ids,
    list_management_broadcast_history,
    list_market_management_history,
    list_market_management_recipient_user_ids,
)
from core.utils import create_user_notification, publish_user_event
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.user import User


router = APIRouter()


class AdminMarketMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    reused_from_id: int | None = None


class AdminMarketMessageRead(BaseModel):
    id: int
    content: str
    created_by_id: int
    created_by_name: str | None = None
    reused_from_id: int | None = None
    is_active: bool
    notified_recipients_count: int
    published_at: datetime
    created_at: datetime


class AdminBroadcastCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    target_groups: list[Literal["users", "managers", "accountants", "customers"]] = Field(..., min_length=1)


class AdminBroadcastRead(BaseModel):
    id: int
    content: str
    created_by_id: int
    created_by_name: str | None = None
    target_groups: list[str]
    recipient_count: int
    published_at: datetime
    created_at: datetime


class AdminBroadcastCreateResponse(AdminBroadcastRead):
    delivered_user_ids: list[int]


def _serialize_market_message(message: AdminMarketMessage | None) -> AdminMarketMessageRead | None:
    if message is None:
        return None
    return AdminMarketMessageRead(
        id=message.id,
        content=message.content,
        created_by_id=message.created_by_id,
        created_by_name=getattr(getattr(message, "created_by", None), "account_name", None),
        reused_from_id=message.reused_from_id,
        is_active=bool(message.is_active),
        notified_recipients_count=int(message.notified_recipients_count or 0),
        published_at=message.published_at,
        created_at=message.created_at,
    )


def _serialize_broadcast(message: AdminBroadcastMessage) -> AdminBroadcastRead:
    return AdminBroadcastRead(
        id=message.id,
        content=message.content,
        created_by_id=message.created_by_id,
        created_by_name=getattr(getattr(message, "created_by", None), "account_name", None),
        target_groups=list(message.target_groups or []),
        recipient_count=int(message.recipient_count or 0),
        published_at=message.published_at,
        created_at=message.created_at,
    )


def _serialize_broadcast_message_event(message, *, chat_id: int) -> dict:
    payload = MessageRead(
        id=message.id,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        content=message.content,
        message_type=message.message_type,
        is_read=message.is_read,
        is_deleted=message.is_deleted,
        updated_at=message.updated_at,
        created_at=message.created_at,
        forwarded_from_id=message.forwarded_from_id,
        sender_name=MANAGEMENT_MESSAGE_TITLE,
    ).model_dump(mode="json")
    payload.update(
        {
            "room_kind": "group",
            "chat_id": chat_id,
            "conversation_id": -int(chat_id),
            "conversation_other_user_id": -int(chat_id),
            "conversation_title": MANAGEMENT_MESSAGE_TITLE,
            "can_send": False,
            "is_system": True,
        }
    )
    return payload


async def _notify_users(
    db: AsyncSession,
    *,
    user_ids: list[int],
    message: str,
    route: str,
    extra_payload: dict | None = None,
) -> None:
    notification_text = f"{MANAGEMENT_MESSAGE_TITLE}\n{message}"
    for user_id in user_ids:
        payload = {"route": route, "source": "admin_message"}
        if extra_payload:
            payload.update(extra_payload)
        await create_user_notification(
            db,
            user_id,
            notification_text,
            level=NotificationLevel.INFO,
            category=NotificationCategory.SYSTEM,
            extra_payload=payload,
        )


@router.get("/market/current", response_model=AdminMarketMessageRead | None)
async def get_current_market_message(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return _serialize_market_message(await get_current_market_management_message(db))


@router.get("/market/history", response_model=list[AdminMarketMessageRead])
async def get_market_message_history(
    limit: int = 50,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return [_serialize_market_message(message) for message in await list_market_management_history(db, limit=limit)]


@router.post("/market", response_model=AdminMarketMessageRead)
async def create_market_message(
    data: AdminMarketMessageCreate,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    recipient_ids = await list_market_management_recipient_user_ids(db, exclude_user_ids=[current_user.id])
    message = await create_market_management_message(
        db,
        actor=current_user,
        content=data.content,
        reused_from_id=data.reused_from_id,
        notified_recipients_count=len(recipient_ids),
    )
    payload = _serialize_market_message(message).model_dump(mode="json")
    publish_event_sync("market:admin_message_published", payload)
    await _notify_users(
        db,
        user_ids=recipient_ids,
        message=message.content,
        route="/market",
        extra_payload={"admin_message_type": "market", "admin_message_id": message.id},
    )
    return _serialize_market_message(message)


@router.delete("/market/current", response_model=AdminMarketMessageRead | None)
async def clear_current_market_message(
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    message = await deactivate_current_market_management_message(db)
    publish_event_sync("market:admin_message_published", None)
    return _serialize_market_message(message)


@router.get("/broadcasts/history", response_model=list[AdminBroadcastRead])
async def get_broadcast_history(
    limit: int = 50,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return [_serialize_broadcast(message) for message in await list_management_broadcast_history(db, limit=limit)]


@router.post("/broadcasts", response_model=AdminBroadcastCreateResponse)
async def create_broadcast(
    data: AdminBroadcastCreate,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    target_groups = [group for group in data.target_groups if group in SUPPORTED_BROADCAST_TARGET_GROUPS]
    recipient_ids = await list_broadcast_recipient_user_ids(
        db,
        target_groups=target_groups,
        exclude_user_ids=[current_user.id],
    )
    broadcast, created_messages = await create_management_broadcast(
        db,
        actor=current_user,
        content=data.content,
        target_groups=target_groups,
        recipient_user_ids=recipient_ids,
    )

    for created in created_messages:
        chat_route = f"/chat?user_id={-int(created.chat.id)}"
        await publish_user_event(
            created.recipient_user_id,
            "chat:message",
            _serialize_broadcast_message_event(created.message, chat_id=created.chat.id),
        )
        await _notify_users(
            db,
            user_ids=[created.recipient_user_id],
            message=broadcast.content,
            route=chat_route,
            extra_payload={
                "admin_message_type": "broadcast",
                "admin_broadcast_id": broadcast.id,
                "chat_id": created.chat.id,
            },
        )

    base = _serialize_broadcast(broadcast).model_dump()
    return AdminBroadcastCreateResponse(**base, delivered_user_ids=recipient_ids)