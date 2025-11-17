# trading_bot/api/routers/commodities.py (کامل و اصلاح شده)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from typing import List

from core.db import get_db
from models.commodity import Commodity, CommodityAlias
from models.user import User
from .auth import verify_super_admin_or_dev_key
import schemas

router = APIRouter(
    prefix="/commodities",
    tags=["Commodities"],
    dependencies=[Depends(verify_super_admin_or_dev_key)]
)

@router.get("/", response_model=List[schemas.Commodity])
async def read_all_commodities(db: AsyncSession = Depends(get_db)):
    """
    دریافت لیست تمام کالاها به همراه نام‌های مستعار آن‌ها.
    """
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).order_by(Commodity.id)
    result = await db.execute(stmt)
    commodities = result.scalars().unique().all()
    return commodities

@router.get("/{commodity_id}", response_model=schemas.Commodity)
async def read_commodity(commodity_id: int, db: AsyncSession = Depends(get_db)):
    """
    دریافت یک کالای خاص با ID.
    """
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    result = await db.execute(stmt)
    commodity = result.scalars().unique().first()
    if not commodity:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    return commodity

@router.post("/", response_model=schemas.Commodity, status_code=status.HTTP_201_CREATED) # <--- ۱. اصلاح شد
async def create_commodity(
    commodity_data: schemas.CommodityCreate,
    aliases: List[str],
    db: AsyncSession = Depends(get_db)
):
    """
    ایجاد یک کالای جدید به همراه لیستی از نام‌های مستعار.
    """
    # بررسی تکراری بودن نام اصلی
    stmt = select(Commodity).where(Commodity.name == commodity_data.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"کالایی با نام '{commodity_data.name}' از قبل وجود دارد")

    db_commodity = Commodity(name=commodity_data.name)
    
    # افزودن نام‌های مستعار
    for alias_name in set(aliases): # استفاده از set برای جلوگیری از تکرار در ورودی
        db_commodity.aliases.append(CommodityAlias(alias=alias_name))
        
    db.add(db_commodity)
    await db.commit()
    await db.refresh(db_commodity, ['aliases'])
    return db_commodity

@router.put("/{commodity_id}", response_model=schemas.Commodity) # <--- ۲. اصلاح شد
async def update_commodity_name(
    commodity_id: int,
    commodity_update: schemas.CommodityCreate, # فقط نام را آپدیت می‌کنیم
    db: AsyncSession = Depends(get_db)
):
    """
    ویرایش نام اصلی یک کالا.
    """
    stmt = select(Commodity).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_commodity:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")

    # بررسی تکراری بودن نام جدید
    if commodity_update.name != db_commodity.name:
        stmt_check = select(Commodity).where(Commodity.name == commodity_update.name)
        existing = (await db.execute(stmt_check)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"کالایی با نام '{commodity_update.name}' از قبل وجود دارد")
    
    db_commodity.name = commodity_update.name
    await db.commit()
    await db.refresh(db_commodity)
    return db_commodity

@router.delete("/{commodity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_commodity(commodity_id: int, db: AsyncSession = Depends(get_db)):
    """
    حذف کامل یک کالا (به همراه تمام نام‌های مستعار آن).
    """
    stmt = select(Commodity).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_commodity:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    
    # (نام‌های مستعار به صورت خودکار به دلیل cascade delete حذف می‌شوند)
    await db.delete(db_commodity)
    await db.commit()
    return None

# --- مدیریت نام‌های مستعار (Aliases) ---

@router.post("/{commodity_id}/aliases", response_model=schemas.CommodityAlias, status_code=status.HTTP_201_CREATED)
async def add_alias_to_commodity(
    commodity_id: int,
    alias: schemas.CommodityAliasCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    افزودن یک نام مستعار جدید به کالای موجود.
    """
    # بررسی تکراری بودن نام مستعار در کل جدول
    stmt_check = select(CommodityAlias).where(CommodityAlias.alias == alias.alias)
    existing_alias = (await db.execute(stmt_check)).scalar_one_or_none()
    if existing_alias:
        raise HTTPException(status_code=409, detail=f"نام مستعار '{alias.alias}' از قبل برای کالای دیگری ثبت شده است")

    db_alias = CommodityAlias(commodity_id=commodity_id, alias=alias.alias)
    db.add(db_alias)
    
    try:
        await db.commit()
        await db.refresh(db_alias)
        return db_alias
    except Exception: # اگر commodity_id معتبر نباشد
        await db.rollback()
        raise HTTPException(status_code=404, detail="کالایی با این ID برای افزودن نام مستعار یافت نشد")


@router.put("/aliases/{alias_id}", response_model=schemas.CommodityAlias)
async def update_alias(
    alias_id: int,
    alias_update: schemas.CommodityAliasCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    ویرایش متن یک نام مستعار.
    """
    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")
        
    # بررسی تکراری بودن نام مستعار جدید
    if alias_update.alias != db_alias.alias:
        stmt_check = select(CommodityAlias).where(CommodityAlias.alias == alias_update.alias)
        existing = (await db.execute(stmt_check)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"نام مستعار '{alias_update.alias}' از قبل ثبت شده است")
            
    db_alias.alias = alias_update.alias
    await db.commit()
    await db.refresh(db_alias)
    return db_alias

@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(alias_id: int, db: AsyncSession = Depends(get_db)):
    """
    حذف یک نام مستعار.
    """
    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")
        
    await db.delete(db_alias)
    await db.commit()
    return None