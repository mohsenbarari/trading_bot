from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional
import asyncio

from core.db import get_db
from models.user import User
from .auth import verify_super_admin_or_dev_key
from core.utils import create_user_notification, to_jalali_str, send_telegram_notification
from core.enums import NotificationLevel, NotificationCategory
import schemas

async def send_delayed_removal_notification_api(db_session_factory, user_id: int, telegram_id: int, is_block: bool, delay_seconds: int = 120):
    """ارسال نوتیفیکیشن رفع مسدودیت/محدودیت با تاخیر
    
    قبل از ارسال بررسی می‌کند که آیا کاربر هنوز رفع محدودیت/مسدودیت است یا خیر.
    اگر مجدداً محدود شده باشد، نوتیفیکیشن ارسال نمی‌شود.
    """
    from datetime import datetime
    
    await asyncio.sleep(delay_seconds)
    
    # بررسی وضعیت فعلی کاربر قبل از ارسال نوتیفیکیشن
    async for session in db_session_factory():
        user = await session.get(User, user_id)
        if not user:
            return  # کاربر حذف شده
        
        if is_block:
            # بررسی مسدودیت: اگر مجدداً مسدود شده، نوتیفیکیشن ارسال نشود
            if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
                return  # هنوز مسدود است، نوتیفیکیشن رفع مسدودیت ارسال نشود
            
            msg = (
                "ℹ️ *رفع مسدودیت توسط مدیر*\n\n"
                "مسدودیت حساب شما توسط مدیر رفع شد."
            )
        else:
            # بررسی محدودیت: اگر مجدداً محدود شده، نوتیفیکیشن ارسال نشود
            has_limitations = (
                user.max_daily_trades is not None or
                user.max_active_commodities is not None or
                user.max_daily_requests is not None
            )
            if has_limitations:
                return  # هنوز محدود است، نوتیفیکیشن رفع محدودیت ارسال نشود
            
            msg = (
                "ℹ️ *رفع محدودیت توسط مدیر*\n\n"
                "محدودیت‌های حساب شما توسط مدیر رفع شد."
            )
        
        await create_user_notification(
            session, user_id, msg,
            level=NotificationLevel.INFO,
            category=NotificationCategory.SYSTEM
        )
        break
    await send_telegram_notification(telegram_id, msg)

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

    update_data = user_update.model_dump(exclude_unset=True)
    if 'role' in update_data:
        user.role = update_data['role']
    
    # Track has_bot_access change
    bot_access_changed = False
    old_bot_access = user.has_bot_access
    if 'has_bot_access' in update_data:
        user.has_bot_access = update_data['has_bot_access']
        if old_bot_access != update_data['has_bot_access']:
            bot_access_changed = True
    
    # Track what changed for notifications
    block_notification_needed = False
    limitation_notification_needed = False
    unblock_notification_needed = False  # رفع مسدودیت
    unlimit_notification_needed = False  # رفع محدودیت
    
    if 'trading_restricted_until' in update_data:
        old_restricted = user.trading_restricted_until
        restricted_until = update_data['trading_restricted_until']
        if restricted_until is not None:
            import pytz
            from datetime import timezone
            if restricted_until.tzinfo is None:
                iran_tz = pytz.timezone('Asia/Tehran')
                restricted_until = iran_tz.localize(restricted_until).astimezone(timezone.utc).replace(tzinfo=None)
            else:
                restricted_until = restricted_until.astimezone(timezone.utc).replace(tzinfo=None)
            block_notification_needed = True
        else:
            # اگر قبلاً مسدود بوده و الان null شده = رفع مسدودیت
            if old_restricted is not None:
                unblock_notification_needed = True
        user.trading_restricted_until = restricted_until
    
    # Limitations
    limitations_changed = []
    old_had_limits = (user.max_daily_trades is not None or 
                      user.max_active_commodities is not None or 
                      user.max_daily_requests is not None)
    
    if 'max_daily_trades' in update_data:
        user.max_daily_trades = update_data['max_daily_trades']
        if update_data['max_daily_trades'] is not None:
            limitations_changed.append(f"مجموع تعداد معاملات: {update_data['max_daily_trades']}")
    if 'max_active_commodities' in update_data:
        user.max_active_commodities = update_data['max_active_commodities']
        if update_data['max_active_commodities'] is not None:
            limitations_changed.append(f"مجموع تعداد کالای معامله شده: {update_data['max_active_commodities']}")
    if 'max_daily_requests' in update_data:
        user.max_daily_requests = update_data['max_daily_requests']
        if update_data['max_daily_requests'] is not None:
            limitations_changed.append(f"مجموع ارسال لفظ در کانال: {update_data['max_daily_requests']}")
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
        # اگر محدودیت جدید اعمال شده، شمارنده‌ها ریست شوند
        user.trades_count = 0
        user.commodities_traded_count = 0
        user.channel_messages_count = 0
    
    # بررسی آیا همه محدودیت‌ها null شدند؟
    new_has_limits = (user.max_daily_trades is not None or 
                      user.max_active_commodities is not None or 
                      user.max_daily_requests is not None)
    if old_had_limits and not new_has_limits:
        unlimit_notification_needed = True
        # ریست شمارنده‌ها
        user.trades_count = 0
        user.commodities_traded_count = 0
        user.channel_messages_count = 0
        
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
    
    # 3. Unblock Notification (با تاخیر ۲ دقیقه)
    if unblock_notification_needed:
        asyncio.create_task(send_delayed_removal_notification_api(get_db, user.id, user.telegram_id, is_block=True))
    
    # 4. Unlimit Notification (با تاخیر ۲ دقیقه)
    if unlimit_notification_needed:
        asyncio.create_task(send_delayed_removal_notification_api(get_db, user.id, user.telegram_id, is_block=False))
    
    # 5. Bot Access Notification
    if bot_access_changed:
        if not user.has_bot_access:
            # دسترسی بات محدود شد
            bot_access_message = (
                "ℹ️ *اطلاعیه*\n\n"
                "دسترسی شما به ربات تلگرام محدود شده است.\n\n"
                "شما همچنان می‌توانید از طریق *MiniApp* به سیستم دسترسی داشته باشید.\n\n"
                "برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
            )
            await create_user_notification(
                db, user.id, bot_access_message,
                level=NotificationLevel.INFO,
                category=NotificationCategory.SYSTEM
            )
            await send_telegram_notification(user.telegram_id, bot_access_message)
        else:
            # دسترسی بات فعال شد
            bot_access_message = (
                "✅ *اطلاعیه*\n\n"
                "دسترسی شما به ربات تلگرام مجدداً فعال شد.\n\n"
                "اکنون می‌توانید از تمام امکانات بات استفاده کنید."
            )
            await create_user_notification(
                db, user.id, bot_access_message,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.SYSTEM
            )
            await send_telegram_notification(user.telegram_id, bot_access_message)
    
    return user

