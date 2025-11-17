# trading_bot/api/routers/notifications.py (فایل جدید)

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from core.db import get_db
from models.notification import Notification
from models.user import User
from .auth import get_current_user

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
    dependencies=[Depends(get_current_user)]
)

class NotificationRead(BaseModel):
    id: int
    message: str

@router.get("/unread", response_model=List[NotificationRead])
async def get_unread_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """دریافت پیام‌های خوانده نشده و علامت‌گذاری آن‌ها به عنوان خوانده شده"""
    stmt = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    )
    result = await db.execute(stmt)
    notifications = result.scalars().all()

    # مارک کردن به عنوان خوانده شده
    for notif in notifications:
        notif.is_read = True
    
    if notifications:
        await db.commit()

    return [{"id": n.id, "message": n.message} for n in notifications]