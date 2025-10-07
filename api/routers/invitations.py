# api/routers/invitations.py (نسخه نهایی با پیام‌های خطای دقیق)
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from schemas import Invitation, InvitationCreate
from models.invitation import Invitation as InvitationModel
from core.db import get_db

router = APIRouter(prefix="/invitations", tags=["Invitations"])

@router.post("/", response_model=Invitation, status_code=201)
async def create_invitation(invitation: InvitationCreate, db: AsyncSession = Depends(get_db)):
    # --- بخش کلیدی اصلاح شده ---

    # ۱. ابتدا بررسی می‌کنیم که آیا نام کاربری تکراری است؟
    stmt_account = select(InvitationModel).where(InvitationModel.account_name == invitation.account_name)
    if (await db.execute(stmt_account)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"این نام کاربری (`{invitation.account_name}`) قبلاً استفاده شده است.")

    # ۲. سپس بررسی می‌کنیم که آیا شماره موبایل تکراری است؟
    stmt_mobile = select(InvitationModel).where(InvitationModel.mobile_number == invitation.mobile_number)
    if (await db.execute(stmt_mobile)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"این شماره موبایل (`{invitation.mobile_number}`) قبلاً استفاده شده است.")

    # --------------------------

    # اگر هیچکدام تکراری نبود، دعوتنامه را می‌سازیم
    token_value = f"INV-{secrets.token_hex(16)}"
    db_invitation = InvitationModel(
        mobile_number=invitation.mobile_number,
        account_name=invitation.account_name,
        token=token_value,
        role=invitation.role
    )
    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    return db_invitation