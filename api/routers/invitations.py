# trading_bot/api/routers/invitations.py (کامل و اصلاح شده)

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, delete, func
from datetime import datetime, timedelta
import secrets

from core.db import get_db
from core.config import settings
from core.utils import normalize_account_name
from models.user import User
from models.invitation import Invitation
from .auth import get_admin_user_dependency # <--- از این تابع استفاده می‌کنیم
import schemas

router = APIRouter(
    prefix="/invitations",
    tags=["Invitations"],
    dependencies=[Depends(get_admin_user_dependency)] 
)

async def cleanup_expired_invitations(db: AsyncSession):
    """دعوت‌نامه‌های منقضی شده را پاک می‌کند"""
    await db.execute(
        delete(Invitation).where(Invitation.expires_at < datetime.utcnow())
    )
    await db.commit()

@router.post("/", response_model=schemas.InvitationRead)
async def create_invitation(
    invitation: schemas.InvitationCreate,
    current_user: User = Depends(get_admin_user_dependency), 
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """ایجاد لینک دعوت جدید (فقط مدیر ارشد)"""
    
    account_name_normalized = normalize_account_name(invitation.account_name)
    mobile_number_normalized = invitation.mobile_number
    
    if not account_name_normalized or len(account_name_normalized) < 3:
         raise HTTPException(status_code=422, detail="نام کاربری پس از نرمال‌سازی بسیار کوتاه است")

    # بررسی کاربر موجود
    stmt = select(User).where(
        or_(
            User.account_name == account_name_normalized,
            User.mobile_number == mobile_number_normalized
        )
    )
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        if existing_user.mobile_number == mobile_number_normalized:
            detail = f"کاربری با شماره موبایل **{mobile_number_normalized}** از قبل وجود دارد."
        else:
            detail = f"کاربری با نام کاربری **{invitation.account_name}** از قبل وجود دارد."
        raise HTTPException(status_code=409, detail=detail) # <--- این خطا باقی می‌ماند (برای کاربر تکراری)

    # بررسی دعوت‌نامه موجود
    stmt_inv = select(Invitation).where(
        or_(
            Invitation.account_name == account_name_normalized,
            Invitation.mobile_number == mobile_number_normalized
        )
    )
    existing_invitation = (await db.execute(stmt_inv)).scalar_one_or_none()
    
    background_tasks.add_task(cleanup_expired_invitations, db)

    if existing_invitation:
        if existing_invitation.expires_at > datetime.utcnow():
            # --- ۱. منطق اصلی تغییر کرد ---
            # اگر لینک فعال وجود دارد، به جای خطا، یک پیام ساختاریافته برای بات می‌فرستیم
            raise HTTPException(
                status_code=409, 
                detail=f"EXISTING_ACTIVE_LINK::{existing_invitation.account_name}::{existing_invitation.token}"
            )
            # ---------------------------
        else:
            # اگر لینک منقضی شده، حذفش می‌کنیم تا جدید بسازیم
            await db.delete(existing_invitation)
            await db.commit()

    # (ادامه کد برای ساخت لینک جدید)
    token = f"INV-{secrets.token_hex(16)}"
    #expires_at = datetime.utcnow() + timedelta(days=settings.invitation_expiry_days)
    
    expires_at = datetime.utcnow() + timedelta(minutes=3)

    db_invitation = Invitation(
        account_name=account_name_normalized,
        mobile_number=mobile_number_normalized,
        role=invitation.role,
        token=token,
        created_by_id=current_user.id,
        expires_at=expires_at
    )
    
    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    
    return db_invitation