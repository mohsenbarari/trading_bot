import asyncio
import json
import logging
import time
import hmac
import hashlib
import httpx
import redis.asyncio as redis
from core.config import settings
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.logging_config import configure_logging
from core.server_routing import default_peer_server_url

# Configure logging
configure_logging("sync-worker")
logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


class SyncDeliveryError(Exception):
    def __init__(self, *, status_code: int, response_summary: dict):
        super().__init__(f"sync delivery rejected with status {status_code}")
        self.status_code = status_code
        self.response_summary = response_summary


def summarize_queue_payload(payload: str) -> dict:
    payload_bytes = payload.encode("utf-8", errors="replace")
    return {
        "payload_size_bytes": len(payload_bytes),
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest()[:16],
    }


def summarize_peer_response(response) -> dict:
    body = getattr(response, "text", "") or ""
    body_bytes = str(body).encode("utf-8", errors="replace")
    headers = getattr(response, "headers", {}) or {}
    return {
        "status_code": getattr(response, "status_code", None),
        "peer_response_size_bytes": len(body_bytes),
        "peer_response_sha256": hashlib.sha256(body_bytes).hexdigest()[:16] if body_bytes else None,
        "peer_content_type": headers.get("content-type") if hasattr(headers, "get") else None,
    }


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
    target_url = default_peer_server_url()
    api_key = getattr(settings, "sync_api_key", None)
    
    # Default URL normalization (remove trailing slash)
    if target_url and target_url.endswith("/"):
        target_url = target_url[:-1]
    
    if not target_url or not api_key:
        logger.warning(f"⚠️ Sync Worker not fully configured. URL={target_url}, API_Key={'***' if api_key else 'None'}")
    
    async with httpx.AsyncClient() as client:
        iteration = 0
        while True:
            iteration += 1
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
                    logger.error(
                        "❌ Invalid JSON in sync queue",
                        extra={
                            "event": "job.item.invalid_payload",
                            "job_name": "sync_worker",
                            "origin_queue": origin_queue,
                            **summarize_queue_payload(payload),
                        },
                    )
                    continue
                
                item_hash = data.get('hash', 'unknown')
                logger.info(
                    "📦 Processing sync item",
                    extra={
                        "event": "job.item.received",
                        "job_name": "sync_worker",
                        "origin_queue": origin_queue,
                        "sync_item_hash": item_hash,
                    },
                )
                
                if not target_url or not api_key:
                     logger.warning("Target URL or API Key missing. Re-queueing to retry queue and sleeping.")
                     await r.rpush(retry_queue, payload)
                     await asyncio.sleep(30)
                     continue

                try:
                    start_time = time.perf_counter()
                    run_id = None
                    with job_context("sync_worker", iteration=iteration, origin_queue=origin_queue, sync_item_hash=item_hash) as run_id:
                        response = await send_sync_item(client, data, target_url, api_key)
                    
                        if response.status_code == 200:
                            logger.info(
                                "✅ Sync item delivered to peer.",
                                extra={
                                    "event": "job.item.delivered",
                                    "job_name": "sync_worker",
                                    "run_id": run_id,
                                    "iteration": iteration,
                                    "status_code": response.status_code,
                                    "duration_ms": duration_ms_since(start_time),
                                },
                            )
                            # TODO: Update DB status (mark verified/synced)
                            # This requires async db session access
                        else:
                            raise SyncDeliveryError(
                                status_code=response.status_code,
                                response_summary=summarize_peer_response(response),
                            )

                except SyncDeliveryError as delivery_err:
                    logger.error(
                        "❌ Sync delivery rejected by peer",
                        extra={
                            "event": "job.item.failed",
                            "job_name": "sync_worker",
                            "run_id": run_id,
                            "iteration": iteration,
                            "duration_ms": duration_ms_since(start_time),
                            **delivery_err.response_summary,
                        },
                    )
                    # Push to retry queue (at the end)
                    await r.rpush(retry_queue, payload)
                    await asyncio.sleep(1) # Backoff slightly
                except httpx.RequestError as req_err:
                    _loop_errors.log(
                        logger,
                        "❌ Network error during sync: %s",
                        req_err,
                        job_name="sync_worker",
                        run_id=run_id,
                        metric_recorded=True,
                    )
                    await r.rpush(retry_queue, payload)
                    await asyncio.sleep(5) # Network retry backoff

            except Exception as e:
                _loop_errors.log(logger, "❌ Error in Sync Worker Loop: %s", e, job_name="sync_worker")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
