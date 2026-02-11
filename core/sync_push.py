# core/sync_push.py
"""
Direct HTTP push for sync data — fire-and-forget via thread pool.
Falls back to Redis queue (sync_worker) on failure.
"""
import json
import time
import hmac
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx

logger = logging.getLogger(__name__)

# Global thread pool for non-blocking HTTP pushes
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sync_push")

# Persistent HTTP client (thread-safe)
_http_client = None
_client_lock = threading.Lock()


def _get_client() -> httpx.Client:
    """Get or create persistent synchronous HTTP client"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        with _client_lock:
            if _http_client is None or _http_client.is_closed:
                _http_client = httpx.Client(
                    timeout=5.0,
                    http2=False,
                    verify=False,  # Allow self-signed certs or IP usage
                    limits=httpx.Limits(
                        max_connections=5,
                        max_keepalive_connections=2,
                        keepalive_expiry=30
                    )
                )
    return _http_client


def _do_push(payload: dict, target_url: str, api_key: str):
    """
    Synchronous HTTP push — runs in thread pool.
    On failure, data stays in Redis queue for sync_worker retry.
    """
    try:
        timestamp = int(time.time())
        items = [payload]
        json_body = json.dumps(items, sort_keys=True, default=str)

        message = f"{timestamp}:{json_body}"
        signature = hmac.new(
            api_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        client = _get_client()
        response = client.post(
            f"{target_url}/api/sync/receive",
            content=json_body,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "X-Timestamp": str(timestamp),
                "X-Signature": signature
            }
        )

        if response.status_code == 200:
            logger.info(f"⚡ Direct push OK: {payload.get('table')}:{payload.get('id')}")
        else:
            logger.warning(f"⚡ Direct push failed ({response.status_code}), sync_worker will retry")

    except Exception as e:
        logger.warning(f"⚡ Direct push error: {e}, sync_worker will retry")


def push_sync_direct(payload: dict):
    """
    Submit a sync payload for direct HTTP push (non-blocking).
    This runs in a background thread so it doesn't block the DB transaction.
    Falls back to sync_worker retry if push fails.
    """
    from core.config import settings

    target_url = getattr(settings, "foreign_server_url", None)
    api_key = getattr(settings, "sync_api_key", None)

    if not target_url or not api_key:
        return  # Not configured, sync_worker will handle

    if target_url.endswith("/"):
        target_url = target_url[:-1]

    try:
        _executor.submit(_do_push, payload, target_url, api_key)
    except Exception as e:
        logger.warning(f"⚡ Could not submit push task: {e}")
