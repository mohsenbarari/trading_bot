from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from core.db import get_db
from models.invitation import Invitation
from models.user import User, UserRole
from core.config import settings
from api.deps import get_current_user, verify_super_admin
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

def generate_token():
    return "INV-" + secrets.token_hex(16)

def generate_short_code():
    # 8-char random string (alphanumeric)
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(8))

@router.post("/", response_model=InvitationResponse)
async def create_invitation(
    invite: InvitationCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(verify_super_admin)
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

    # Check active invitation
    stmt = select(Invitation).where(
        (Invitation.mobile_number == mobile) &
        (Invitation.is_used == False) &
        (Invitation.expires_at > func.now())
    )
    active_inv = (await db.execute(stmt)).scalar_one_or_none()
    if active_inv:
        # Return existing active invitation
        bot_link = f"https://t.me/{settings.bot_username}?start={active_inv.token}"
        web_link = f"{settings.frontend_url}/register?token={active_inv.token}"
        short_link = f"{settings.frontend_url}/i/{active_inv.short_code}" if active_inv.short_code else None
        
        return {
            "token": active_inv.token,
            "link": bot_link,
            "short_link": short_link,
            "expires_at": active_inv.expires_at
        }

    # Create new invitation
    token = generate_token()
    short_code = generate_short_code()
    expires_at = datetime.utcnow() + timedelta(days=settings.invitation_expiry_days)
    
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
    
    bot_link = f"https://t.me/{settings.bot_username}?start={token}"
    web_link = f"{settings.frontend_url}/register?token={token}"
    short_link = f"{settings.frontend_url}/i/{short_code}"
    
    # Send SMS based on connectivity
    is_connected = await is_internet_connected()
    
    # Always send two links as requested by user
    # Text is handled inside send_invitation_sms
    # We send the direct links, or short link if preferred?
    # User wanted two links. Let's send the direct links to avoid extra redirect if possible,
    # OR send the short link if we implement the landing page.
    # User said: "send both links".
    
    # Let's send the direct links for now.
    # If we want a landing page (short_link), we can use that too.
    
    # Actually, sending two long links in SMS is bad.
    # Let's send the short_link for the landing page which has both buttons?
    # User rejected landing page idea initially but then accepted "send two links".
    # Sending two long links in one SMS might fail or cost 3-4 segments.
    # Short link is best.
    
    # But wait, user said: "send both links: one web, one telegram".
    # So we must send both.
    
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
        
    if inv.expires_at < datetime.utcnow():
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
        
    if inv.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invitation expired")
        
    return {
        "valid": True,
        "account_name": inv.account_name,
        "mobile_number": inv.mobile_number,
        "role": inv.role
    }
