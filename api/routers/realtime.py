# api/routers/realtime.py
"""
Real-time SSE (Server-Sent Events) for MiniApp
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis
import asyncio
import json

from core.db import get_db
from core.redis import pool
from models.user import User
from .auth import get_current_user_optional


router = APIRouter(
    prefix="/realtime",
    tags=["Real-time"],
)


async def event_generator(user_id: int):
    """
    Generator برای SSE events
    گوش می‌دهد به رویدادهای Redis و آنها را به کلاینت ارسال می‌کند
    """
    async with redis.Redis(connection_pool=pool) as redis_client:
        pubsub = redis_client.pubsub()
        
        # Subscribe به کانال‌های مورد نیاز
        await pubsub.subscribe(
            "events:offer:created",
            "events:offer:expired",
            "events:offer:updated",
            "events:trade:created",
            f"notifications:{user_id}"  # نوتیفیکیشن‌های شخصی کاربر
        )
        
        # ارسال heartbeat برای جلوگیری از timeout
        heartbeat_interval = 30  # ثانیه
        last_heartbeat = asyncio.get_event_loop().time()
        
        try:
            while True:
                # چک کردن پیام‌های جدید
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=5.0
                )
                
                if message and message.get("type") == "message":
                    channel = message.get("channel", b"").decode("utf-8")
                    data = message.get("data", b"").decode("utf-8")
                    
                    # تعیین نوع رویداد
                    event_type = channel.replace("events:", "").replace(f"notifications:{user_id}", "notification")
                    
                    yield f"event: {event_type}\ndata: {data}\n\n"
                
                # ارسال heartbeat
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat > heartbeat_interval:
                    yield f"event: heartbeat\ndata: {json.dumps({'timestamp': current_time})}\n\n"
                    last_heartbeat = current_time
                    
        except asyncio.TimeoutError:
            # ادامه حلقه در صورت timeout
            pass
        except asyncio.CancelledError:
            # cleanup در صورت cancel شدن
            await pubsub.unsubscribe()
            raise
        finally:
            await pubsub.unsubscribe()


@router.get("/stream")
async def sse_stream(request: Request, current_user: User = Depends(get_current_user_optional)):
    """
    SSE endpoint برای دریافت رویدادهای Real-time
    
    Events:
    - offer:created - لفظ جدید ایجاد شد
    - offer:expired - لفظ منقضی شد
    - offer:updated - موجودی لفظ تغییر کرد
    - trade:created - معامله جدید انجام شد
    - notification - نوتیفیکیشن شخصی
    - heartbeat - برای جلوگیری از timeout
    """
    user_id = current_user.id if current_user else 0
    
    return StreamingResponse(
        event_generator(user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # برای nginx
        }
    )


# --- Helper function برای publish رویدادها ---

async def publish_event(event_type: str, data: dict):
    """
    Publish یک رویداد به Redis برای SSE
    
    event_type: مثل 'offer:created', 'trade:created'
    data: دیتای JSON-serializable
    """
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            channel = f"events:{event_type}"
            await redis_client.publish(channel, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        print(f"⚠️ Error publishing event {event_type}: {e}")
