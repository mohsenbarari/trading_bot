# api/routers/invitations.py (نسخه نهایی با اعتبارسنجی اعداد فارسی)
import secrets
import re # <-- ۱. ماژول re اضافه شد
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from schemas import Invitation, InvitationCreate
from models.invitation import Invitation as InvitationModel
from core.db import get_db
from .auth import verify_super_admin_or_dev_key

# === ۲. توابع اعتبارسنجی و تبدیل اضافه شد ===
PERSIAN_TO_ENGLISH_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', # ارقام عربی
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', # ارقام عربی
}
MOBILE_REGEX = r"^09[0-9]{9}$" # فرمت: 11 رقم، شروع با 09

def normalize_mobile(mobile: str) -> str:
    """اعداد فارسی/عربی را به انگلیسی تبدیل می‌کند."""
    if not mobile:
        return ""
    return mobile.translate(str.maketrans(PERSIAN_TO_ENGLISH_MAP))
# === پایان افزودن توابع ===

router = APIRouter(
    prefix="/invitations", 
    tags=["Invitations"],
    dependencies=[Depends(verify_super_admin_or_dev_key)]
)

@router.post("/", response_model=Invitation, status_code=201)
async def create_invitation(invitation: InvitationCreate, db: AsyncSession = Depends(get_db)):
    
    # === ۳. اعتبارسنجی و تبدیل اعمال شد ===
    normalized_mobile = normalize_mobile(invitation.mobile_number)
    
    if not re.match(MOBILE_REGEX, normalized_mobile):
        raise HTTPException(
            status_code=400, 
            detail=f"شماره موبایل **'{invitation.mobile_number}'** نامعتبر است. فرمت صحیح: 11 رقم، شروع با 09."
        )
    # === پایان اعمال ===

    stmt_account = select(InvitationModel).where(InvitationModel.account_name == invitation.account_name)
    if (await db.execute(stmt_account)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"این نام کاربری (`{invitation.account_name}`) قبلاً استفاده شده است.")

    # === ۴. بررسی تکراری بودن روی شماره نرمال‌شده ===
    stmt_mobile = select(InvitationModel).where(InvitationModel.mobile_number == normalized_mobile)
    if (await db.execute(stmt_mobile)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"این شماره موبایل (`{normalized_mobile}`) قبلاً استفاده شده است.")

    token_value = f"INV-{secrets.token_hex(16)}"
    db_invitation = InvitationModel(
        mobile_number=normalized_mobile, # <-- ۵. ذخیره شماره نرمال‌شده
        account_name=invitation.account_name,
        token=token_value,
        role=invitation.role
    )
    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    return db_invitation