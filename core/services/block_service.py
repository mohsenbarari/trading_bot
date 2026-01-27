# core/services/block_service.py
"""
Block Service - منطق مسدود کردن کاربران

این ماژول شامل توابع مدیریت بلاک کاربران است.
"""
from typing import Tuple, List, Optional
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.user_block import UserBlock


async def can_user_block(db: AsyncSession, user_id: int) -> Tuple[bool, str, dict]:
    """
    بررسی می‌کند که آیا کاربر می‌تواند کاربر دیگری را بلاک کند.
    
    Returns:
        (can_block, error_message, status_dict)
    """
    user = await db.get(User, user_id)
    if not user:
        return False, "کاربر یافت نشد.", {}
    
    if not user.can_block_users:
        return False, "❌ قابلیت مسدود کردن برای شما غیرفعال است.", {
            "can_block": False,
            "max_blocked": user.max_blocked_users,
            "current_blocked": 0,
            "remaining": 0
        }
    
    # تعداد بلاک‌های فعلی
    current_blocked = await db.scalar(
        select(func.count()).where(UserBlock.blocker_id == user_id)
    )
    
    remaining = user.max_blocked_users - current_blocked
    
    if remaining <= 0:
        return False, f"❌ شما حداکثر {user.max_blocked_users} کاربر را می‌توانید مسدود کنید.", {
            "can_block": False,
            "max_blocked": user.max_blocked_users,
            "current_blocked": current_blocked,
            "remaining": 0
        }
    
    return True, "", {
        "can_block": True,
        "max_blocked": user.max_blocked_users,
        "current_blocked": current_blocked,
        "remaining": remaining
    }


async def block_user(db: AsyncSession, blocker_id: int, blocked_id: int) -> Tuple[bool, str]:
    """
    مسدود کردن کاربر
    
    Returns:
        (success, message)
    """
    # چک کاربر یکسان
    if blocker_id == blocked_id:
        return False, "❌ نمی‌توانید خودتان را مسدود کنید!"
    
    # چک بلاک قبلی
    existing = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id
        )
    )
    if existing:
        return False, "این کاربر قبلاً مسدود شده است."
    
    # چک محدودیت
    can_block, error_msg, _ = await can_user_block(db, blocker_id)
    if not can_block:
        return False, error_msg
    
    # ایجاد بلاک
    new_block = UserBlock(
        blocker_id=blocker_id,
        blocked_id=blocked_id
    )
    db.add(new_block)
    await db.commit()
    
    return True, "✅ کاربر با موفقیت مسدود شد."


async def unblock_user(db: AsyncSession, blocker_id: int, blocked_id: int) -> Tuple[bool, str]:
    """
    رفع مسدودیت کاربر
    
    Returns:
        (success, message)
    """
    block = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id
        )
    )
    
    if not block:
        return False, "این کاربر مسدود نشده است."
    
    await db.delete(block)
    await db.commit()
    
    return True, "✅ مسدودیت کاربر برداشته شد."


async def is_blocked(db: AsyncSession, user_a_id: int, user_b_id: int) -> Tuple[bool, Optional[int]]:
    """
    بررسی می‌کند که آیا بین دو کاربر بلاک وجود دارد (دوطرفه).
    
    Returns:
        (is_blocked, blocker_id) - blocker_id کسی است که بلاک کرده
    """
    # چک A -> B
    block_a_b = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == user_a_id,
            UserBlock.blocked_id == user_b_id
        )
    )
    if block_a_b:
        return True, user_a_id
    
    # چک B -> A
    block_b_a = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == user_b_id,
            UserBlock.blocked_id == user_a_id
        )
    )
    if block_b_a:
        return True, user_b_id
    
    return False, None


async def get_blocked_users(db: AsyncSession, user_id: int) -> List[dict]:
    """
    لیست کاربران مسدود شده توسط کاربر
    
    Returns:
        List of {id, account_name, mobile_number, created_at}
    """
    stmt = (
        select(UserBlock, User)
        .join(User, User.id == UserBlock.blocked_id)
        .where(UserBlock.blocker_id == user_id)
        .order_by(UserBlock.created_at.desc())
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    blocked_users = []
    for block, user in rows:
        blocked_users.append({
            "id": user.id,
            "account_name": user.account_name,
            "mobile_number": user.mobile_number,
            "full_name": user.full_name,
            "blocked_at": block.created_at
        })
    
    return blocked_users


async def get_block_status(db: AsyncSession, user_id: int) -> dict:
    """
    وضعیت قابلیت بلاک کاربر
    
    Returns:
        {can_block, max_blocked, current_blocked, remaining, blocked_users}
    """
    user = await db.get(User, user_id)
    if not user:
        return {"error": "کاربر یافت نشد"}
    
    current_blocked = await db.scalar(
        select(func.count()).where(UserBlock.blocker_id == user_id)
    )
    
    return {
        "can_block": user.can_block_users,
        "max_blocked": user.max_blocked_users,
        "current_blocked": current_blocked,
        "remaining": max(0, user.max_blocked_users - current_blocked) if user.can_block_users else 0
    }


async def is_blocked_by(db: AsyncSession, blocker_id: int, blocked_id: int) -> bool:
    """
    بررسی می‌کند که آیا blocker کاربر blocked را مسدود کرده است (یک‌طرفه).
    """
    block = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_id == blocker_id,
            UserBlock.blocked_id == blocked_id
        )
    )
    return block is not None


async def search_users_for_block(
    db: AsyncSession, 
    query: str, 
    current_user_id: int,
    limit: int = 10
) -> List[dict]:
    """
    جستجوی کاربران برای بلاک
    
    Args:
        query: شماره موبایل یا نام کاربری (حداقل 2 کاراکتر)
        current_user_id: شناسه کاربر جستجوکننده (برای حذف از نتایج)
        limit: حداکثر تعداد نتایج
    
    Returns:
        List of {id, account_name, mobile_number, is_blocked}
    """
    if len(query) < 2:
        return []
    
    search_pattern = f"%{query}%"
    
    stmt = (
        select(User)
        .where(
            User.id != current_user_id,
            User.is_deleted == False,
            or_(
                User.mobile_number.ilike(search_pattern),
                User.account_name.ilike(search_pattern)
            )
        )
        .limit(limit)
    )
    
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    user_list = []
    for user in users:
        # چک بلاک
        is_user_blocked = await is_blocked_by(db, current_user_id, user.id)
        
        user_list.append({
            "id": user.id,
            "account_name": user.account_name,
            "mobile_number": user.mobile_number,
            "full_name": user.full_name,
            "is_blocked": is_user_blocked
        })
    
    return user_list
