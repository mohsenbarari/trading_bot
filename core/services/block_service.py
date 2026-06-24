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
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from core.services.accountant_relation_service import is_user_accountant
from core.services.customer_relation_service import is_user_customer


BLOCK_STATUS_REASON_CAPABILITY_DISABLED = "capability_disabled"
BLOCK_STATUS_REASON_LIMIT_REACHED = "limit_reached"
BLOCK_STATUS_REASON_CUSTOMER_DELEGATED = "customer_block_delegated"
BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED = "accountant_block_delegated"
ACCOUNTANT_BLOCK_MANAGEMENT_MESSAGE = "قابلیت بلاک کاربران فقط در اختیار سرگروه است."


async def _load_customer_display_name_map(db: AsyncSession, user_ids: List[int]) -> dict[int, str]:
    normalized_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(CustomerRelation.customer_user_id, CustomerRelation.management_name).where(
            CustomerRelation.customer_user_id.in_(normalized_ids),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    return {
        int(customer_user_id): str(management_name).strip()
        for customer_user_id, management_name in result.all()
        if customer_user_id is not None and str(management_name or "").strip()
    }


async def _is_user_customer_for_block(db: AsyncSession, user_id: int) -> bool:
    if not hasattr(db, "execute"):
        return False
    return await is_user_customer(db, user_id)


async def _is_user_accountant_for_block(db: AsyncSession, user_id: int) -> bool:
    if not hasattr(db, "execute"):
        return False
    return await is_user_accountant(db, user_id)


def _build_customer_block_status_payload(user: User) -> dict:
    return {
        "can_block": False,
        "can_block_now": False,
        "max_blocked": 0,
        "current_blocked": 0,
        "remaining": 0,
        "reason_code": BLOCK_STATUS_REASON_CUSTOMER_DELEGATED,
        "reason_message": "سیستم بلاک مشتریان توسط مالک مدیریت می‌شود.",
    }


def _build_accountant_block_status_payload(user: User) -> dict:
    return {
        "can_block": False,
        "can_block_now": False,
        "max_blocked": 0,
        "current_blocked": 0,
        "remaining": 0,
        "reason_code": BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED,
        "reason_message": ACCOUNTANT_BLOCK_MANAGEMENT_MESSAGE,
    }


def _build_block_status_payload(user: User, current_blocked: int) -> dict:
    remaining = max(0, user.max_blocked_users - current_blocked) if user.can_block_users else 0
    can_block_now = bool(user.can_block_users and remaining > 0)
    reason_code: str | None = None
    reason_message: str | None = None

    if not user.can_block_users:
        reason_code = BLOCK_STATUS_REASON_CAPABILITY_DISABLED
        reason_message = "قابلیت بلاک برای شما غیرفعال است."
    elif remaining <= 0:
        reason_code = BLOCK_STATUS_REASON_LIMIT_REACHED
        reason_message = f"ظرفیت بلاک شما تکمیل است. حداکثر {user.max_blocked_users} کاربر را می‌توانید بلاک کنید."

    return {
        "can_block": bool(user.can_block_users),
        "can_block_now": can_block_now,
        "max_blocked": user.max_blocked_users,
        "current_blocked": current_blocked,
        "remaining": remaining,
        "reason_code": reason_code,
        "reason_message": reason_message,
    }


async def can_user_block(db: AsyncSession, user_id: int) -> Tuple[bool, str, dict]:
    """
    بررسی می‌کند که آیا کاربر می‌تواند کاربر دیگری را بلاک کند.
    
    Returns:
        (can_block, error_message, status_dict)
    """
    user = await db.get(User, user_id)
    if not user:
        return False, "کاربر یافت نشد.", {}

    if await _is_user_customer_for_block(db, user_id):
        return False, "❌ سیستم بلاک مشتریان توسط مالک مدیریت می‌شود.", _build_customer_block_status_payload(user)

    if await _is_user_accountant_for_block(db, user_id):
        return False, f"❌ {ACCOUNTANT_BLOCK_MANAGEMENT_MESSAGE}", _build_accountant_block_status_payload(user)
    
    if not user.can_block_users:
        return False, "❌ قابلیت مسدود کردن برای شما غیرفعال است.", _build_block_status_payload(user, 0)
    
    # تعداد بلاک‌های فعلی
    current_blocked = await db.scalar(
        select(func.count()).where(UserBlock.blocker_id == user_id)
    )
    
    remaining = user.max_blocked_users - current_blocked
    status_payload = _build_block_status_payload(user, current_blocked)
    
    if remaining <= 0:
        return False, f"❌ شما حداکثر {user.max_blocked_users} کاربر را می‌توانید مسدود کنید.", status_payload
    
    return True, "", status_payload


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
    customer_names = await _load_customer_display_name_map(db, [user.id for _, user in rows])
    
    blocked_users = []
    for block, user in rows:
        blocked_users.append({
            "id": user.id,
            "account_name": customer_names.get(user.id) or user.account_name,
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

    if await _is_user_customer_for_block(db, user_id):
        return _build_customer_block_status_payload(user)

    if await _is_user_accountant_for_block(db, user_id):
        return _build_accountant_block_status_payload(user)
    
    current_blocked = await db.scalar(
        select(func.count()).where(UserBlock.blocker_id == user_id)
    )
    
    return _build_block_status_payload(user, current_blocked)


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
    
    matching_customer_ids = select(CustomerRelation.customer_user_id).where(
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.management_name.ilike(search_pattern),
        CustomerRelation.customer_user_id.is_not(None),
    )

    stmt = (
        select(User)
        .where(
            User.id != current_user_id,
            User.is_deleted == False,
            or_(
                User.mobile_number.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.id.in_(matching_customer_ids),
            )
        )
        .limit(limit)
    )
    
    result = await db.execute(stmt)
    users = result.scalars().all()
    customer_names = await _load_customer_display_name_map(db, [user.id for user in users])
    
    user_list = []
    for user in users:
        # چک بلاک
        is_user_blocked = await is_blocked_by(db, current_user_id, user.id)
        
        user_list.append({
            "id": user.id,
            "account_name": customer_names.get(user.id) or user.account_name,
            "mobile_number": user.mobile_number,
            "full_name": user.full_name,
            "is_blocked": is_user_blocked
        })
    
    return user_list
