from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from models.user import User
from api.routers.auth import get_current_user
import schemas

router = APIRouter(
    prefix="/users-public",
    tags=["Public Users"],
    dependencies=[Depends(get_current_user)]
)

@router.get("/{user_id}", response_model=schemas.UserPublicRead)
async def read_public_user(user_id: int, db: AsyncSession = Depends(get_db)):
    """دریافت اطلاعات عمومی یک کاربر (قابل دسترسی برای همه کاربران لاگین شده)"""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
