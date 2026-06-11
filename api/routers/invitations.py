from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
import secrets
import string
from datetime import datetime, timedelta

from core.db import get_db
from core.utils import utc_now_naive
from core.services.accountant_relation_service import (
    get_pending_accountant_relation_by_invitation_token,
    is_accountant_invitation_token,
)
from core.services.customer_relation_service import (
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
)
from models.invitation import Invitation
from models.user import User, UserRole
from core.config import settings
from api.deps import verify_admin_user
from core.sms import send_invitation_sms
from core.connectivity import is_internet_connected

router = APIRouter()

class InvitationCreate(BaseModel):
    account_name: str
    mobile_number: str
    role: UserRole = UserRole.WATCH

class InvitationResponse(BaseModel):
    token: str
    link: str
    short_link: str | None
    expires_at: datetime


class PendingInvitationResponse(BaseModel):
    id: int
    account_name: str
    mobile_number: str
    role: UserRole
    token: str
    short_code: str | None
    web_link: str
    short_link: str | None
    expires_at: datetime
    created_at: datetime | None
    created_by_id: int | None

def generate_token():
    return "INV-" + secrets.token_hex(16)


def generate_short_code():
    # 8-char random string (alphanumeric)
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))


def build_invitation_links(invitation: Invitation) -> tuple[str, str, str | None]:
    bot_link = f"https://t.me/{settings.bot_username}?start={invitation.token}"
    web_link = f"{settings.frontend_url}/register?token={invitation.token}"
    short_link = f"{settings.frontend_url}/i/{invitation.short_code}" if invitation.short_code else None
    return bot_link, web_link, short_link


def serialize_pending_invitation(invitation: Invitation) -> dict:
    _bot_link, web_link, short_link = build_invitation_links(invitation)
    return {
        "id": invitation.id,
        "account_name": invitation.account_name,
        "mobile_number": invitation.mobile_number,
        "role": invitation.role,
        "token": invitation.token,
        "short_code": invitation.short_code,
        "web_link": web_link,
        "short_link": short_link,
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
        "created_by_id": invitation.created_by_id,
    }


def pending_invitation_select(now: datetime):
    return select(Invitation).where(
        Invitation.token.like("INV-%"),
        Invitation.is_used == False,
        Invitation.expires_at > now,
    )


def can_manage_invitation(admin: User, invitation: Invitation) -> bool:
    if getattr(admin, "role", None) == UserRole.SUPER_ADMIN:
        return True
    return invitation.created_by_id == admin.id


@router.get("/pending", response_model=list[PendingInvitationResponse])
async def list_pending_invitations(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user),
):
    now = utc_now_naive()
    stmt = (
        pending_invitation_select(now)
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        .limit(100)
    )
    if getattr(admin, "role", None) != UserRole.SUPER_ADMIN:
        stmt = stmt.where(Invitation.created_by_id == admin.id)

    invitations = list((await db.execute(stmt)).scalars().all())
    return [serialize_pending_invitation(invitation) for invitation in invitations]


@router.delete("/pending/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pending_invitation(
    invitation_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user),
):
    stmt = select(Invitation).where(Invitation.id == invitation_id)
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if (
        not invitation
        or not str(invitation.token or "").startswith("INV-")
        or not can_manage_invitation(admin, invitation)
    ):
        raise HTTPException(status_code=404, detail="دعوت‌نامه پیدا نشد")

    if invitation.is_used or invitation.expires_at <= utc_now_naive():
        raise HTTPException(status_code=400, detail="دعوت‌نامه pending نیست")

    await db.delete(invitation)
    await db.commit()
    return None

