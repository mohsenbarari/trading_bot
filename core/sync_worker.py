import asyncio
import json
import logging
import time
import hmac
import hashlib

import httpx
import redis.asyncio as redis

from core.config import settings
from core.logging_config import configure_logging
from core.server_routing import default_peer_server_url

configure_logging("sync_worker")
logger = logging.getLogger(__name__)


async def send_sync_item(client: httpx.AsyncClient, item: dict, target_url: str, api_key: str):
    """Send item to target server with security headers."""
    timestamp = int(time.time())
    # Prepare payload as list (batch of 1)
    payload = [item]
    json_body = json.dumps(payload, sort_keys=True, default=str)

    # Create HMAC signature
    message = f"{timestamp}:{json_body}"
    signature = hmac.new(
        api_key.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    response = await client.post(
        f"{target_url}/api/sync/receive",
        content=json_body,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "X-Timestamp": str(timestamp),
            "X-Signature": signature,
        },
        timeout=10.0,
    )
    return response


async def main():
    logger.info("Starting Sync Worker", extra={"event": "sync_worker.startup"})

    # Redis connection
    r = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=0,
        decode_responses=True,
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
        logger.warning(
            "Sync Worker not fully configured",
            extra={
                "event": "sync.config_missing",
                "target_url_configured": bool(target_url),
                "api_key_configured": bool(api_key),
            },
        )

    processed_since_summary = 0
    synced_since_summary = 0
    retried_since_summary = 0
    failed_since_summary = 0
    last_summary_at = time.monotonic()

    async with httpx.AsyncClient() as client:
        while True:
            try:
                # BLPOP blocks until item is available.
                res = await r.blpop([queue_name, retry_queue], timeout=5)

                now = time.monotonic()
                if now - last_summary_at >= 30 and processed_since_summary:
                    logger.info(
                        "Sync worker summary",
                        extra={
                            "event": "sync.summary",
                            "processed": processed_since_summary,
                            "synced": synced_since_summary,
                            "retried": retried_since_summary,
                            "failed": failed_since_summary,
                        },
                    )
                    processed_since_summary = 0
                    synced_since_summary = 0
                    retried_since_summary = 0
                    failed_since_summary = 0
                    last_summary_at = now

                if not res:
                    continue

                origin_queue, payload = res
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    failed_since_summary += 1
                    logger.error(
                        "Invalid JSON in sync queue",
                        extra={
                            "event": "sync.invalid_json",
                            "origin_queue": origin_queue,
                            "payload_size": len(payload) if isinstance(payload, str) else None,
                        },
                    )
                    continue

                if not isinstance(data, dict):
                    failed_since_summary += 1
                    logger.error(
                        "Unexpected sync payload type",
                        extra={"event": "sync.invalid_payload_type", "origin_queue": origin_queue, "payload_type": type(data).__name__},
                    )
                    continue

                processed_since_summary += 1
                logger.debug(
                    "Processing sync item",
                    extra={
                        "event": "sync.item.processing",
                        "origin_queue": origin_queue,
                        "table": data.get("table"),
                        "record_id": data.get("id"),
                        "hash": data.get("hash"),
                    },
                )

                if not target_url or not api_key:
                    retried_since_summary += 1
                    logger.warning(
                        "Sync target configuration missing; requeueing item",
                        extra={
                            "event": "sync.requeue.config_missing",
                            "origin_queue": origin_queue,
                            "table": data.get("table"),
                            "record_id": data.get("id"),
                            "hash": data.get("hash"),
                        },
                    )
                    await r.rpush(retry_queue, payload)
                    await asyncio.sleep(30)
                    continue

                try:
                    response = await send_sync_item(client, data, target_url, api_key)

                    if response.status_code == 200:
                        synced_since_summary += 1
                        logger.debug(
                            "Successfully synced item",
                            extra={
                                "event": "sync.item.synced",
                                "table": data.get("table"),
                                "record_id": data.get("id"),
                                "hash": data.get("hash"),
                            },
                        )
                        # TODO: Update DB status (mark verified/synced)
                        # This requires async db session access
                    else:
                        retried_since_summary += 1
                        logger.error(
                            "Sync request failed; item will be retried",
                            extra={
                                "event": "sync.item.failed",
                                "status_code": response.status_code,
                                "table": data.get("table"),
                                "record_id": data.get("id"),
                                "hash": data.get("hash"),
                                "response_size": len(response.text or ""),
                            },
                        )
                        await r.rpush(retry_queue, payload)
                        await asyncio.sleep(1)  # Backoff slightly

                except httpx.RequestError as req_err:
                    retried_since_summary += 1
                    logger.warning(
                        "Network error during sync; item will be retried",
                        extra={
                            "event": "sync.item.network_error",
                            "table": data.get("table"),
                            "record_id": data.get("id"),
                            "hash": data.get("hash"),
                            "error_type": type(req_err).__name__,
                        },
                    )
                    await r.rpush(retry_queue, payload)
                    await asyncio.sleep(5)  # Network retry backoff

            except Exception:
                failed_since_summary += 1
                logger.exception("Error in Sync Worker loop", extra={"event": "sync_worker.loop_error"})
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
