# (فایل جدید) api/routers/commodities.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from typing import List

from core.db import get_db
from models.commodity import Commodity, CommodityAlias
from models.user import User
from core.enums import UserRole
import schemas
from .auth import get_current_user # برای بررسی نقش کاربر

router = APIRouter(
    prefix="/commodities",
    tags=["Commodities"],
    # فقط مدیر ارشد به این اندپوینت‌ها دسترسی دارد
    # dependencies=[Depends(get_current_user)] # فعلا کامنت می‌کنیم تا نقش را داخل هر تابع چک کنیم
)

# --- Dependency Function for Super Admin Check ---
async def verify_super_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted for this user role."
        )
    return current_user

# --- CRUD Operations ---

@router.post("/", response_model=schemas.CommodityRead, status_code=status.HTTP_201_CREATED)
async def create_commodity(
    commodity_in: schemas.CommodityCreate, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin) # اطمینان از نقش مدیر ارشد
):
    """ایجاد یک کالای جدید به همراه نام‌های مستعار آن."""
    # چک کردن اینکه نام کالا تکراری نباشد
    stmt_name = select(Commodity).where(Commodity.name == commodity_in.name)
    if (await db.execute(stmt_name)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Commodity with name '{commodity_in.name}' already exists.")

    # چک کردن اینکه نام‌های مستعار تکراری نباشند (در کل جدول aliases)
    if commodity_in.aliases:
        stmt_alias = select(CommodityAlias).where(CommodityAlias.alias.in_(commodity_in.aliases))
        existing_aliases = (await db.execute(stmt_alias)).scalars().all()
        if existing_aliases:
            raise HTTPException(status_code=400, detail=f"Aliases already exist: {[a.alias for a in existing_aliases]}")

    db_commodity = Commodity(name=commodity_in.name)
    db.add(db_commodity)
    await db.flush() # برای گرفتن ID کالا قبل از کامیت

    # افزودن نام‌های مستعار
    for alias_str in commodity_in.aliases:
        db_alias = CommodityAlias(alias=alias_str, commodity_id=db_commodity.id)
        db.add(db_alias)
        
    await db.commit()
    await db.refresh(db_commodity, ["aliases"]) # رفرش برای بارگیری aliases
    return db_commodity

@router.get("/", response_model=List[schemas.CommodityRead])
async def read_commodities(
    skip: int = 0, 
    limit: int = 100, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin) # اطمینان از نقش مدیر ارشد
):
    """خواندن لیست کالاها به همراه نام‌های مستعار."""
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).offset(skip).limit(limit)
    result = await db.execute(stmt)
    commodities = result.scalars().unique().all() # unique() برای جلوگیری از تکرار به خاطر join
    return commodities

@router.get("/{commodity_id}", response_model=schemas.CommodityRead)
async def read_commodity(
    commodity_id: int, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin) # اطمینان از نقش مدیر ارشد
):
    """خواندن اطلاعات یک کالای خاص."""
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None:
        raise HTTPException(status_code=404, detail="Commodity not found")
    return db_commodity

@router.put("/{commodity_id}", response_model=schemas.CommodityRead)
async def update_commodity(
    commodity_id: int, 
    commodity_in: schemas.CommodityUpdate, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin) # اطمینان از نقش مدیر ارشد
):
    """به‌روزرسانی نام و/یا نام‌های مستعار یک کالا."""
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None:
        raise HTTPException(status_code=404, detail="Commodity not found")

    # به‌روزرسانی نام (اگر داده شده و متفاوت است)
    if commodity_in.name is not None and commodity_in.name != db_commodity.name:
        # چک کردن تکراری نبودن نام جدید
        stmt_name = select(Commodity).where(Commodity.name == commodity_in.name, Commodity.id != commodity_id)
        if (await db.execute(stmt_name)).scalar_one_or_none():
             raise HTTPException(status_code=400, detail=f"Commodity with name '{commodity_in.name}' already exists.")
        db_commodity.name = commodity_in.name

    # به‌روزرسانی نام‌های مستعار (اگر داده شده)
    if commodity_in.aliases is not None:
        new_aliases_set = set(commodity_in.aliases)
        current_aliases_set = {alias.alias for alias in db_commodity.aliases}
        
        # چک کردن تکراری نبودن نام‌های مستعار جدید (در بین کالاهای دیگر)
        aliases_to_check = new_aliases_set - current_aliases_set
        if aliases_to_check:
            stmt_alias = select(CommodityAlias).where(CommodityAlias.alias.in_(aliases_to_check), CommodityAlias.commodity_id != commodity_id)
            existing_aliases = (await db.execute(stmt_alias)).scalars().all()
            if existing_aliases:
                raise HTTPException(status_code=400, detail=f"Aliases already used by other commodities: {[a.alias for a in existing_aliases]}")

        # حذف نام‌های مستعاری که دیگر در لیست جدید نیستند
        for alias_obj in list(db_commodity.aliases): # Iterate over a copy
             if alias_obj.alias not in new_aliases_set:
                 await db.delete(alias_obj)
                 # db_commodity.aliases.remove(alias_obj) # لازم نیست با cascade

        # افزودن نام‌های مستعار جدید
        aliases_to_add = new_aliases_set - current_aliases_set
        for alias_str in aliases_to_add:
             db_alias = CommodityAlias(alias=alias_str, commodity_id=db_commodity.id)
             db.add(db_alias)
             # db_commodity.aliases.append(db_alias) # لازم نیست با back_populates

    if db.is_modified(db_commodity) or commodity_in.aliases is not None:
         await db.commit()
         await db.refresh(db_commodity, ["aliases"])
         
    return db_commodity

@router.delete("/{commodity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_commodity(
    commodity_id: int, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin) # اطمینان از نقش مدیر ارشد
):
    """حذف یک کالا (نام‌های مستعار آن هم به صورت خودکار حذف می‌شوند)."""
    stmt = select(Commodity).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None:
        raise HTTPException(status_code=404, detail="Commodity not found")
        
    await db.delete(db_commodity)
    await db.commit()
    return None # No content response