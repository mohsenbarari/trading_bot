# api/routers/auth.py
import random
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete
from core.db import get_db
from core.config import settings
from core.security import create_access_token
from models.user import User
from models.invitation import Invitation
from models.session import UserSession, Platform
import schemas

router = APIRouter(prefix="/auth", tags=["Authentication"])

# In-memory storage for OTPs. For production, use Redis.
otp_storage = {}

@router.post("/request-otp", status_code=200)
async def request_otp(request: schemas.OTPRequest, db: AsyncSession = Depends(get_db)):
    # Find user by mobile number via the invitation table
    inv_stmt = select(Invitation).where(Invitation.mobile_number == request.mobile_number)
    invitation = (await db.execute(inv_stmt)).scalar_one_or_none()

    if not invitation:
        raise HTTPException(status_code=404, detail="Mobile number not found.")

    user_stmt = select(User).where(User.telegram_id == invitation.telegram_id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User associated with this mobile number not found.")

    otp = str(random.randint(100000, 999999))
    otp_storage[user.telegram_id] = otp  # Store OTP

    # Send OTP via Telegram Bot API
    bot_token = settings.bot_token
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": user.telegram_id,
        "text": f"کد ورود شما: {otp}"
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

    return {"message": "OTP sent to your Telegram account."}


@router.post("/verify-otp", response_model=schemas.Token)
async def verify_otp(request: schemas.OTPVerify, db: AsyncSession = Depends(get_db)):
    inv_stmt = select(Invitation).where(Invitation.mobile_number == request.mobile_number)
    invitation = (await db.execute(inv_stmt)).scalar_one_or_none()

    if not invitation:
        raise HTTPException(status_code=404, detail="Mobile number not found.")

    user_stmt = select(User).where(User.telegram_id == invitation.telegram_id)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    
    if not user or otp_storage.get(user.telegram_id) != request.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP or user.")
    
    del otp_storage[user.telegram_id] # Clean up OTP

    # Delete any existing session for this user on the same platform
    delete_stmt = delete(UserSession).where(
        and_(UserSession.user_id == user.id, UserSession.platform == request.platform)
    )
    await db.execute(delete_stmt)

    # Create new session
    new_session = UserSession(
        user_id=user.id,
        platform=request.platform,
        device_fingerprint=request.device_fingerprint
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)

    access_token = create_access_token(data={"sub": str(user.id), "session_id": new_session.id})
    return {"access_token": access_token, "token_type": "bearer"}