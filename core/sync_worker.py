import asyncio
import json
import logging
import time
import hmac
import hashlib
import httpx
import redis.asyncio as redis
from core.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def send_sync_item(client: httpx.AsyncClient, item: dict, target_url: str, api_key: str):
    """Send item to target server with security headers"""
    timestamp = int(time.time())
    # Prepare payload as list (batch of 1)
    payload = [item]
    json_body = json.dumps(payload, sort_keys=True)
    
    # Create HMAC signature
    message = f"{timestamp}:{json_body}"
    signature = hmac.new(
        api_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    response = await client.post(
        f"{target_url}/api/sync/receive",
        content=json_body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "X-Timestamp": str(timestamp),
            "X-Signature": signature
        },
        timeout=10.0
    )
    return response

async def main():
    logger.info("🚀 Starting Sync Worker...")
    
    # Redis connection
    r = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port, 
        db=0,
        decode_responses=True
    )

    queue_name = "sync:outbound"
    retry_queue = "sync:retry"

    # Get configuration (handle potential missing attributes gracefully)
    target_url = getattr(settings, "foreign_server_url", None)
    api_key = getattr(settings, "sync_api_key", None)
    
    # Default URL normalization (remove trailing slash)
    if target_url and target_url.endswith("/"):
        target_url = target_url[:-1]
    
    if not target_url or not api_key:
        logger.warning(f"⚠️ Sync Worker not fully configured. URL={target_url}, API_Key={'***' if api_key else 'None'}")
    
    async with httpx.AsyncClient() as client:
        while True:
            try:
                # Wait for items in queue
                # BLPOP blocks until item is available
                res = await r.blpop([queue_name, retry_queue], timeout=5)
                
                if not res:
                    continue
                    
                origin_queue, payload = res
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logger.error(f"❌ Invalid JSON in queue: {payload}")
                    continue
                
                logger.info(f"📦 Processing item from {origin_queue}: {data.get('hash', 'unknown')}")
                
                if not target_url or not api_key:
                     logger.warning("Target URL or API Key missing. Re-queueing to retry queue and sleeping.")
                     await r.rpush(retry_queue, payload)
                     await asyncio.sleep(30)
                     continue

                try:
                    response = await send_sync_item(client, data, target_url, api_key)
                    
                    if response.status_code == 200:
                        logger.info("✅ Successfully synced item.")
                        # TODO: Update DB status (mark verified/synced)
                        # This requires async db session access
                    else:
                        logger.error(f"❌ Sync failed: {response.status_code} - {response.text}")
                        # Push to retry queue (at the end)
                        await r.rpush(retry_queue, payload)
                        await asyncio.sleep(1) # Backoff slightly
                        
                except httpx.RequestError as req_err:
                    logger.error(f"❌ Network error during sync: {req_err}")
                    await r.rpush(retry_queue, payload)
                    await asyncio.sleep(5) # Network retry backoff

            except Exception as e:
                logger.error(f"❌ Error in Sync Worker Loop: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
