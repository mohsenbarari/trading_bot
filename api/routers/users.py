from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import asyncio
import pytz

from core.db import get_db
from models.user import User
from .auth import verify_super_admin_or_dev_key
from core.utils import create_user_notification, to_jalali_str, send_telegram_notification
from core.enums import NotificationLevel, NotificationCategory
import schemas


# ===== Helper Functions for update_user =====

IRAN_TZ = pytz.timezone('Asia/Tehran')


def convert_to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """تبدیل datetime به UTC (فرض می‌کند naive datetime در تایم‌زون ایران است)"""
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Naive datetime - فرض می‌کنیم ایران است
        dt = IRAN_TZ.localize(dt)
    
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def track_limitation_changes(user: User, update_data: Dict[str, Any]) -> Tuple[List[str], bool, bool]:
    """
    تغییرات محدودیت را پیگیری و اعمال می‌کند.
    
    Returns:
        (limitations_changed, limitation_needed, unlimit_needed)
    """
    limitations_changed = []
    old_had_limits = (
        user.max_daily_trades is not None or 
        user.max_active_commodities is not None or 
        user.max_daily_requests is not None
    )
    
    limit_fields = {
        'max_daily_trades': 'مجموع تعداد معاملات',
        'max_active_commodities': 'مجموع تعداد کالای معامله شده',
        'max_daily_requests': 'مجموع ارسال لفظ در کانال'
    }
    
    for field, label in limit_fields.items():
        if field in update_data:
            setattr(user, field, update_data[field])
            if update_data[field] is not None:
                limitations_changed.append(f"{label}: {update_data[field]}")
    
    if 'limitations_expire_at' in update_data:
        user.limitations_expire_at = convert_to_utc(update_data['limitations_expire_at'])
    
    new_has_limits = (
        user.max_daily_trades is not None or 
        user.max_active_commodities is not None or 
        user.max_daily_requests is not None
    )
    
    limitation_needed = bool(limitations_changed)
    unlimit_needed = old_had_limits and not new_has_limits
    
    # ریست شمارنده‌ها در هر دو حالت
    if limitation_needed or unlimit_needed:
        user.trades_count = 0
        user.commodities_traded_count = 0
        user.channel_messages_count = 0
    
    return limitations_changed, limitation_needed, unlimit_needed


async def send_block_notification(
    db: AsyncSession, 
    user: User, 
    restricted_until: datetime
) -> None:
    """ارسال نوتیفیکیشن مسدودیت"""
    jalali_date = to_jalali_str(restricted_until)
    
    if restricted_until.year > 2100:
        message = (
            f"⛔ *اخطار مسدودیت حساب*\n\n"
            f"حساب کاربری شما به صورت *دائمی* مسدود شده است.\n"
            f"برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
        )
    else:
        message = (
            f"⛔ *اخطار مسدودیت حساب*\n\n"
            f"حساب کاربری شما موقتاً مسدود شده است.\n\n"
            f"📅 *پایان مسدودیت:* {jalali_date}\n\n"
            f"تا زمان رفع مسدودیت امکان انجام معاملات وجود ندارد."
        )
    
    await create_user_notification(db, user.id, message, NotificationLevel.WARNING, NotificationCategory.SYSTEM)
    await send_telegram_notification(user.telegram_id, message)


async def send_limitation_notification(
    db: AsyncSession, 
    user: User, 
    limitations_changed: List[str]
) -> None:
    """ارسال نوتیفیکیشن محدودیت"""
    expire_jalali = to_jalali_str(user.limitations_expire_at) if user.limitations_expire_at else "نامحدود"
    
    message = f"⚠️ *اعمال محدودیت*\n\nمحدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
    for lim in limitations_changed:
        message += f"• {lim}\n"
    message += f"\n📅 *اعتبار تا:* {expire_jalali}"
    
    await create_user_notification(db, user.id, message, NotificationLevel.WARNING, NotificationCategory.SYSTEM)
    await send_telegram_notification(user.telegram_id, message)


