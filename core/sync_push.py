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
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import httpx

logger = logging.getLogger(__name__)

# Global thread pool for non-blocking HTTP pushes
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sync_push")

# Persistent HTTP client (thread-safe)
_http_client = None
_client_lock = threading.Lock()
_cooldown_lock = threading.Lock()
_target_cooldowns: dict[str, float] = {}


def _direct_push_disabled() -> bool:
    return os.getenv("TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH", "").strip().lower() in {"1", "true", "yes", "on"}


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


def _get_cooldown_seconds() -> float:
    from core.config import settings

    try:
        return max(float(getattr(settings, "sync_direct_push_cooldown_seconds", 90.0)), 0.0)
    except (TypeError, ValueError):
        return 90.0


def _clear_target_cooldown(target_url: str) -> None:
    with _cooldown_lock:
        _target_cooldowns.pop(target_url, None)


def _mark_target_cooldown(target_url: str, reason: str) -> None:
    cooldown_seconds = _get_cooldown_seconds()
    if cooldown_seconds <= 0:
        return

    now = time.monotonic()
    cooldown_until = now + cooldown_seconds
    should_log = False

    with _cooldown_lock:
        existing_until = _target_cooldowns.get(target_url, 0.0)
        if existing_until <= now:
            should_log = True
        _target_cooldowns[target_url] = max(existing_until, cooldown_until)

    if should_log:
        logger.warning(
            "⚡ Direct push entering %.0fs cooldown for %s after %s",
            cooldown_seconds,
            target_url,
            reason,
        )


def _target_is_in_cooldown(target_url: str) -> bool:
    now = time.monotonic()
    with _cooldown_lock:
        cooldown_until = _target_cooldowns.get(target_url)
        if not cooldown_until:
            return False
        if cooldown_until <= now:
            _target_cooldowns.pop(target_url, None)
            return False
        return True


def _peer_response_is_success(response) -> bool:
    if getattr(response, "status_code", None) != 200:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        errors = int(payload.get("errors") or 0)
    except (TypeError, ValueError):
        return False
    return payload.get("status") in {"success", "ok"} and errors == 0


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

        if _peer_response_is_success(response):
            _clear_target_cooldown(target_url)
            logger.info(f"⚡ Direct push OK: {payload.get('table')}:{payload.get('id')}")
        else:
            _mark_target_cooldown(target_url, f"peer rejected status={response.status_code}")
            logger.warning(f"⚡ Direct push failed ({response.status_code}), sync_worker will retry")

    except Exception as e:
        _mark_target_cooldown(target_url, str(e))
        logger.warning(f"⚡ Direct push error: {e}, sync_worker will retry")


def push_sync_direct(payload: dict):
    """
    Submit a sync payload for direct HTTP push (non-blocking).
    This runs in a background thread so it doesn't block the DB transaction.
    Falls back to sync_worker retry if push fails.
    """
    if _direct_push_disabled():
        return

    from core.config import settings
    from core.server_routing import default_peer_server_url

    target_url = default_peer_server_url()
    api_key = getattr(settings, "sync_api_key", None)

    if not target_url or not api_key:
        return  # Not configured, sync_worker will handle

    if target_url.endswith("/"):
        target_url = target_url[:-1]

    if _target_is_in_cooldown(target_url):
        return

    try:
        _executor.submit(_do_push, payload, target_url, api_key)
    except Exception as e:
        logger.warning(f"⚡ Could not submit push task: {e}")
