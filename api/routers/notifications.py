# trading_bot/api/routers/notifications.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from typing import List
from pydantic import BaseModel
from datetime import datetime
from fastapi.responses import StreamingResponse
import asyncio
import json
from core.db import get_db
from models.notification import Notification
from models.user import User
from api.deps import get_current_user

from core.redis import get_redis, Redis 
from core.enums import NotificationLevel, NotificationCategory

router = APIRouter(
    tags=["Notifications"],
    dependencies=[Depends(get_current_user)]
)

class NotificationRead(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: datetime
    # 👇 این دو خط حیاتی هستند تا فرانت‌اند بتواند استایل را اعمال کند
    level: NotificationLevel 
    category: NotificationCategory

    class Config:
        from_attributes = True 

@router.get("/unread-count", response_model=int)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """
    تعداد پیام‌های خوانده نشده را مستقیماً از Redis می‌خواند.
    """
    count_key = f"user:{current_user.id}:unread_count"
    count = await redis.get(count_key)
    return int(count or 0)

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
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis) # ✅ اصلاح شد: aioredis.Redis -> Redis
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
        
        # کاهش شمارنده در Redis
        count_key = f"user:{current_user.id}:unread_count"
        await redis.decr(count_key)
        
        # اطمینان از منفی نشدن
        current_count = await redis.get(count_key)
        if current_count and int(current_count) < 0:
            await redis.set(count_key, 0)

    return None

@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis)
):
    """
    تمام پیام‌ها را در دیتابیس خوانده شده ثبت می‌کند و شمارنده Redis را صفر می‌کند.
    """
    # ۱. آپدیت دیتابیس
    stmt = update(Notification).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).values(
        is_read = True
    )
    await db.execute(stmt)
    await db.commit()
    
    # ۲. ریست کردن شمارنده در Redis
    count_key = f"user:{current_user.id}:unread_count"
    await redis.set(count_key, 0)
    
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


@router.get("/stream")
async def stream_notifications(
    current_user: User = Depends(get_current_user),
    redis: Redis = Depends(get_redis)
):
    """
    اتصال SSE برای دریافت زنده نوتیفیکیشن‌ها
    """
    async def event_generator():
        pubsub = redis.pubsub()
        channel_key = f"notifications:{current_user.id}"
        await pubsub.subscribe(channel_key)
        
        try:
            # گوش دادن به کانال
            async for message in pubsub.listen():
                if message["type"] == "message":
                    raw_data = message["data"]
                    try:
                        payload = json.loads(raw_data)
                        # Check for new event format
                        if isinstance(payload, dict) and "event" in payload:
                            yield f"event: {payload['event']}\n"
                            yield f"data: {json.dumps(payload['data'])}\n\n"
                        else:
                            yield f"data: {raw_data}\n\n"
                    except:
                        yield f"data: {raw_data}\n\n"
                    
                # هرت‌بیت (Heartbeat) برای زنده نگه داشتن اتصال (اختیاری)
                # await asyncio.sleep(0.1) 
        except asyncio.CancelledError:
            # اگر کلاینت قطع شد
            await pubsub.unsubscribe(channel_key)

    return StreamingResponse(event_generator(), media_type="text/event-stream")