@router.post("/", response_model=InvitationResponse)
async def create_invitation(
    invite: InvitationCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_admin_user)
):
    # Normalize mobile (simple check)
    mobile = invite.mobile_number
    if not mobile.startswith("09") or len(mobile) != 11:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است")

    # Check if user already exists
    stmt = select(User).where(
        (User.account_name == invite.account_name) | 
        (User.mobile_number == mobile)
    )
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="کاربری با این نام کاربری یا موبایل قبلاً ثبت شده است")

    if getattr(admin, "role", UserRole.SUPER_ADMIN) == UserRole.MIDDLE_MANAGER and invite.role not in (UserRole.WATCH, UserRole.STANDARD):
        raise HTTPException(status_code=403, detail="مدیر میانی فقط می‌تواند کاربران عادی یا تماشا را دعوت کند")

    # Check active invitation
    stmt = select(Invitation).where(
        (Invitation.mobile_number == mobile) &
        (Invitation.is_used == False) &
        (Invitation.expires_at > func.now())
    )
    active_inv = (await db.execute(stmt)).scalar_one_or_none()
    if active_inv:
        # Return existing active invitation
        bot_link, _web_link, short_link = build_invitation_links(active_inv)
        
        return {
            "token": active_inv.token,
            "link": bot_link,
            "short_link": short_link,
            "expires_at": active_inv.expires_at
        }

    # Create new invitation
    token = generate_token()
    short_code = generate_short_code()
    expires_at = utc_now_naive() + timedelta(days=settings.invitation_expiry_days)
    
    new_inv = Invitation(
        account_name=invite.account_name,
        mobile_number=mobile,
        role=invite.role,
        token=token,
        short_code=short_code,
        created_by_id=admin.id,
        expires_at=expires_at
    )
    
    db.add(new_inv)
    await db.commit()
    await db.refresh(new_inv)
    
    bot_link, web_link, short_link = build_invitation_links(new_inv)
    
    # Send SMS based on connectivity
    is_connected = await is_internet_connected()
    
    send_invitation_sms(
        mobile=mobile,
        account_name=invite.account_name,
        bot_link=bot_link,
        web_link=web_link
    )
    
    return {
        "token": token,
        "link": bot_link,
        "short_link": short_link,
        "expires_at": expires_at
    }

@router.get("/lookup/{short_code}")
async def lookup_invitation(
    short_code: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Lookup full token from short code.
    """
    stmt = select(Invitation).where(Invitation.short_code == short_code)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv:
        raise HTTPException(status_code=404, detail="Invalid short code")
        
    if inv.is_used:
        raise HTTPException(status_code=400, detail="Invitation already used")

    if is_accountant_invitation_token(inv.token):
        relation = await get_pending_accountant_relation_by_invitation_token(db, inv.token)
        if not relation:
            raise HTTPException(status_code=400, detail="Invitation expired")
    elif is_customer_invitation_token(inv.token):
        relation = await get_pending_customer_relation_by_invitation_token(db, inv.token)
        if not relation:
            raise HTTPException(status_code=400, detail="Invitation expired")
        
    if inv.expires_at < utc_now_naive():
        raise HTTPException(status_code=400, detail="Invitation expired")
        
    return {"token": inv.token}

@router.get("/validate/{token}")
async def validate_invitation(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Validate invitation token for web registration.
    """
    stmt = select(Invitation).where(Invitation.token == token)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv:
        raise HTTPException(status_code=404, detail="Invalid token")
        
    if inv.is_used:
        raise HTTPException(status_code=400, detail="Invitation already used")

    if is_accountant_invitation_token(token):
        relation = await get_pending_accountant_relation_by_invitation_token(db, token)
        if not relation:
            raise HTTPException(status_code=400, detail="Invitation expired")
    elif is_customer_invitation_token(token):
        relation = await get_pending_customer_relation_by_invitation_token(db, token)
        if not relation:
            raise HTTPException(status_code=400, detail="Invitation expired")
        
    if inv.expires_at < utc_now_naive():
        raise HTTPException(status_code=400, detail="Invitation expired")
        
    return {
        "valid": True,
        "account_name": inv.account_name,
        "mobile_number": inv.mobile_number,
        "role": inv.role
    }
