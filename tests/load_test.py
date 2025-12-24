# tests/load_test.py
"""
تست فشار سنگین (Extreme Load Testing) - Trading Bot
سناریو: 4000 کاربر، 2 میلیون لفظ، 1 میلیون معامله در 5 دقیقه

ویژگی‌ها:
- استفاده از Bulk Insert برای سرعت بالا
- شبیه‌سازی Race Condition با Task های همزمان
- اعتبارسنجی دقیق خروجی‌ها (Valid vs Invalid)
- مدیریت زمان (Timeout 5 دقیقه)
"""

import asyncio
import random
import time
import argparse
import logging
import uuid
from datetime import datetime
from typing import List, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func, update, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, DBAPIError

# Fix for implicit cast issue if needed
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

from core.db import AsyncSessionLocal
from core.enums import UserRole
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity

# تنظیم لاگ - فقط Error/Info برای جلوگیری از کندی I/O
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("LoadTest")

class ExtremeLoadStats:
    def __init__(self):
        self.start_time = time.time()
        self.users_created = 0
        self.offers_created_valid = 0
        self.offers_created_invalid = 0  # Expected failures
        self.offers_failed_unexpected = 0 # Unexpected failures
        self.trades_success = 0
        self.trades_failed_race = 0      # Race condition failures (Expected)
        self.trades_failed_unexpected = 0
        self.expired_manual = 0
        self.expired_auto = 0
        self.lock = asyncio.Lock()

    def report(self):
        duration = time.time() - self.start_time
        return f"""
═══════════════════════════════════════════════════════════════
🚀 Extreme Load Test Results (Duration: {duration:.2f}s)
═══════════════════════════════════════════════════════════════
👥 Users:     {self.users_created:,}
📝 Offers:    {self.offers_created_valid:,} Valid | {self.offers_created_invalid:,} Invalid (Detected)
🤝 Trades:    {self.trades_success:,} Success | {self.trades_failed_race:,} Race Failures
☠️ Expired:   {self.expired_manual:,} Manual | {self.expired_auto:,} Auto
❌ Errors:    {self.offers_failed_unexpected:,} Offers | {self.trades_failed_unexpected:,} Trades (Unexpected)
⚡ Rate:      {(self.offers_created_valid + self.trades_success) / duration:.0f} ops/sec
═══════════════════════════════════════════════════════════════
"""

