from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional

from core.db import get_db
from models.user import User
from .auth import verify_super_admin_or_dev_key
from core.utils import create_user_notification, to_jalali_str, send_telegram_notification
from core.enums import NotificationLevel, NotificationCategory
import schemas

router = APIRouter(
    prefix="/users",
    tags=["Users Management"],
    dependencies=[Depends(verify_super_admin_or_dev_key)]
)

@router.get("/", response_model=List[schemas.UserRead])
async def read_all_users(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = Query(None, min_length=1),
    db: AsyncSession = Depends(get_db)
):
    """دریافت لیست کاربران با قابلیت جستجو (فقط برای مدیر ارشد)"""
    query = select(User).order_by(User.id.desc())
    
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                User.full_name.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern)
            )
        )
        
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return users

@router.get("/{user_id}", response_model=schemas.UserRead)
async def read_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """دریافت اطلاعات یک کاربر خاص"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/{user_id}", response_model=schemas.UserRead)
async def update_user(user_id: int, user_update: schemas.UserUpdate, db: AsyncSession = Depends(get_db)):
    """ویرایش اطلاعات کاربر (نقش و دسترسی بات)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user_update.role is not None:
        user.role = user_update.role
    if user_update.has_bot_access is not None:
        user.has_bot_access = user_update.has_bot_access
    if user_update.trading_restricted_until is not None:
        # اگر مقدار ارسال شده باشد (حتی اگر None باشد برای رفع مسدودیت، البته در Pydantic اگر فیلد Optional باشد و ارسال نشود None است)
        # اما اینجا چون Optional[datetime] = None است، اگر در درخواست نباشد None است.
        # برای تمایز بین "ارسال نشده" و "ارسال شده با مقدار null" در Pydantic v2 باید از model_dump(exclude_unset=True) استفاده کرد
        # اما اینجا ساده‌تر عمل می‌کنیم: کلاینت باید مقدار را بفرستد.
        # نکته: اگر کلاینت بخواهد رفع مسدودیت کند، باید مقدار null بفرستد؟ یا یک فلگ؟
        # در اینجا فرض می‌کنیم کلاینت مقدار datetime یا null می‌فرستد.
        # اما چون در UserUpdate مقدار پیش‌فرض None است، نمی‌توانیم بفهمیم کلاینت نخواسته آپدیت کند یا خواسته None کند.
        # راه حل بهتر: استفاده از exclude_unset در روتر.
        pass

    # استفاده از exclude_unset برای اعمال تغییرات
    update_data = user_update.model_dump(exclude_unset=True)
    if 'role' in update_data:
        user.role = update_data['role']
    if 'has_bot_access' in update_data:
        user.has_bot_access = update_data['has_bot_access']
    # Track what changed for notifications
    block_notification_needed = False
    limitation_notification_needed = False
    
    if 'trading_restricted_until' in update_data:
        restricted_until = update_data['trading_restricted_until']
        if restricted_until is not None:
            import pytz
            from datetime import timezone
            if restricted_until.tzinfo is None:
                # اگر naive است، فرض می‌کنیم تایم ایران است -> تبدیل به UTC
                iran_tz = pytz.timezone('Asia/Tehran')
                restricted_until = iran_tz.localize(restricted_until).astimezone(timezone.utc).replace(tzinfo=None)
            else:
                # اگر aware است -> تبدیل به UTC
                restricted_until = restricted_until.astimezone(timezone.utc).replace(tzinfo=None)
            block_notification_needed = True
        user.trading_restricted_until = restricted_until
    
    # Limitations
    limitations_changed = []
    if 'max_daily_trades' in update_data:
        user.max_daily_trades = update_data['max_daily_trades']
        if update_data['max_daily_trades'] is not None:
            limitations_changed.append(f"تعداد ترید روزانه: {update_data['max_daily_trades']}")
    if 'max_active_commodities' in update_data:
        user.max_active_commodities = update_data['max_active_commodities']
        if update_data['max_active_commodities'] is not None:
            limitations_changed.append(f"تعداد کالای فعال: {update_data['max_active_commodities']}")
    if 'max_daily_requests' in update_data:
        user.max_daily_requests = update_data['max_daily_requests']
        if update_data['max_daily_requests'] is not None:
            limitations_changed.append(f"تعداد درخواست روزانه: {update_data['max_daily_requests']}")
    if 'limitations_expire_at' in update_data:
        expire_at = update_data['limitations_expire_at']
        if expire_at is not None:
            import pytz
            from datetime import timezone
            if expire_at.tzinfo is None:
                iran_tz = pytz.timezone('Asia/Tehran')
                expire_at = iran_tz.localize(expire_at).astimezone(timezone.utc).replace(tzinfo=None)
            else:
                expire_at = expire_at.astimezone(timezone.utc).replace(tzinfo=None)
        user.limitations_expire_at = expire_at
    
    if limitations_changed:
        limitation_notification_needed = True
        
    await db.commit()
    await db.refresh(user)
    
    # --- Send Notifications ---
    
    # 1. Block Notification
    if block_notification_needed and user.trading_restricted_until:
        jalali_date = to_jalali_str(user.trading_restricted_until)
        # Check if it's a permanent block (year > 2100)
        if user.trading_restricted_until.year > 2100:
            block_message = (
                f"⛔ *اخطار مسدودیت حساب*\n\n"
                f"حساب کاربری شما به صورت *دائمی* مسدود شده است.\n"
                f"برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
            )
        else:
            block_message = (
                f"⛔ *اخطار مسدودیت حساب*\n\n"
                f"حساب کاربری شما موقتاً مسدود شده است.\n\n"
                f"📅 *پایان مسدودیت:* {jalali_date}\n\n"
                f"تا زمان رفع مسدودیت امکان انجام معاملات وجود ندارد."
            )
        # In-app notification
        await create_user_notification(
            db, user.id, block_message,
            level=NotificationLevel.WARNING,
            category=NotificationCategory.SYSTEM
        )
        # Telegram notification
        await send_telegram_notification(user.telegram_id, block_message)
    
    # 2. Limitation Notification
    if limitation_notification_needed:
        expire_jalali = to_jalali_str(user.limitations_expire_at) if user.limitations_expire_at else "نامحدود"
        limitation_message = (
            f"⚠️ *اعمال محدودیت*\n\n"
            f"محدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
        )
        for lim in limitations_changed:
            limitation_message += f"• {lim}\n"
        limitation_message += f"\n📅 *اعتبار تا:* {expire_jalali}"
        
        # In-app notification
        await create_user_notification(
            db, user.id, limitation_message,
            level=NotificationLevel.WARNING,
            category=NotificationCategory.SYSTEM
        )
        # Telegram notification
        await send_telegram_notification(user.telegram_id, limitation_message)
    
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """حذف کاربر"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}