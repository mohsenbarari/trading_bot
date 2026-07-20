from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
import asyncio
import pytz

from api.admin_authority import require_shared_admin_write_authority
from core.db import get_db
from core.audit_logger import audit_log
from core.services.accountant_relation_service import is_user_accountant
from core.services.user_account_status_service import transition_user_account_status
from core.services.chat_room_service import sync_mandatory_channel_for_user_state_change
from core.services.session_service import force_clear_sessions
from core.services.user_deletion_service import delete_user_account
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
    enqueue_account_restriction_telegram_notification_once,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.services.user_management_context_service import (
    apply_user_management_order,
    attach_user_management_relation_context,
    build_user_management_search_filter,
)
from models.user import User
from api.deps import verify_admin_or_dev_key

from core.utils import create_user_notification, send_telegram_notification, to_jalali_str
from core.enums import NotificationLevel, NotificationCategory, UserRole, UserAccountStatus
from core.user_counter_sync import reset_user_counters_in_memory
import schemas


# ===== Helper Functions for update_user =====

IRAN_TZ = pytz.timezone('Asia/Tehran')


async def attach_customer_user_context(db: AsyncSession, user: User) -> User:
    await attach_user_management_relation_context(db, [user])
    return user


def serialize_user_read(user: User) -> schemas.UserRead:
    return schemas.UserRead.model_validate(user, from_attributes=True)


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
        reset_user_counters_in_memory(user)
    
    return limitations_changed, limitation_needed, unlimit_needed


async def send_block_notification(
    db: AsyncSession, 
    user: User, 
    restricted_until: datetime,
    *,
    telegram_intent_persisted: bool = False,
) -> None:
    """ارسال نوتیفیکیشن مسدودیت"""
    message = _block_notification_message(restricted_until)
    queue_mode = (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )
    if user.telegram_id is not None and queue_mode and not telegram_intent_persisted:
        await _enqueue_block_notification_intent(
            db,
            user=user,
            message=message,
        )
    await create_user_notification(db, user.id, message, NotificationLevel.WARNING, NotificationCategory.SYSTEM)
    if user.telegram_id is not None and not queue_mode:
        await send_telegram_notification(user.telegram_id, message)


def _block_notification_message(restricted_until: datetime) -> str:
    jalali_date = to_jalali_str(restricted_until)
    if restricted_until.year > 2100:
        return (
            f"⛔ *اخطار مسدودیت حساب*\n\n"
            f"حساب کاربری شما به صورت *دائمی* مسدود شده است.\n"
            f"برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
        )
    return (
        f"⛔ *اخطار مسدودیت حساب*\n\n"
        f"حساب کاربری شما موقتاً مسدود شده است.\n\n"
        f"📅 *پایان مسدودیت:* {jalali_date}\n\n"
        f"تا زمان رفع مسدودیت امکان انجام معاملات وجود ندارد."
    )


async def _enqueue_block_notification_intent(
    db: AsyncSession,
    *,
    user: User,
    message: str,
) -> None:
    user_sync_version = int(getattr(user, "sync_version", 0) or 0)
    await enqueue_account_restriction_telegram_notification_once(
        db,
        recipient=TelegramNotificationRecipient(
            user_id=int(user.id),
            telegram_id=int(user.telegram_id),
        ),
        source_id=f"restriction-block:{user.id}:{user_sync_version}",
        text=message,
        user=user,
        restriction_kind="block",
        user_sync_version=user_sync_version,
    )


async def send_limitation_notification(
    db: AsyncSession, 
    user: User, 
    limitations_changed: List[str],
    *,
    telegram_intent_persisted: bool = False,
) -> None:
    """ارسال نوتیفیکیشن محدودیت"""
    message = _limitation_notification_message(user, limitations_changed)
    queue_mode = (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )
    if user.telegram_id is not None and queue_mode and not telegram_intent_persisted:
        await _enqueue_limitation_notification_intent(
            db,
            user=user,
            message=message,
        )
    await create_user_notification(db, user.id, message, NotificationLevel.WARNING, NotificationCategory.SYSTEM)
    if user.telegram_id is not None and not queue_mode:
        await send_telegram_notification(user.telegram_id, message)


