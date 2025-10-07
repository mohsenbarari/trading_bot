# api/routers/invitations.py (نسخه نهایی و اصلاح شده)
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from schemas import Invitation, InvitationCreate
from models.invitation import Invitation as InvitationModel
from core.db import get_db

router = APIRouter(prefix="/invitations", tags=["Invitations"])

@router.post("/", response_model=Invitation, status_code=201)
async def create_invitation(invitation: InvitationCreate, db: AsyncSession = Depends(get_db)):
    # چک می‌کنیم که نام کاربری یا شماره موبایل تکراری نباشد
    stmt = select(InvitationModel).where(
        or_(
            InvitationModel.mobile_number == invitation.mobile_number,
            InvitationModel.account_name == invitation.account_name
        )
    )
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Invitation for this mobile number or account name already exists.")

    # --- بخش اصلاح شده ---
    token_value = f"INV-{secrets.token_hex(16)}"
    db_invitation = InvitationModel(
        mobile_number=invitation.mobile_number,
        account_name=invitation.account_name,
        token=token_value  # <-- خط کلیدی: مقدار توکن تولید شده را به آبجکت اختصاص می‌دهیم
    )
    # -----------------------

    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    return db_invitation