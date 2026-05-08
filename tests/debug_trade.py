"""Manual non-regression tool for debugging a single trade execution path.

This script is intentionally retained for operator-driven debugging and is not part
of the automated unittest discovery flow.
"""

# tests/debug_trade.py
import asyncio
import logging
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from core.db import AsyncSessionLocal
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity
from core.enums import UserRole

# Config logging to stdout
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger("DebugTrade")

async def debug_single_trade():
    print("🚀 Starting Debug Trade...")
    async with AsyncSessionLocal() as session:
        # 1. Create a Commodity if needed
        res = await session.execute(select(Commodity).limit(1))
        commodity = res.scalar_one_or_none()
        if not commodity:
            print("Creating dummy commodity...")
            commodity = Commodity(name="Gold", unit="kg")
            session.add(commodity)
            await session.flush()
        
        # Commit commodity to prevent rollback loss
        await session.commit()
        # Refresh or fetch commodity again (or just use its ID which we know)
        # Assuming single commodity test environment
        res = await session.execute(select(Commodity).limit(1))
        commodity = res.scalar_one()
        comm_id = commodity.id
        print(f"Using Commodity ID: {comm_id}")

        # 2. Create User
        buyer = User(
            account_name="buyer_debug",
            mobile_number="09111111111",
            telegram_id=123,
            role=UserRole.STANDARD,
            full_name="Debug Buyer",
            address="Debug Address",
            has_bot_access=True
        )
        seller = User(
            account_name="seller_debug",
            mobile_number="09222222222",
            telegram_id=456,
            role=UserRole.STANDARD,
            full_name="Debug Seller",
            address="Debug Address",
            has_bot_access=True
        )
        session.add_all([buyer, seller])
        try:
            await session.flush()
            await session.commit()
            print("Users created.")
        except Exception:
            await session.rollback()
            # Try fetch existing
            res = await session.execute(select(User).where(User.mobile_number=="09222222222"))
            seller = res.scalar_one()
            res = await session.execute(select(User).where(User.mobile_number=="09111111111"))
            buyer = res.scalar_one()
            print(f"Users found: {seller.id}, {buyer.id}")

            # Ensure we have valid IDs and they are attached
            # (Fetching them attaches them)

        # 3. Create Offer
        print("Creating Offer...")
        offer = Offer(
            user_id=seller.id,
            offer_type=OfferType.SELL,
            commodity_id=comm_id,
            quantity=10,
            remaining_quantity=10,
            price=1000,
            is_wholesale=False,
            status=OfferStatus.ACTIVE
        )
        session.add(offer)
        await session.flush()
        print(f"Offer created: ID={offer.id}")
        
        await session.commit() 
        # New session for trade
    
    print("Executing Trade Logic...")
    async with AsyncSessionLocal() as session:
        try:
            offer_id = offer.id
            trade_qty = 5
            
            print(f"Attempting to update verify Offer {offer_id}...")
            
            stmt = (
                update(Offer)
                .where(Offer.id == offer_id)
                .where(Offer.remaining_quantity >= trade_qty)
                .where(Offer.status == OfferStatus.ACTIVE)
                .values(
                    remaining_quantity=Offer.remaining_quantity - trade_qty,
                )
                .returning(Offer.remaining_quantity)
                .execution_options(synchronize_session=False)
            )
            
            print("Running execute...")
            result = await session.execute(stmt)
            print("Execute done. Fetching scalar...")
            new_remaining = result.scalar_one_or_none()
            print(f"New remaining: {new_remaining}")
            
            if new_remaining is not None:
                print("Update Success!")
                # Create Trade
                trade = Trade(
                    trade_number=random.randint(1000,9999),
                    offer_id=offer_id,
                    offer_user_id=seller.id,
                    responder_user_id=buyer.id,
                    commodity_id=comm_id,
                    trade_type=TradeType.BUY,
                    quantity=trade_qty,
                    price=1000,
                    status=TradeStatus.COMPLETED
                )
                session.add(trade)
                await session.commit()
                print("Trade Committed!")
            else:
                print("Rowcount 0. Update failed (Predicate missed).")
        
        except Exception as e:
            print(f"❌ EXCEPTION CAPTURED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(debug_single_trade())
