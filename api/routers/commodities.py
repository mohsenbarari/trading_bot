# api/routers/commodities.py (نسخه نهایی با اندپوینت‌های مدیریت Alias)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
import re
from typing import List

from core.db import get_db
from models.commodity import Commodity, CommodityAlias # <-- CommodityAlias را import کنید
from models.user import User
from core.enums import UserRole
import schemas
from .auth import get_current_user_optional, verify_dev_key_optional

router = APIRouter(
    prefix="/commodities",
    tags=["Commodities"],
)

async def verify_super_admin_or_dev_key(
    current_user: User | None = Depends(get_current_user_optional),
    is_dev: bool = Depends(verify_dev_key_optional)
) -> User | None:
    # ... (کد این تابع بدون تغییر) ...
    if is_dev: return None
    if current_user and current_user.role == UserRole.SUPER_ADMIN: return current_user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated as Super Admin or with valid Dev Key")

def extract_duplicate_value(error: IntegrityError) -> str | None:
    # ... (کد این تابع بدون تغییر) ...
    detail = str(error.orig)
    match = re.search(r"\(([^)=]+)\)=\(([^)]+)\)", detail)
    if match:
        value = match.group(2)
        return value
    return None

# --- CRUD کالاها (بدون تغییر) ---

@router.post("/", response_model=schemas.CommodityRead, status_code=status.HTTP_201_CREATED)
async def create_commodity(
    commodity_in: schemas.CommodityCreate, db: AsyncSession = Depends(get_db), _ = Depends(verify_super_admin_or_dev_key)
):
    # ... (کد این تابع بدون تغییر) ...
    stmt_name_check = select(Commodity).where(Commodity.name == commodity_in.name)
    if (await db.execute(stmt_name_check)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"نام کالا **'{commodity_in.name}'** از قبل وجود دارد.")
    if commodity_in.aliases:
        stmt_alias_check = select(CommodityAlias).where(CommodityAlias.alias.in_(commodity_in.aliases))
        existing_aliases = (await db.execute(stmt_alias_check)).scalars().all()
        if existing_aliases:
            duplicate_aliases = [f"**'{a.alias}'**" for a in existing_aliases]
            raise HTTPException(status_code=400, detail=f"نام‌های مستعار زیر از قبل وجود دارند: {', '.join(duplicate_aliases)}")
    db_commodity = Commodity(name=commodity_in.name)
    db.add(db_commodity)
    for alias_str in commodity_in.aliases:
        db_alias = CommodityAlias(alias=alias_str, commodity=db_commodity)
        db.add(db_alias)
    try:
        await db.commit()
        await db.refresh(db_commodity, ["aliases"])
        return db_commodity
    except IntegrityError as e:
        await db.rollback()
        duplicate_value = extract_duplicate_value(e)
        error_detail = f"خطای یکپارچگی دیتابیس: مقدار تکراری."
        if duplicate_value:
             if 'commodities_name' in str(e.orig): error_detail = f"نام کالا **'{duplicate_value}'** از قبل وجود دارد."
             elif 'commodity_aliases_alias' in str(e.orig): error_detail = f"نام مستعار **'{duplicate_value}'** از قبل وجود دارد."
             else: error_detail = f"مقدار **'{duplicate_value}'** از قبل وجود دارد (خطای یکپارچگی)."
        raise HTTPException(status_code=400, detail=error_detail)
    except Exception as e: await db.rollback(); raise HTTPException(status_code=500, detail=f"خطای داخلی سرور هنگام ثبت کالا: {e}")

@router.get("/", response_model=List[schemas.CommodityRead])
async def read_commodities(
    skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db), _ = Depends(verify_super_admin_or_dev_key)
):
    # ... (کد این تابع بدون تغییر) ...
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).offset(skip).limit(limit)
    result = await db.execute(stmt)
    commodities = result.scalars().unique().all()
    return commodities

@router.get("/{commodity_id}", response_model=schemas.CommodityRead)
async def read_commodity(
    commodity_id: int, db: AsyncSession = Depends(get_db), _ = Depends(verify_super_admin_or_dev_key)
):
    # ... (کد این تابع بدون تغییر) ...
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None: raise HTTPException(status_code=404, detail="کالا یافت نشد")
    return db_commodity

