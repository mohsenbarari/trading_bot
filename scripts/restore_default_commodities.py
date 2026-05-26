import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import select

from bot.utils.redis_helpers import invalidate_commodity_cache
from core.commodity_defaults import IMAM_COMMODITY_ALIASES, IMAM_COMMODITY_NAME
from core.db import AsyncSessionLocal
from models.commodity import Commodity, CommodityAlias


async def ensure_default_commodities(db) -> dict:
    commodity = (await db.execute(
        select(Commodity).where(Commodity.name == IMAM_COMMODITY_NAME)
    )).scalar_one_or_none()

    commodity_created = False
    if commodity is None:
        commodity = Commodity(name=IMAM_COMMODITY_NAME)
        db.add(commodity)
        await db.flush()
        commodity_created = True

    alias_rows = (await db.execute(
        select(CommodityAlias).where(CommodityAlias.alias.in_(IMAM_COMMODITY_ALIASES))
    )).scalars().all()
    alias_by_name = {alias.alias: alias for alias in alias_rows}

    aliases_added: list[str] = []
    aliases_existing: list[str] = []
    aliases_conflicted: list[str] = []

    for alias_name in IMAM_COMMODITY_ALIASES:
        existing_alias = alias_by_name.get(alias_name)
        if existing_alias is None:
            db.add(CommodityAlias(alias=alias_name, commodity_id=commodity.id))
            aliases_added.append(alias_name)
            continue
        if existing_alias.commodity_id == commodity.id:
            aliases_existing.append(alias_name)
            continue
        aliases_conflicted.append(alias_name)

    await db.commit()

    return {
        "commodity_id": commodity.id,
        "commodity_created": commodity_created,
        "aliases_added": aliases_added,
        "aliases_existing": aliases_existing,
        "aliases_conflicted": aliases_conflicted,
    }


async def main() -> int:
    try:
        async with AsyncSessionLocal() as db:
            stats = await ensure_default_commodities(db)
    except Exception as exc:
        print(f"❌ خطا در بازسازی کالاهای پیش فرض: {exc}")
        return 1

    print(f"✅ کالای پیش فرض امام آماده است (ID: {stats['commodity_id']})")
    if stats["commodity_created"]:
        print("   - commodity: created")
    else:
        print("   - commodity: already present")

    if stats["aliases_added"]:
        print(f"   - aliases added: {', '.join(stats['aliases_added'])}")
    if stats["aliases_existing"]:
        print(f"   - aliases already present: {', '.join(stats['aliases_existing'])}")
    if stats["aliases_conflicted"]:
        print(
            "   - aliases skipped (already attached to another commodity): "
            + ", ".join(stats["aliases_conflicted"])
        )

    try:
        await invalidate_commodity_cache()
        print("   - commodity cache invalidated")
    except Exception as exc:
        print(f"⚠️ پاکسازی cache کالاها ناموفق بود: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))