class ExtremeLoadTester:
    def __init__(self, target_users=4000, target_offers=2000000, target_trades=1000000, duration_min=5.0):
        self.target_users = target_users
        self.target_offers = target_offers
        self.target_trades = target_trades
        self.end_time = time.time() + (duration_min * 60)
        
        self.stats = ExtremeLoadStats()
        self.users: List[int] = [] # Only store IDs to save memory
        self.commodities: List[int] = []
        self.active_offer_ids: List[int] = [] 
        
    async def setup(self):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Commodity.id))
            self.commodities = result.scalars().all()
            if not self.commodities:
                raise ValueError("No commodities found in DB")
            logger.info(f"Loaded {len(self.commodities)} commodities")

    async def _create_users_batch(self, batch_size: int):
        async with AsyncSessionLocal() as session:
            users_data = []
            base_ts = int(time.time() * 1000000)
            for i in range(batch_size):
                uid = uuid.uuid4().hex[:8]
                mobile = f"09{random.randint(100000000, 999999999)}"
                
                users_data.append(User(
                    account_name=f"u_{base_ts}_{i}_{uid}",
                    mobile_number=mobile,
                    telegram_id=base_ts + i + random.randint(1, 10000000),
                    full_name=f"User {uid}",
                    address="Test Address",
                    role=UserRole.STANDARD,
                    has_bot_access=True
                ))
            
            session.add_all(users_data)
            try:
                await session.commit()
                # Store IDs
                for u in users_data:
                    self.users.append(u.id)
                self.stats.users_created += len(users_data)
            except IntegrityError:
                await session.rollback()
            except Exception as e:
                logger.error(f"User batch failed: {e}")

    async def _generate_offer_data(self, is_valid: bool) -> Tuple[Offer, str]:
        """Generate Offer object and expected outcome"""
        if not self.users:
            raise ValueError("No users available")
            
        user_id = random.choice(self.users)
        commodity_id = random.choice(self.commodities)
        
        if is_valid:
            # 70% Retail (Khord), 30% Wholesale
            is_wholesale = random.random() > 0.7
            quantity = random.randint(5, 50)
            
            # Logic for Lot Sizes
            lot_sizes = None
            if not is_wholesale and quantity >= 10:
                parts = random.randint(2, 3)
                if quantity >= parts * 5: # Ensure we can split
                    # Simple split logic
                    base = quantity // parts
                    rem = quantity % parts
                    lot_sizes = [base] * parts
                    lot_sizes[-1] += rem
            
            # 30% Notes
            notes = f"Test Note {uuid.uuid4().hex}" if random.random() < 0.3 else None
            
            offer = Offer(
                user_id=user_id,
                offer_type=random.choice([OfferType.BUY, OfferType.SELL]),
                commodity_id=commodity_id,
                quantity=quantity,
                remaining_quantity=quantity,
                price=random.randint(10000, 1000000),
                is_wholesale=is_wholesale,
                lot_sizes=lot_sizes,
                status=OfferStatus.ACTIVE,
                notes=notes
            )
            return offer, "success"
        else:
            # Generate Invalid Data
            error_type = random.choice(['qty_neg', 'price_zero', 'no_user'])
            
            if error_type == 'qty_neg':
                offer = Offer(user_id=user_id, commodity_id=commodity_id, quantity=-10, price=10000, offer_type=OfferType.BUY)
            elif error_type == 'price_zero':
                offer = Offer(user_id=user_id, commodity_id=commodity_id, quantity=10, price=0, offer_type=OfferType.BUY)
            else:
                offer = Offer(user_id=999999999, commodity_id=commodity_id, quantity=10, price=10000, offer_type=OfferType.BUY)
            
            return offer, "error"

    async def offers_worker(self):
        """Worker to continuously create offers"""
        batch_size = 500
        while time.time() < self.end_time and (self.stats.offers_created_valid + self.stats.offers_created_invalid) < self.target_offers:
            
            # Decide batch composition: 70% valid, 30% invalid
            valid_count = int(batch_size * 0.7)
            invalid_count = batch_size - valid_count
            
            offers_to_add = []
            
            async with AsyncSessionLocal() as session:
                # 1. Valid Moves (Bulk Insert)
                if self.users: # Ensure we have users
                    for _ in range(valid_count):
                        try:
                            offer_obj, _ = await self._generate_offer_data(True)
                            offers_to_add.append(offer_obj)
                        except Exception:
                            pass # Skip generation fail
                    
                    if offers_to_add:
                        session.add_all(offers_to_add)
                        try:
                            await session.commit()
                            # Capture IDs for trades
                            for o in offers_to_add:
                                self.active_offer_ids.append(o.id)
                            
                            self.stats.offers_created_valid += valid_count
                        except Exception as e:
                            self.stats.offers_failed_unexpected += valid_count
                            await session.rollback()

                    # 2. Invalid Moves (Must verify they FAIL)
                    for _ in range(invalid_count):
                        try:
                            async with session.begin_nested(): # Savepoint
                                offer_obj, _ = await self._generate_offer_data(False)
                                session.add(offer_obj)
                                await session.flush()
                            self.stats.offers_failed_unexpected += 1
                        except (IntegrityError, DBAPIError, Exception):
                            self.stats.offers_created_invalid += 1

            await asyncio.sleep(0.01)

    async def _execute_trade(self, session: AsyncSession, offer_id: int):
        """Try to execute a trade on an offer"""
        try:
            responder_id = random.choice(self.users)
            
            stmt = select(Offer).where(Offer.id == offer_id)
            result = await session.execute(stmt)
            offer = result.scalar_one_or_none()
            
            if not offer or offer.status != OfferStatus.ACTIVE:
                return "expired"

            trade_qty = offer.remaining_quantity
            if offer.lot_sizes:
                trade_qty = offer.lot_sizes[0]
            elif not offer.is_wholesale:
                trade_qty = min(5, offer.remaining_quantity)

            # Using SQL-side update for optimistic locking
            stmt = (
                update(Offer)
                .where(Offer.id == offer_id)
                .where(Offer.remaining_quantity >= trade_qty)
                .where(Offer.status == OfferStatus.ACTIVE)
                .values(
                    remaining_quantity=Offer.remaining_quantity - trade_qty,
                    # We avoid 'case' for status if possible or be careful with type
                )
                .returning(Offer.remaining_quantity)
                .execution_options(synchronize_session=False)
            )
            
            # Simple approach: Check remaining quantity AFTER update to decide status?
            # Or use a separate update for status if 0?
            
            # Let's try to update status in same query but using explicit values if possible
            # Or simplified: if remaining < qty, invalid (handled by where).
            # If remaining == qty, new status = COMPLETED.
            
            # Reverting 'case' logic issue:
            # Let's try separate updates to isolate failure.
            # First update Quantity.
            
            result = await session.execute(stmt)
            new_remaining = result.scalar_one_or_none()
            
            if new_remaining is not None:
                # Update successful. Check if completed.
                if new_remaining <= 0:
                     stmt_status = update(Offer).where(Offer.id == offer_id).values(status=OfferStatus.COMPLETED)
                     await session.execute(stmt_status)
                
                trade = Trade(
                    trade_number=random.randint(100000, 999999),
                    offer_id=offer_id,
                    offer_user_id=offer.user_id,
                    responder_user_id=responder_id,
                    commodity_id=offer.commodity_id,
                    trade_type=TradeType.BUY, # Simplified
                    quantity=trade_qty,
                    price=offer.price,
                    status=TradeStatus.COMPLETED
                )
                session.add(trade)
                await session.commit()
                return "success"
            else:
                await session.rollback()
                return "race_fail"

        except Exception as e:
            # Enhanced Logging
            logger.error(f"❌ Trade Error: {e}", exc_info=True)
            await session.rollback()
            return "error"

    async def trades_worker(self):
        """Worker to continuously execute trades"""
        while time.time() < self.end_time and self.stats.trades_success < self.target_trades:
            if self.stats.trades_failed_unexpected > 20: 
                 logger.error("🛑 Too many errors! Stopping trades worker.")
                 break
                 
            if not self.active_offer_ids or not self.users:
                await asyncio.sleep(0.1)
                continue
            
            try:
                offer_id = random.choice(self.active_offer_ids)
            except IndexError:
                continue
            
            async with AsyncSessionLocal() as session1, AsyncSessionLocal() as session2:
                res1, res2 = await asyncio.gather(
                    self._execute_trade(session1, offer_id),
                    self._execute_trade(session2, offer_id)
                )
                
                for r in [res1, res2]:
                    if r == "success": self.stats.trades_success += 1
                    elif r == "race_fail": self.stats.trades_failed_race += 1
                    elif r == "error": self.stats.trades_failed_unexpected += 1
            
            await asyncio.sleep(0.001)

    async def expiration_worker(self):
        while time.time() < self.end_time:
            if not self.active_offer_ids:
                await asyncio.sleep(1)
                continue
                
            async with AsyncSessionLocal() as session:
                try:
                    target_idx = random.randint(0, len(self.active_offer_ids)-1)
                    oid = self.active_offer_ids[target_idx]
                    
                    stmt = update(Offer).where(Offer.id == oid).values(status=OfferStatus.EXPIRED)
                    await session.execute(stmt)
                    await session.commit()
                    self.stats.expired_manual += 1
                except Exception:
                    pass
            
            await asyncio.sleep(0.5)

    async def run(self):
        logger.info(f"🔥 Starting Extreme Load Test (Goal: {self.target_offers} offers in {self.end_time - time.time():.0f}s)")
        await self.setup()
        
        logger.info("👥 Creating Users...")
        tasks = []
        batch_size = 100
        batches = max(1, self.target_users // batch_size)
        for _ in range(batches):
            tasks.append(self._create_users_batch(batch_size))
        await asyncio.gather(*tasks)
        logger.info(f"✅ Created {len(self.users)} users")
        
        if not self.users:
             logger.error("No users created! Aborting.")
             return

        logger.info("🚀 Launching Traffic Workers...")
        workers = []
        
        for _ in range(20):
            workers.append(asyncio.create_task(self.offers_worker()))
        for _ in range(50):
            workers.append(asyncio.create_task(self.trades_worker()))
        workers.append(asyncio.create_task(self.expiration_worker()))
        
        try:
            while time.time() < self.end_time:
                print(self.stats.report())
                if self.stats.offers_created_valid >= self.target_offers:
                    logger.info("✅ Target Offers Reached")
                    break
                # Only check every 1s for better responsiveness
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
        finally:
            for w in workers: w.cancel()
            
        print(self.stats.report())

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=4000)
    parser.add_argument("--offers", type=int, default=2000000)
    parser.add_argument("--trades", type=int, default=1000000)
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()

    tester = ExtremeLoadTester(
        target_users=args.users,
        target_offers=args.offers,
        target_trades=args.trades,
        duration_min=args.duration
    )
    await tester.run()

if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
