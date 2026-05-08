
"""Manual non-regression tool for live API/Telegram simulation flows.

This script is intentionally retained for operator-driven end-to-end debugging and
is not part of the automated unittest discovery flow.
"""

import asyncio
import os
import sys
import httpx
import random
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from core.db import AsyncSessionLocal
from core.security import create_access_token
from core.enums import UserRole
from models.user import User
from models.commodity import Commodity

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("LiveSim")

API_URL = "http://localhost:8000/api"

async def get_or_create_user(session, mobile, name, telegram_id):
    result = await session.execute(select(User).where(User.mobile_number == mobile))
    user = result.scalar_one_or_none()
    
    if not user:
        user = User(
            account_name=name,
            mobile_number=mobile,
            telegram_id=telegram_id,
            role=UserRole.STANDARD,
            full_name=name,
            address="Simulation Address",
            has_bot_access=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info(f"Created User: {name}")
    else:
        logger.info(f"Found User: {name}")
    
    # Ensure role/access
    if user.role != UserRole.STANDARD:
        user.role = UserRole.STANDARD
        session.add(user)
        await session.commit()
    
    return user

async def main():
    logger.info("🚀 Starting Live Simulation (API + Telegram Integration)...")
    
    async with AsyncSessionLocal() as session:
        # 1. Setup Users
        seller = await get_or_create_user(session, "09990001111", "SimSeller", 11111)
        buyer = await get_or_create_user(session, "09990002222", "SimBuyer", 22222)
        
        # 2. Setup Commodity
        res = await session.execute(select(Commodity).limit(1))
        commodity = res.scalar_one_or_none()
        if not commodity:
            logger.error("No commodities found! create data first.")
            return
        
        comm_id = commodity.id
        logger.info(f"Using Commodity: {commodity.name} (ID: {comm_id})")
        
        # 3. Generate Tokens (HACK: API expects sub to be telegram_id)
        seller_token = create_access_token({"sub": str(seller.telegram_id), "telegram_id": seller.telegram_id})
        buyer_token = create_access_token({"sub": str(buyer.telegram_id), "telegram_id": buyer.telegram_id})
        
        seller_headers = {"Authorization": f"Bearer {seller_token}"}
        buyer_headers = {"Authorization": f"Bearer {buyer_token}"}
    
    # 4. Loop Simulation
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(1, 4): # Run 3 cycles
            logger.info(f"\n--- Cycle {i}/3 ---")
            
            # A. Create Offer (Seller)
            offer_payload = {
                "offer_type": "sell",
                "commodity_id": comm_id,
                "quantity": 10,
                "price": 1000 + (i*100),
                "is_wholesale": True,
                "notes": f"Simulation Offer #{i}"
            }
            
            logger.info("📤 Creating Offer via API...")
            resp = await client.post(f"{API_URL}/offers/", json=offer_payload, headers=seller_headers)
            
            if resp.status_code == 201:
                offer_data = resp.json()
                offer_id = offer_data["id"]
                msg_id = offer_data.get("channel_message_id")
                logger.info(f"✅ Offer Created! ID: {offer_id}")
                if msg_id:
                     logger.info(f"📢 Published to Telegram Channel. MsgID: {msg_id}")
                else:
                     logger.warning("⚠️ No channel_message_id returned (Check Bot Token/Log).")
            else:
                logger.error(f"❌ Failed to create offer: {resp.text}")
                continue
            
            # Wait for user to see it in channel
            logger.info("⏳ Waiting 3 seconds...")
            await asyncio.sleep(3)
            
            # B. Execute Trade (Buyer)
            trade_payload = {
                "offer_id": offer_id,
                "quantity": 5
            }
            
            logger.info("🤝 Executing Trade via API...")
            resp = await client.post(f"{API_URL}/trades/", json=trade_payload, headers=buyer_headers)
            
            if resp.status_code == 201:
                trade_data = resp.json()
                logger.info(f"✅ Trade Executed! ID: {trade_data['id']}")
                logger.info("📢 Channel message should update now.")
            else:
                logger.error(f"❌ Failed to execute trade: {resp.text}")
            
            await asyncio.sleep(2)

    logger.info("\n✅ Simulation Complete.")

if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
