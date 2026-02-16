# trading_bot/api/routers/commodities.py

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from typing import List, Optional
from jose import jwt, JWTError
import logging

from core.db import get_db
from core.config import settings
from models.commodity import Commodity, CommodityAlias
from models.user import User
from api.deps import verify_super_admin_or_dev_key, oauth2_scheme

import schemas

# تنظیم لاگر
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/commodities",
    tags=["Commodities"]
    # توجه: dependency مدیر ارشد از سطح روتر حذف شد و به endpoint های خاص اضافه می‌شود
)

# --- 👇 تابع جدید برای تشخیص منبع درخواست 👇 ---
async def get_request_source(
    api_key: Optional[str] = Header(None, alias="x-api-key"),
    token: Optional[str] = Depends(oauth2_scheme)
) -> str:
    """
    منبع درخواست را تشخیص می‌دهد:
    - اگر x-api-key باشد -> 'bot'
    - اگر Token باشد -> مقدار source داخل توکن (مثلاً 'miniapp')
    """
    if api_key:
        return "bot"
    
    if token:
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            return payload.get("source", "miniapp") # پیش‌فرض miniapp
        except JWTError:
            return "unknown"
            
    return "unknown"
# -------------------------------------------------------

@router.get("/", response_model=List[schemas.Commodity])
async def read_all_commodities(db: AsyncSession = Depends(get_db)):
    """
    دریافت لیست تمام کالاها به همراه نام‌های مستعار آن‌ها.
    """
    # ===== Redis Cache Check =====
    from core.cache import get_cached_commodities, set_cached_commodities
    
    cached = await get_cached_commodities()
    if cached:
        return cached
    # =============================
    
    stmt = select(Commodity).options(selectinload(Commodity.aliases)).order_by(Commodity.id)
    result = await db.execute(stmt)
    commodities = result.scalars().unique().all()
    
    # ===== Cache Result =====
    # تبدیل به dict برای کش
    commodities_data = [
        {
            "id": c.id,
            "name": c.name,
            "aliases": [{"id": a.id, "alias": a.alias, "commodity_id": a.commodity_id} for a in c.aliases]
        }
        for c in commodities
    ]
    await set_cached_commodities(commodities_data)
    # ========================
    
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

