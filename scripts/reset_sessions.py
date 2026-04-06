import os
import sys
import asyncio
from pathlib import Path

# Add project root to sys.path so we can import from core/models
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from core.db import AsyncSessionLocal, init_db
from models.user import User
from models.session import UserSession, SessionLoginRequest
from sqlalchemy import select, delete
from core.redis import pool
import redis.asyncio as redis

async def reset_user_sessions(mobile: str):
    await init_db()
    async with AsyncSessionLocal() as db:
        # Check if user exists
        stmt = select(User).where(User.mobile_number == mobile)
        user = (await db.execute(stmt)).scalar_one_or_none()
        
        if not user:
            print(f"❌ یافت نشد: کاربری با شماره موبایل {mobile} وجود ندارد.")
            return
            
        print(f"🔧 در حال ریست کردن سشن‌های کاربر: {user.full_name or user.account_name} ({mobile})")
        
        # Blacklist active sessions and trigger WS logout event exactly as the API does
        from core.services.session_service import force_clear_sessions
        cleared_count = await force_clear_sessions(db, user.id)
        if cleared_count > 0:
            print(f"✅ تعداد {cleared_count} نشست فعال باطل و به بلک‌لیست اضافه شد.")

        # Delete login requests
        stmt_requests = delete(SessionLoginRequest).where(SessionLoginRequest.user_id == user.id)
        await db.execute(stmt_requests)
        
        # Delete active sessions
        stmt_sessions = delete(UserSession).where(UserSession.user_id == user.id)
        await db.execute(stmt_sessions)
        
        try:
            await db.commit()
            print("✅ رکوردهای سشن و درخواست‌های لاگین از دیتابیس پاک شدند.")
        except Exception as e:
            await db.rollback()
            print(f"❌ خطا در پاکسازی دیتابیس: {e}")
            return

    # Clear Redis counters
    r = redis.Redis(connection_pool=pool, decode_responses=True)
    try:
        # Find all keys related to this user's session requests
        pattern = f"session_req:{user.id}:*"
        keys_to_delete = []
        
        async for key in r.scan_iter(pattern):
            keys_to_delete.append(key)
            
        # Also clear OTP limits and bans
        keys_to_delete.extend([
            f"otp_limit:{mobile}",
            f"banned:{mobile}"
        ])
        
        # Filter existing keys
        actual_keys = []
        for k in set(keys_to_delete):
            if await r.exists(k):
                actual_keys.append(k)
        
        if actual_keys:
            await r.delete(*actual_keys)
            print(f"✅ تعداد {len(actual_keys)} کلید محدودیت/شمارنده از ردیس پاک شد.")
        else:
            print("✅ هیچ محدودیت فعالی در ردیس یافت نشد.")
            
        print("🎉 عملیات موفق! کاربر اکنون می‌تواند از هر دستگاهی بدون تأیید لاگین کند.")
    except Exception as e:
        print(f"❌ خطا در پاکسازی ردیس: {e}")
    finally:
        await r.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("راهنما: python scripts/reset_sessions.py <mobile_number>")
        print("مثال: python scripts/reset_sessions.py 09121234567")
        sys.exit(1)
        
    mobile_input = sys.argv[1]
    
    asyncio.run(reset_user_sessions(mobile_input))