def _limitation_notification_message(
    user: User,
    limitations_changed: List[str],
) -> str:
    expire_jalali = to_jalali_str(user.limitations_expire_at) if user.limitations_expire_at else "نامحدود"
    message = f"⚠️ *اعمال محدودیت*\n\nمحدودیت‌های زیر برای حساب شما اعمال شده است:\n\n"
    for lim in limitations_changed:
        message += f"• {lim}\n"
    message += f"\n📅 *اعتبار تا:* {expire_jalali}"
    return message


async def _enqueue_limitation_notification_intent(
    db: AsyncSession,
    *,
    user: User,
    message: str,
) -> None:
    user_sync_version = int(getattr(user, "sync_version", 0) or 0)
    await enqueue_account_restriction_telegram_notification_once(
        db,
        recipient=TelegramNotificationRecipient(
            user_id=int(user.id),
            telegram_id=int(user.telegram_id),
        ),
        source_id=(
            f"restriction-limitations:{user.id}:{user_sync_version}"
        ),
        text=message,
        user=user,
        restriction_kind="limitations",
        user_sync_version=user_sync_version,
    )


async def send_delayed_removal_notification_api(
    db_session_factory,
    user_id: int,
    telegram_id: Optional[int],
    is_block: bool,
    delay_seconds: int = 120,
    include_telegram: bool = True,
) -> None:
    """ارسال نوتیفیکیشن رفع مسدودیت/محدودیت با تاخیر
    
    قبل از ارسال بررسی می‌کند که آیا کاربر هنوز رفع محدودیت/مسدودیت است یا خیر.
    اگر مجدداً محدود شده باشد، نوتیفیکیشن ارسال نمی‌شود.
    """
    await asyncio.sleep(delay_seconds)
    
    msg: Optional[str] = None

    async for session in db_session_factory():
        user = await session.get(User, user_id)
        if not user:
            return
        
        if is_block:
            current_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if user.trading_restricted_until and user.trading_restricted_until > current_utc:
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
    
    if include_telegram and telegram_id is not None and msg is not None:
        await send_telegram_notification(telegram_id, msg)


async def enqueue_delayed_removal_telegram_notification_api(
    db: AsyncSession,
    *,
    user: User,
    is_block: bool,
    delay_seconds: int = 120,
) -> None:
    if user.telegram_id is None:
        return
    from core.services.telegram_notification_outbox_service import (
        TelegramNotificationRecipient,
        enqueue_delayed_restriction_telegram_notification_once,
    )

    kind = "block" if is_block else "limitations"
    msg = (
        "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."
        if is_block
        else "ℹ️ *رفع محدودیت توسط مدیر*\n\nمحدودیت‌های حساب شما توسط مدیر رفع شد."
    )
    due_at = datetime.now(timezone.utc) + timedelta(
        seconds=max(0, int(delay_seconds))
    )
    await enqueue_delayed_restriction_telegram_notification_once(
        db,
        recipient=TelegramNotificationRecipient(
            user_id=int(user.id),
            telegram_id=int(user.telegram_id),
        ),
        source_id=(
            f"delayed-restriction:{kind}:{user.id}:{due_at.isoformat()}"
        ),
        text=msg,
        restriction_kind=kind,
        not_before=due_at,
        user_sync_version=int(user.sync_version or 0),
    )

ADMIN_ROLE_VALUES = {UserRole.SUPER_ADMIN.value, UserRole.MIDDLE_MANAGER.value}


def _normalize_actor(actor):
    return actor if getattr(actor, "role", None) is not None else None


def _role_value(role) -> str | None:
    if role is None:
        return None
    return getattr(role, "value", role)


def _is_admin_role(role) -> bool:
    return _role_value(role) in ADMIN_ROLE_VALUES


def _audit_actor(actor) -> dict:
    actor = _normalize_actor(actor)
    return {
        "actor_id": getattr(actor, "id", None),
        "actor_role": _role_value(getattr(actor, "role", None)),
    }


def _audit_user_summary(user: User) -> dict:
    return {
        "role": _role_value(getattr(user, "role", None)),
        "account_status": getattr(getattr(user, "account_status", None), "value", getattr(user, "account_status", None)),
        "is_deleted": getattr(user, "is_deleted", None),
        "trading_restricted": bool(getattr(user, "trading_restricted_until", None)),
        "max_sessions": getattr(user, "max_sessions", None),
        "can_block_users": getattr(user, "can_block_users", None),
        "max_blocked_users": getattr(user, "max_blocked_users", None),
        "max_accountants": getattr(user, "max_accountants", None),
        "max_customers": getattr(user, "max_customers", None),
    }