@router.post("/", response_model=schemas.Commodity, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def create_commodity(
    commodity_data: schemas.CommodityCreate,
    aliases: List[str],
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    ایجاد یک کالای جدید به همراه لیستی از نام‌های مستعار.
    """
    logger.info(f"Creating commodity '{commodity_data.name}' via source: {source}") # لاگ کردن منبع

    # بررسی تکراری بودن نام اصلی
    stmt = select(Commodity).where(Commodity.name == commodity_data.name)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"کالایی با نام '{commodity_data.name}' از قبل وجود دارد")

    db_commodity = Commodity(name=commodity_data.name)
    
    # افزودن نام‌های مستعار
    for alias_name in set(aliases): 
        db_commodity.aliases.append(CommodityAlias(alias=alias_name))
        
    db.add(db_commodity)
    await db.commit()
    await db.refresh(db_commodity, ['aliases'])
    
    # پاک کردن cache کالاها
    try:
        from bot.utils.redis_helpers import invalidate_commodity_cache
        await invalidate_commodity_cache()
    except:
        pass
    
    return db_commodity

@router.put("/{commodity_id}", response_model=schemas.Commodity, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def update_commodity_name(
    commodity_id: int,
    commodity_update: schemas.CommodityCreate, 
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    ویرایش نام اصلی یک کالا.
    """
    logger.info(f"Updating commodity ID {commodity_id} name to '{commodity_update.name}' via source: {source}")

    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
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

    # پاک کردن cache کالاها
    try:
        from bot.utils.redis_helpers import invalidate_commodity_cache
        await invalidate_commodity_cache()
    except:
        pass

    return db_commodity

@router.delete("/{commodity_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def delete_commodity(
    commodity_id: int, 
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    حذف کامل یک کالا (به همراه تمام نام‌های مستعار آن).
    اگر لفظ فعالی برای این کالا وجود داشته باشد، حذف مجاز نیست.
    """
    logger.info(f"Deleting commodity ID {commodity_id} via source: {source}")

    stmt = select(Commodity).options(selectinload(Commodity.aliases)).where(Commodity.id == commodity_id)
    db_commodity = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_commodity:
        raise HTTPException(status_code=404, detail="کالا یافت نشد")
    
    # Check for active offers referencing this commodity
    from models.offer import Offer, OfferStatus
    offer_count_stmt = select(Offer).where(
        Offer.commodity_id == commodity_id,
        Offer.status == OfferStatus.ACTIVE
    )
    active_offers = (await db.execute(offer_count_stmt)).scalars().all()
    if active_offers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"امکان حذف این کالا وجود ندارد. {len(active_offers)} لفظ فعال برای این کالا وجود دارد. ابتدا لفظ‌ها را لغو یا حذف کنید."
        )

    # Delete aliases first (ORM events fire for each), then commodity
    for alias in list(db_commodity.aliases):
        await db.delete(alias)
    
    try:
        await db.delete(db_commodity)
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting commodity {commodity_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="امکان حذف این کالا وجود ندارد. لفظ یا معامله‌ای به این کالا متصل است."
        )

    # پاک کردن cache کالاها
    try:
        from bot.utils.redis_helpers import invalidate_commodity_cache
        await invalidate_commodity_cache()
    except:
        pass

    return None

# --- مدیریت نام‌های مستعار (Aliases) ---

@router.post("/{commodity_id}/aliases", response_model=schemas.CommodityAlias, status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def add_alias_to_commodity(
    commodity_id: int,
    alias: schemas.CommodityAliasCreate,
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    افزودن یک نام مستعار جدید به کالای موجود.
    """
    logger.info(f"Adding alias '{alias.alias}' to commodity ID {commodity_id} via source: {source}")

    stmt_check = select(CommodityAlias).where(CommodityAlias.alias == alias.alias)
    existing_alias = (await db.execute(stmt_check)).scalar_one_or_none()
    if existing_alias:
        raise HTTPException(status_code=409, detail=f"نام مستعار '{alias.alias}' از قبل برای کالای دیگری ثبت شده است")

    db_alias = CommodityAlias(commodity_id=commodity_id, alias=alias.alias)
    db.add(db_alias)
    
    try:
        await db.commit()
        await db.refresh(db_alias)

        # پاک کردن cache کالاها
        try:
            from bot.utils.redis_helpers import invalidate_commodity_cache
            await invalidate_commodity_cache()
        except:
            pass

        return db_alias
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=404, detail="کالایی با این ID برای افزودن نام مستعار یافت نشد")


@router.put("/aliases/{alias_id}", response_model=schemas.CommodityAlias, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def update_alias(
    alias_id: int,
    alias_update: schemas.CommodityAliasCreate,
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    ویرایش متن یک نام مستعار.
    """
    logger.info(f"Updating alias ID {alias_id} to '{alias_update.alias}' via source: {source}")

    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")
        
    if alias_update.alias != db_alias.alias:
        stmt_check = select(CommodityAlias).where(CommodityAlias.alias == alias_update.alias)
        existing = (await db.execute(stmt_check)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"نام مستعار '{alias_update.alias}' از قبل ثبت شده است")
            
    db_alias.alias = alias_update.alias
    await db.commit()
    await db.refresh(db_alias)

    # پاک کردن cache کالاها
    try:
        from bot.utils.redis_helpers import invalidate_commodity_cache
        await invalidate_commodity_cache()
    except:
        pass

    return db_alias

@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_super_admin_or_dev_key)])
async def delete_alias(
    alias_id: int, 
    db: AsyncSession = Depends(get_db),
    source: str = Depends(get_request_source) # 👈 دریافت منبع
):
    """
    حذف یک نام مستعار.
    """
    logger.info(f"Deleting alias ID {alias_id} via source: {source}")

    stmt = select(CommodityAlias).where(CommodityAlias.id == alias_id)
    db_alias = (await db.execute(stmt)).scalar_one_or_none()
    
    if not db_alias:
        raise HTTPException(status_code=404, detail="نام مستعار یافت نشد")
        
    await db.delete(db_alias)
    await db.commit()

    # پاک کردن cache کالاها
    try:
        from bot.utils.redis_helpers import invalidate_commodity_cache
        await invalidate_commodity_cache()
    except:
        pass

    return None