@router.put("/{commodity_id}", response_model=schemas.CommodityRead)
async def update_commodity(
    commodity_id: int, commodity_in: schemas.CommodityUpdate, db: AsyncSession = Depends(get_db), _ = Depends(verify_super_admin_or_dev_key)
):
    # ... (کد این تابع بدون تغییر) ...
    # (این تابع همچنان برای ویرایش کلی کالا و همه alias ها با هم استفاده می‌شود)
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None: raise HTTPException(status_code=404, detail="کالا یافت نشد")
    is_modified = False
    if commodity_in.name is not None and commodity_in.name != db_commodity.name:
        stmt_name_check = select(Commodity.id).where(Commodity.name == commodity_in.name, Commodity.id != commodity_id)
        if (await db.execute(stmt_name_check)).scalar_one_or_none():
             raise HTTPException(status_code=400, detail=f"نام کالا **'{commodity_in.name}'** از قبل وجود دارد.")
        db_commodity.name = commodity_in.name
        is_modified = True
    if commodity_in.aliases is not None:
        new_aliases_set = set(commodity_in.aliases)
        current_aliases_set = {alias.alias for alias in db_commodity.aliases}
        aliases_to_add = new_aliases_set - current_aliases_set
        aliases_to_remove_objs = [alias for alias in db_commodity.aliases if alias.alias not in new_aliases_set]
        if aliases_to_add:
            stmt_alias_check = select(CommodityAlias).where(CommodityAlias.alias.in_(aliases_to_add), CommodityAlias.commodity_id != commodity_id)
            existing_aliases = (await db.execute(stmt_alias_check)).scalars().all()
            if existing_aliases:
                duplicate_aliases = [f"**'{a.alias}'**" for a in existing_aliases]
                raise HTTPException(status_code=400, detail=f"نام‌های مستعار زیر توسط کالاهای دیگر استفاده شده‌اند: {', '.join(duplicate_aliases)}")
        for alias_obj in aliases_to_remove_objs: db.delete(alias_obj); is_modified = True
        for alias_str in aliases_to_add: db_alias = CommodityAlias(alias=alias_str, commodity_id=db_commodity.id); db.add(db_alias); is_modified = True
    if is_modified:
         try:
             await db.commit()
             await db.refresh(db_commodity, ["aliases"])
         except IntegrityError as e:
             await db.rollback()
             duplicate_value = extract_duplicate_value(e)
             error_detail = f"خطای یکپارچگی دیتابیس: مقدار تکراری."
             if duplicate_value:
                  if 'commodities_name' in str(e.orig): error_detail = f"نام کالا **'{duplicate_value}'** از قبل وجود دارد."
                  elif 'commodity_aliases_alias' in str(e.orig): error_detail = f"نام مستعار **'{duplicate_value}'** از قبل وجود دارد."
                  else: error_detail = f"مقدار **'{duplicate_value}'** از قبل وجود دارد (خطای یکپارچگی)."
             raise HTTPException(status_code=400, detail=error_detail)
         except Exception as e: await db.rollback(); raise HTTPException(status_code=500, detail=f"خطای داخلی سرور هنگام ویرایش کالا: {e}")
    return db_commodity

@router.delete("/{commodity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_commodity(
    commodity_id: int, db: AsyncSession = Depends(get_db), _ = Depends(verify_super_admin_or_dev_key)
):
    # ... (کد این تابع بدون تغییر) ...
    stmt = select(Commodity).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    if db_commodity is None: raise HTTPException(status_code=404, detail="کالا یافت نشد")
    try:
        await db.delete(db_commodity)
        await db.commit()
    except Exception as e: await db.rollback(); raise HTTPException(status_code=400, detail=f"خطا در حذف کالا: {e}")
    return None

# === اندپوینت‌های جدید برای مدیریت تکی نام‌های مستعار ===

@router.post("/{commodity_id}/aliases", response_model=schemas.CommodityAliasRead, status_code=status.HTTP_201_CREATED)
async def add_alias_to_commodity(
    commodity_id: int,
    alias_in: schemas.CommodityAliasCreate, # (این اسکیما از قبل در schemas.py وجود دارد)
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_super_admin_or_dev_key)
):
    """افزودن یک نام مستعار جدید به یک کالای موجود."""
    # ابتدا کالا را پیدا کن
    db_commodity = (await db.execute(select(Commodity).where(Commodity.id == commodity_id))).scalar_one_or_none()
    if not db_commodity:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    
    # چک کن alias تکراری نباشد
    stmt_alias_check = select(CommodityAlias).where(CommodityAlias.alias == alias_in.alias)
    if (await db.execute(stmt_alias_check)).scalar_one_or_none():
         raise HTTPException(status_code=400, detail=f"نام مستعار **'{alias_in.alias}'** از قبل وجود دارد.")

    db_alias = CommodityAlias(alias=alias_in.alias, commodity_id=commodity_id)
    db.add(db_alias)
    try:
        await db.commit()
        await db.refresh(db_alias)
        return db_alias
    except IntegrityError as e: # برای اطمینان (race condition)
        await db.rollback()
        duplicate_value = extract_duplicate_value(e)
        raise HTTPException(status_code=400, detail=f"نام مستعار **'{duplicate_value or alias_in.alias}'** از قبل وجود دارد.")

@router.put("/aliases/{alias_id}", response_model=schemas.CommodityAliasRead)
async def update_alias(
    alias_id: int,
    alias_in: schemas.CommodityAliasUpdate, # <-- اسکیما جدید
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_super_admin_or_dev_key)
):
    """ویرایش متن یک نام مستعار."""
    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")

    if db_alias.alias == alias_in.alias: # تغییری نکرده است
        return db_alias

    # چک کردن تکراری نبودن نام جدید
    stmt_check = select(CommodityAlias.id).where(CommodityAlias.alias == alias_in.alias, CommodityAlias.id != alias_id)
    if (await db.execute(stmt_check)).scalar_one_or_none():
         raise HTTPException(status_code=400, detail=f"نام مستعار **'{alias_in.alias}'** از قبل وجود دارد.")

    db_alias.alias = alias_in.alias
    try:
        await db.commit()
        await db.refresh(db_alias)
        return db_alias
    except IntegrityError as e: # برای اطمینان (race condition)
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"نام مستعار **'{alias_in.alias}'** از قبل وجود دارد.")

@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(
    alias_id: int,
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_super_admin_or_dev_key)
):
    """حذف یک نام مستعار."""
    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")
    
    await db.delete(db_alias)
    await db.commit()
    return None # No content response