@router.delete("/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """حذف کاربر"""
    from core.config import settings
    from models.offer import Offer
    from models.trade import Trade
    import httpx
    
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # ارسال نوتیفیکیشن به کاربر قبل از حذف
    if user.telegram_id:
        notification_text = (
            "ℹ️ **اطلاعیه**\n\n"
            "حساب کاربری شما توسط مدیر سیستم حذف شده است.\n\n"
            "در صورت نیاز به اطلاعات بیشتر، با پشتیبانی تماس بگیرید."
        )
        await send_telegram_notification(user.telegram_id, notification_text)
        
        # حذف کاربر از کانال (kick بدون ban دائمی)
        if settings.channel_id:
            try:
                async with httpx.AsyncClient() as client:
                    # اول ban می‌کنیم
                    await client.post(
                        f"https://api.telegram.org/bot{settings.bot_token}/banChatMember",
                        json={
                            "chat_id": settings.channel_id,
                            "user_id": user.telegram_id,
                            "revoke_messages": False
                        }
                    )
                    # سپس unban می‌کنیم تا بتواند دوباره join کند
                    await client.post(
                        f"https://api.telegram.org/bot{settings.bot_token}/unbanChatMember",
                        json={
                            "chat_id": settings.channel_id,
                            "user_id": user.telegram_id,
                            "only_if_banned": True
                        }
                    )
            except Exception:
                pass  # خطا مهم نیست، ادامه بده
    
    # ذخیره شماره موبایل در معاملات قبل از حذف کاربر
    from sqlalchemy import update
    
    # ذخیره شماره موبایل برای معاملات که کاربر لفظ‌دهنده بوده
    await db.execute(
        update(Trade)
        .where(Trade.offer_user_id == user_id)
        .values(offer_user_mobile=user.mobile_number)
    )
    
    # ذخیره شماره موبایل برای معاملات که کاربر پاسخ‌دهنده بوده
    await db.execute(
        update(Trade)
        .where(Trade.responder_user_id == user_id)
        .values(responder_user_mobile=user.mobile_number)
    )
    
    # منقضی کردن لفظ‌های فعال کاربر
    from models.offer import OfferStatus
    await db.execute(
        update(Offer)
        .where(Offer.user_id == user_id, Offer.status == OfferStatus.ACTIVE)
        .values(status=OfferStatus.EXPIRED)
    )
    
    # حذف دعوت‌نامه‌های مرتبط با این کاربر (شماره موبایل یا نام کاربری)
    from models.invitation import Invitation
    from sqlalchemy import delete
    await db.execute(
        delete(Invitation).where(
            or_(
                Invitation.mobile_number == user.mobile_number,
                Invitation.account_name == user.account_name
            )
        )
    )
        
    # حذف کاربر - FKها به صورت خودکار NULL می‌شوند
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}