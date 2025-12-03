from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional

from core.db import get_db
from models.user import User
from .auth import verify_super_admin_or_dev_key
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
    if 'trading_restricted_until' in update_data:
        restricted_until = update_data['trading_restricted_until']
        if restricted_until is not None and restricted_until.tzinfo is not None:
             # تبدیل به UTC و حذف اطلاعات منطقه زمانی برای سازگاری با دیتابیس
             from datetime import timezone
             restricted_until = restricted_until.astimezone(timezone.utc).replace(tzinfo=None)
        user.trading_restricted_until = restricted_until
    
    # Limitations
    if 'max_daily_trades' in update_data:
        user.max_daily_trades = update_data['max_daily_trades']
    if 'max_active_commodities' in update_data:
        user.max_active_commodities = update_data['max_active_commodities']
    if 'max_daily_requests' in update_data:
        user.max_daily_requests = update_data['max_daily_requests']
    if 'limitations_expire_at' in update_data:
        expire_at = update_data['limitations_expire_at']
        if expire_at is not None and expire_at.tzinfo is not None:
             from datetime import timezone
             expire_at = expire_at.astimezone(timezone.utc).replace(tzinfo=None)
        user.limitations_expire_at = expire_at
        
    await db.commit()
    await db.refresh(user)
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