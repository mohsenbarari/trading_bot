# trading_bot/api/routers/notifications.py (کامل و اصلاح شده)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from typing import List
from pydantic import BaseModel
from datetime import datetime

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
    is_read: bool
    created_at: datetime

@router.get("/unread", response_model=List[NotificationRead])
async def get_unread_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """فقط پیام‌های خوانده نشده را برمی‌گرداند."""
    stmt = select(Notification).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).order_by(Notification.created_at.desc())
    
    result = await db.execute(stmt)
    notifications = result.scalars().all()
    return notifications

@router.get("/", response_model=List[NotificationRead])
async def get_all_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """تمام پیام‌های کاربر (تاریخچه) را برمی‌گرداند."""
    stmt = select(Notification).where(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc())
    
    result = await db.execute(stmt)
    notifications = result.scalars().all()
    return notifications

# --- اندپوینت جدید برای رفع مشکل تکرار ---
@router.patch("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """یک پیام خاص را به عنوان خوانده شده علامت می‌زند."""
    stmt = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    )
    result = await db.execute(stmt)
    notification = result.scalar_one_or_none()
    
    if notification and not notification.is_read:
        notification.is_read = True
        await db.commit()
    return None
# ---------------------------------------
@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    تمام پیام‌های خوانده نشده کاربر را به 'خوانده شده' تغییر می‌دهد.
    """
    stmt = update(Notification).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).values(
        is_read = True
    )
    
    await db.execute(stmt)
    await db.commit()
    return None


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """حذف یک پیام خاص."""
    stmt = select(Notification).where(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    )
    result = await db.execute(stmt)
    notification = result.scalar_one_or_none()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    await db.delete(notification)
    await db.commit()
    return None