def _ensure_actor_can_manage_target(actor, target_user: User):
    actor = _normalize_actor(actor)
    if actor is None:
        return None

    if _role_value(actor.role) == UserRole.MIDDLE_MANAGER.value and _is_admin_role(target_user.role):
        raise HTTPException(
            status_code=403,
            detail="مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )

    return actor


router = APIRouter(tags=["Users Management"])

@router.get("/", response_model=List[schemas.UserRead])
async def read_all_users(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = Query(None, min_length=1),
    include_deleted: bool = Query(False, description="Include soft-deleted users"),
    db: AsyncSession = Depends(get_db),
    actor = Depends(verify_admin_or_dev_key),
):
    """دریافت لیست کاربران با قابلیت جستجو."""
    query = select(User)
    actor = _normalize_actor(actor)
    
    if not include_deleted:
        query = query.where(User.is_deleted == False)

    if actor is not None and _role_value(actor.role) == UserRole.MIDDLE_MANAGER.value:
        query = query.where(~User.role.in_([UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER]))
    
    if search:
        search_pattern = f"%{search}%"
        query = query.where(build_user_management_search_filter(search_pattern))

    query = apply_user_management_order(query).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    await attach_user_management_relation_context(db, users)
    return [serialize_user_read(user) for user in users]

@router.get("/{user_id}", response_model=schemas.UserRead)
async def read_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor = Depends(verify_admin_or_dev_key),
):
    """دریافت اطلاعات یک کاربر خاص"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _ensure_actor_can_manage_target(actor, user)
    return serialize_user_read(await attach_customer_user_context(db, user))

@router.put("/{user_id}", response_model=schemas.UserRead)
async def update_user(
    user_id: int,
    user_update: schemas.UserUpdate,
    db: AsyncSession = Depends(get_db),
    actor = Depends(verify_admin_or_dev_key),
    _admin_authority: None = Depends(require_shared_admin_write_authority("users", operation="update")),
):
    """ویرایش اطلاعات کاربر (نقش، وضعیت حساب، مسدودیت و محدودیت‌ها)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    actor = _ensure_actor_can_manage_target(actor, user)
    before_summary = _audit_user_summary(user)
    
    update_data = user_update.model_dump(exclude_unset=True)
    old_role = user.role
    old_is_deleted = getattr(user, "is_deleted", False)
    old_deleted_at = getattr(user, "deleted_at", None)
    accountant_user = await is_user_accountant(db, user.id) if hasattr(db, "execute") else False
    
    # --- 1. Role Update ---
    if 'role' in update_data:
        if actor is not None and _role_value(actor.role) != UserRole.SUPER_ADMIN.value:
            raise HTTPException(status_code=403, detail="فقط مدیر ارشد می‌تواند نقش کاربر را تغییر دهد")
        user.role = update_data['role']
    
    # --- 2. Reversible Account Status ---
    if 'account_status' in update_data:
        target_status = update_data['account_status']
        if target_status is not None:
            await transition_user_account_status(db, user, UserAccountStatus(target_status))
    
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
    
    # --- 4b. Max Sessions ---
    if 'max_sessions' in update_data:
        val = update_data['max_sessions']
        user.max_sessions = 1 if accountant_user else (max(1, min(val, 3)) if val else 1)

    # --- 4c. User Block Capability ---
    if 'can_block_users' in update_data and update_data['can_block_users'] is not None:
        user.can_block_users = bool(update_data['can_block_users'])

    if 'max_blocked_users' in update_data and update_data['max_blocked_users'] is not None:
        user.max_blocked_users = max(1, min(int(update_data['max_blocked_users']), 100))

    # --- 4d. Accountant Capacity ---
    if 'max_accountants' in update_data and update_data['max_accountants'] is not None:
        user.max_accountants = max(0, update_data['max_accountants'])

    # --- 4e. Customer Capacity ---
    if 'max_customers' in update_data and update_data['max_customers'] is not None:
        user.max_customers = max(0, update_data['max_customers'])

    await sync_mandatory_channel_for_user_state_change(
        db,
        user=user,
        previous_role=old_role,
        previous_is_deleted=old_is_deleted,
        previous_deleted_at=old_deleted_at,
    )

    queue_mode = (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )
    block_intent_persisted = False
    limitation_intent_persisted = False
    if queue_mode and user.telegram_id is not None and (
        (block_notification_needed and user.trading_restricted_until)
        or limitation_needed
    ):
        # Flush first so event-driven sync_version changes are part of the
        # exact source snapshot persisted in the same transaction.
        await db.flush()
        if block_notification_needed and user.trading_restricted_until:
            await _enqueue_block_notification_intent(
                db,
                user=user,
                message=_block_notification_message(
                    user.trading_restricted_until
                ),
            )
            block_intent_persisted = True
        if limitation_needed:
            await _enqueue_limitation_notification_intent(
                db,
                user=user,
                message=_limitation_notification_message(
                    user,
                    limitations_changed,
                ),
            )
            limitation_intent_persisted = True

    # --- 5. Commit Changes ---
    await db.commit()
    await db.refresh(user)
    
    # ===== Invalidate User Cache =====
    from core.cache import invalidate_user_cache
    await invalidate_user_cache(user.telegram_id)
    # =================================
    
    # --- 6. Send Notifications ---
    
    # مسدودیت
    if block_notification_needed and user.trading_restricted_until:
        if queue_mode:
            await send_block_notification(
                db,
                user,
                user.trading_restricted_until,
                telegram_intent_persisted=block_intent_persisted,
            )
        else:
            await send_block_notification(db, user, user.trading_restricted_until)
    
    # محدودیت
    if limitation_needed:
        if queue_mode:
            await send_limitation_notification(
                db,
                user,
                limitations_changed,
                telegram_intent_persisted=limitation_intent_persisted,
            )
        else:
            await send_limitation_notification(db, user, limitations_changed)
    
    # رفع مسدودیت (با تاخیر)
    if unblock_notification_needed:
        if queue_mode:
            await enqueue_delayed_removal_telegram_notification_api(
                db,
                user=user,
                is_block=True,
            )
            await db.commit()
        asyncio.create_task(
            send_delayed_removal_notification_api(
                get_db,
                user.id,
                user.telegram_id,
                is_block=True,
                include_telegram=not queue_mode,
            )
        )
    
    # رفع محدودیت (با تاخیر)
    if unlimit_needed:
        queue_mode = (
            configured_telegram_delivery_runtime().mode
            == TelegramDeliveryRuntimeMode.QUEUE_V1
        )
        if queue_mode:
            await enqueue_delayed_removal_telegram_notification_api(
                db,
                user=user,
                is_block=False,
            )
            await db.commit()
        asyncio.create_task(
            send_delayed_removal_notification_api(
                get_db,
                user.id,
                user.telegram_id,
                is_block=False,
                include_telegram=not queue_mode,
            )
        )

    changed_fields = sorted(update_data.keys())
    if changed_fields:
        audit_log(
            "user.update",
            target_type="user",
            target_id=user.id,
            before_summary=before_summary,
            after_summary={**_audit_user_summary(user), "updated_fields": changed_fields},
            **_audit_actor(actor),
        )
    
    return serialize_user_read(await attach_customer_user_context(db, user))

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor = Depends(verify_admin_or_dev_key),
    _admin_authority: None = Depends(require_shared_admin_write_authority("users", operation="delete")),
):
    """حذف نرم کاربر (Soft Delete) با تراکنش اتمیک"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    actor = _ensure_actor_can_manage_target(actor, user)
    before_summary = _audit_user_summary(user)

    try:
        await delete_user_account(db, user)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"خطا در حذف کاربر: {str(e)}"
        )

    audit_log(
        "user.delete",
        target_type="user",
        target_id=user.id,
        before_summary=before_summary,
        after_summary={"is_deleted": True},
        **_audit_actor(actor),
    )
    
    return {"message": "User deleted successfully"}


@router.post("/{user_id}/sessions/terminate-all")
async def terminate_user_sessions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor = Depends(verify_admin_or_dev_key),
):
    """پایان دادن فوری به تمام نشست‌های فعال یک کاربر"""
    user = await db.get(User, user_id)
    if not user or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")
    actor = _ensure_actor_can_manage_target(actor, user)

    terminated_count = await force_clear_sessions(db, user.id)
    audit_log(
        "user.sessions_terminate_all",
        target_type="user",
        target_id=user.id,
        after_summary={"terminated_sessions": terminated_count},
        **_audit_actor(actor),
    )
    return {
        "detail": f"{terminated_count} نشست پایان یافت",
        "terminated_sessions": terminated_count,
    }