async def send_bot_access_notification(
    db: AsyncSession, 
    user: User, 
    access_granted: bool
) -> None:
    """ارسال نوتیفیکیشن تغییر دسترسی بات"""
    if access_granted:
        message = (
            "✅ *اطلاعیه*\n\n"
            "دسترسی شما به ربات تلگرام مجدداً فعال شد.\n\n"
            "اکنون می‌توانید از تمام امکانات بات استفاده کنید."
        )
        level = NotificationLevel.SUCCESS
    else:
        message = (
            "ℹ️ *اطلاعیه*\n\n"
            "دسترسی شما به ربات تلگرام محدود شده است.\n\n"
            "شما همچنان می‌توانید از طریق *MiniApp* به سیستم دسترسی داشته باشید.\n\n"
            "برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
        )
        level = NotificationLevel.INFO
    
    await create_user_notification(db, user.id, message, level, NotificationCategory.SYSTEM)
    await send_telegram_notification(user.telegram_id, message)


async def send_delayed_removal_notification_api(db_session_factory, user_id: int, telegram_id: int, is_block: bool, delay_seconds: int = 120):
    """ارسال نوتیفیکیشن رفع مسدودیت/محدودیت با تاخیر
    
    قبل از ارسال بررسی می‌کند که آیا کاربر هنوز رفع محدودیت/مسدودیت است یا خیر.
    اگر مجدداً محدود شده باشد، نوتیفیکیشن ارسال نمی‌شود.
    """
    await asyncio.sleep(delay_seconds)
    
    async for session in db_session_factory():
        user = await session.get(User, user_id)
        if not user:
            return
        
        if is_block:
            if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
                return
            msg = "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."
        else:
            has_limitations = (
                user.max_daily_trades is not None or
                user.max_active_commodities is not None or
                user.max_daily_requests is not None
            )
            if has_limitations:
                return
            msg = "ℹ️ *رفع محدودیت توسط مدیر*\n\nمحدودیت‌های حساب شما توسط مدیر رفع شد."
        
        await create_user_notification(session, user_id, msg, NotificationLevel.INFO, NotificationCategory.SYSTEM)
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
    # فیلتر کردن کاربران حذف شده
    query = select(User).where(User.is_deleted == False).order_by(User.id.desc())
    
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
    """ویرایش اطلاعات کاربر (نقش، دسترسی بات، مسدودیت و محدودیت‌ها)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    update_data = user_update.model_dump(exclude_unset=True)
    
    # --- 1. Role Update ---
    if 'role' in update_data:
        user.role = update_data['role']
    
    # --- 2. Bot Access ---
    old_bot_access = user.has_bot_access
    if 'has_bot_access' in update_data:
        user.has_bot_access = update_data['has_bot_access']
    bot_access_changed = old_bot_access != user.has_bot_access
    
    # --- 3. Trading Restriction (مسدودیت) ---
    block_notification_needed = False
    unblock_notification_needed = False
    
    if 'trading_restricted_until' in update_data:
        old_restricted = user.trading_restricted_until
        restricted_until = convert_to_utc(update_data['trading_restricted_until'])
        
        if restricted_until is not None:
            block_notification_needed = True
        elif old_restricted is not None:
            unblock_notification_needed = True
        
        user.trading_restricted_until = restricted_until
    
    # --- 4. Limitations (محدودیت‌ها) - استفاده از helper function ---
    limitations_changed, limitation_needed, unlimit_needed = track_limitation_changes(user, update_data)
    
    # --- 5. Commit Changes ---
    await db.commit()
    await db.refresh(user)
    
    # --- 6. Send Notifications ---
    
    # مسدودیت
    if block_notification_needed and user.trading_restricted_until:
        await send_block_notification(db, user, user.trading_restricted_until)
    
    # محدودیت
    if limitation_needed:
        await send_limitation_notification(db, user, limitations_changed)
    
    # رفع مسدودیت (با تاخیر)
    if unblock_notification_needed:
        asyncio.create_task(send_delayed_removal_notification_api(get_db, user.id, user.telegram_id, is_block=True))
    
    # رفع محدودیت (با تاخیر)
    if unlimit_needed:
        asyncio.create_task(send_delayed_removal_notification_api(get_db, user.id, user.telegram_id, is_block=False))
    
    # تغییر دسترسی بات
    if bot_access_changed:
        await send_bot_access_notification(db, user, user.has_bot_access)
    
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