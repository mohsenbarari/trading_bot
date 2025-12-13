#!/usr/bin/env python3
"""اسکریپت ایجاد داده‌های فیک برای تست"""

import asyncio
import random
from datetime import datetime, timedelta
from sqlalchemy import select
from core.db import AsyncSessionLocal
from models.user import User
from models.commodity import Commodity
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus


async def seed_fake_data():
    """ایجاد داده‌های فیک"""
    
    async with AsyncSessionLocal() as session:
        # دریافت کاربران موجود
        users_result = await session.execute(select(User))
        users = users_result.scalars().all()
        
        if len(users) < 2:
            print("❌ حداقل 2 کاربر باید در سیستم وجود داشته باشد!")
            return
        
        print(f"✅ {len(users)} کاربر یافت شد")
        
        # دریافت کالاهای موجود
        commodities_result = await session.execute(select(Commodity))
        commodities = commodities_result.scalars().all()
        
        if not commodities:
            print("❌ کالایی در سیستم وجود ندارد!")
            return
        
        print(f"✅ {len(commodities)} کالا یافت شد")
        
        # ایجاد لفظ‌های فیک
        print("\n📝 ایجاد لفظ‌های فیک...")
        offers_created = 0
        
        for _ in range(50):  # 50 لفظ فیک
            user = random.choice(users)
            commodity = random.choice(commodities)
            
            offer = Offer(
                user_id=user.id,
                offer_type=random.choice([OfferType.BUY, OfferType.SELL]),
                commodity_id=commodity.id,
                quantity=random.choice([5, 10, 15, 20, 25, 30, 40, 50]),
                price=random.randint(50000, 100000),
                status=random.choice([OfferStatus.ACTIVE, OfferStatus.COMPLETED, OfferStatus.EXPIRED]),
                channel_message_id=random.randint(1000, 99999),
                created_at=datetime.utcnow() - timedelta(
                    days=random.randint(0, 180),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59)
                )
            )
            session.add(offer)
            offers_created += 1
        
        await session.commit()
        print(f"   ✅ {offers_created} لفظ ایجاد شد")
        
        # دریافت لفظ‌های ایجاد شده
        offers_result = await session.execute(select(Offer))
        offers = offers_result.scalars().all()
        
        # ایجاد معاملات فیک
        print("\n🤝 ایجاد معاملات فیک...")
        trades_created = 0
        
        for offer in offers:
            # 60% شانس ایجاد معامله برای هر لفظ
            if random.random() < 0.6:
                # انتخاب کاربر پاسخ‌دهنده (غیر از لفظ‌دهنده)
                other_users = [u for u in users if u.id != offer.user_id]
                if not other_users:
                    continue
                    
                responder = random.choice(other_users)
                
                # نوع معامله از دید پاسخ‌دهنده (برعکس لفظ‌دهنده)
                if offer.offer_type == OfferType.BUY:
                    trade_type = TradeType.SELL  # پاسخ‌دهنده می‌فروشد
                else:
                    trade_type = TradeType.BUY   # پاسخ‌دهنده می‌خرد
                
                trade = Trade(
                    offer_id=offer.id,
                    offer_user_id=offer.user_id,
                    responder_user_id=responder.id,
                    commodity_id=offer.commodity_id,
                    trade_type=trade_type,
                    quantity=offer.quantity,
                    price=offer.price,
                    status=random.choice([TradeStatus.PENDING, TradeStatus.CONFIRMED, TradeStatus.COMPLETED]),
                    created_at=offer.created_at + timedelta(
                        hours=random.randint(1, 24),
                        minutes=random.randint(0, 59)
                    )
                )
                session.add(trade)
                trades_created += 1
        
        await session.commit()
        print(f"   ✅ {trades_created} معامله ایجاد شد")
        
        # آمار نهایی
        print("\n" + "="*50)
        print("📊 آمار نهایی:")
        print(f"   👥 کاربران: {len(users)}")
        print(f"   📦 کالاها: {len(commodities)}")
        print(f"   📜 لفظ‌ها: {offers_created}")
        print(f"   🤝 معاملات: {trades_created}")
        print("="*50)


if __name__ == "__main__":
    asyncio.run(seed_fake_data())
