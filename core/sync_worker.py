import asyncio
import json
import logging
import time
import hmac
import hashlib
import httpx
import redis.asyncio as redis
from core.background_job_authority import JOB_SYNC_WORKER, assert_background_job_authority
from sqlalchemy import case, select, update
from core.config import settings
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.logging_config import configure_logging
from core.metrics import record_sync_terminal_policy_rejection
from core.server_routing import current_server, default_peer_server_url
from core.sync_authority import IRAN_AUTHORITATIVE_SYNC_TABLES
from core.sync_metadata import (
    build_sync_metadata,
    build_sync_public_identity,
    coerce_positive_int,
    deserialize_sync_data,
)
from core.sync_field_policy import sanitize_sync_payload
from core.sync_protocol import build_sync_protocol_metadata
from core.sync_transport import assert_runtime_sync_transport_allowed, runtime_sync_tls_verify_setting

# Configure logging
configure_logging("sync-worker")
logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)

TERMINAL_SOURCE_AUTHORITY_REJECTION_TABLES = IRAN_AUTHORITATIVE_SYNC_TABLES
SYNC_OUTBOUND_TABLE_PRIORITY = (
    "users",
    "invitations",
    "accountant_relations",
    "customer_relations",
    "telegram_link_tokens",
    "telegram_notification_outbox",
    "commodities",
    "commodity_aliases",
    "trading_settings",
    "market_schedule_overrides",
    "market_runtime_state",
    "offers",
    "trades",
    "offer_requests",
    "offer_publication_states",
    "trade_delivery_receipts",
    "telegram_admin_broadcasts",
    "telegram_admin_broadcast_receipts",
    "notifications",
    "user_blocks",
)


class SyncDeliveryError(Exception):
    def __init__(self, *, status_code: int, response_summary: dict):
        super().__init__(f"sync delivery rejected with status {status_code}")
        self.status_code = status_code
        self.response_summary = response_summary


class SyncDeliveryMarkerError(Exception):
    def __init__(self, *, error_type: str):
        super().__init__("sync delivery marker failed")
        self.error_type = error_type


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
    summary = {
        "status_code": getattr(response, "status_code", None),
        "peer_response_size_bytes": len(body_bytes),
        "peer_response_sha256": hashlib.sha256(body_bytes).hexdigest()[:16] if body_bytes else None,
        "peer_content_type": headers.get("content-type") if hasattr(headers, "get") else None,
    }
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        summary.update(
            {
                "peer_sync_status": payload.get("status"),
                "peer_sync_processed": payload.get("processed"),
                "peer_sync_errors": payload.get("errors"),
            }
        )
    return summary


def peer_response_is_success(response) -> bool:
    if getattr(response, "status_code", None) != 200:
        return False
    try:
        payload = response.json()
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        error_count = int(payload.get("errors") or 0)
    except (TypeError, ValueError):
        return False
    return payload.get("status") in {"success", "ok"} and error_count == 0


def _single_item_partial_rejection_detail(response) -> dict | None:
    if getattr(response, "status_code", None) != 200:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "partial":
        return None
    try:
        processed = int(payload.get("processed") or 0)
        errors = int(payload.get("errors") or 0)
    except (TypeError, ValueError):
        return None
    if processed != 0 or errors != 1:
        return None
    error_items = payload.get("error_items")
    if not isinstance(error_items, list) or len(error_items) != 1:
        return None
    error_item = error_items[0]
    if not isinstance(error_item, dict):
        return None
    return error_item


def _single_item_partial_rejection_reason(response) -> str | None:
    error_item = _single_item_partial_rejection_detail(response)
    if error_item is None:
        return None
    reason = error_item.get("reason")
    return reason if isinstance(reason, str) else None


def _terminal_policy_rejection_reason(error_item: dict | None) -> str | None:
    if not isinstance(error_item, dict):
        return None
    reason = error_item.get("reason")
    if not isinstance(reason, str):
        return None
    if reason == "policy_forbidden:no-sync":
        return reason
    table = error_item.get("table")
    if (
        reason.startswith("source_authority_forbidden:")
        and isinstance(table, str)
        and table in TERMINAL_SOURCE_AUTHORITY_REJECTION_TABLES
    ):
        return reason
    return None


def peer_response_is_terminal_policy_rejection(response) -> bool:
    """Return True when a single receiver rejection is intrinsically terminal."""
    return _terminal_policy_rejection_reason(_single_item_partial_rejection_detail(response)) is not None


def _sync_identity_value_matches(left, right) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def terminal_policy_rejection_detail_for_item(response, item: dict) -> dict | None:
    """Return terminal rejection detail only when it matches the outgoing item."""
    error_item = _single_item_partial_rejection_detail(response)
    reason = _terminal_policy_rejection_reason(error_item)
    if reason is None:
        return None
    if not _sync_identity_value_matches(error_item.get("table"), item.get("table")):
        return None
    if not _sync_identity_value_matches(error_item.get("record_id"), item.get("id")):
        return None
    return error_item


def peer_response_is_terminal_policy_rejection_for_item(response, item: dict) -> bool:
    return terminal_policy_rejection_detail_for_item(response, item) is not None


def peer_response_is_policy_forbidden_no_sync(response) -> bool:
    """Return True for a receiver-side no-sync rejection of a single item."""
    return _single_item_partial_rejection_reason(response) == "policy_forbidden:no-sync"


def deserialize_change_log_data(raw_data):
    return deserialize_sync_data(raw_data)


def change_log_entry_to_sync_item(entry) -> dict:
    timestamp = getattr(entry, "timestamp", None)
    data = sanitize_sync_payload(entry.table_name, deserialize_change_log_data(entry.data))
    item = {
        "type": "db_change",
        "operation": entry.operation,
        "table": entry.table_name,
        "id": entry.record_id,
        "data": data,
        "hash": entry.hash,
        "timestamp": timestamp.timestamp() if timestamp else time.time(),
        "change_log_id": entry.id,
        "sync_protocol": build_sync_protocol_metadata(),
        "sync_meta": build_sync_metadata(
            entry.table_name,
            entry.record_id,
            entry.operation,
            data,
            change_log_id=entry.id,
            source_server=current_server(),
        ),
    }
    public_identity = build_sync_public_identity(entry.table_name, entry.record_id, data)
    if public_identity is not None:
        item["public_identity"] = public_identity
    return item


async def fetch_next_unsynced_change_log_item() -> dict | None:
    """Read the oldest committed unsynced change_log row without relying on Redis wake-up."""
    from core.db import AsyncSessionLocal
    from models.change_log import ChangeLog

    table_priority = case(
        *[
            (ChangeLog.table_name == table_name, priority)
            for priority, table_name in enumerate(SYNC_OUTBOUND_TABLE_PRIORITY)
        ],
        else_=len(SYNC_OUTBOUND_TABLE_PRIORITY),
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeLog)
            .where(ChangeLog.synced.is_(False))
            .order_by(table_priority, ChangeLog.id.asc())
            .limit(1)
        )
        entry = result.scalars().first()
        if entry is None:
            return None
        return change_log_entry_to_sync_item(entry)


async def mark_change_log_delivered(item: dict) -> int:
    """Mark the local change_log row delivered only after the peer accepts it.

    New queue payloads include change_log_id, so the marker updates one exact row.
    Older queued payloads fall back to table/id/operation/hash matching.
    """
    from core.db import AsyncSessionLocal
    from models.change_log import ChangeLog

    change_log_id = coerce_positive_int(item.get("change_log_id"))
    async with AsyncSessionLocal() as db:
        conditions = [ChangeLog.synced.is_(False)]
        if change_log_id is not None:
            conditions.append(ChangeLog.id == change_log_id)
        else:
            record_id = coerce_positive_int(item.get("id"))
            table_name = item.get("table")
            operation = item.get("operation")
            item_hash = item.get("hash")
            if not table_name or not operation or record_id is None or not item_hash:
                return 0
            conditions.extend(
                [
                    ChangeLog.table_name == table_name,
                    ChangeLog.record_id == record_id,
                    ChangeLog.operation == operation,
                    ChangeLog.hash == item_hash,
                ]
            )

        result = await db.execute(
            update(ChangeLog)
            .where(*conditions)
            .values(synced=True, verified=True)
        )
        await db.commit()
        return int(result.rowcount or 0)


def queue_poll_order(iteration: int) -> list[str]:
    """Alternate queue priority so retry work cannot starve behind fresh outbound traffic."""
    if iteration % 2 == 0:
        return ["sync:retry", "sync:outbound"]
    return ["sync:outbound", "sync:retry"]


async def requeue_if_needed(
    redis_client,
    retry_queue: str,
    payload: str,
    *,
    should_requeue: bool,
) -> None:
    if should_requeue:
        await redis_client.rpush(retry_queue, payload)


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
    assert_background_job_authority(JOB_SYNC_WORKER)
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
    
    assert_runtime_sync_transport_allowed()
    async with httpx.AsyncClient(verify=runtime_sync_tls_verify_setting()) as client:
        iteration = 0
        while True:
            iteration += 1
            try:
                # Wait for queue wake-ups/retries. Fresh outbound queue entries
                # are treated only as wake-up signals; peer delivery must be
                # built from committed change_log rows.
                res = await r.blpop(queue_poll_order(iteration), timeout=5)
                
                should_requeue = True
                if not res:
                    data = await fetch_next_unsynced_change_log_item()
                    if data is None:
                        continue
                    origin_queue = "change_log"
                    payload = json.dumps(data, sort_keys=True, default=str)
                    should_requeue = False
                else:
                    origin_queue, payload = res
                    if origin_queue == queue_name:
                        data = await fetch_next_unsynced_change_log_item()
                        if data is None:
                            logger.info(
                                "Dropped outbound sync wake-up with no committed change_log row.",
                                extra={
                                    "event": "job.item.outbound_wakeup_no_committed_change",
                                    "job_name": "sync_worker",
                                    "origin_queue": origin_queue,
                                    **summarize_queue_payload(payload),
                                },
                            )
                            continue
                        origin_queue = "change_log"
                        payload = json.dumps(data, sort_keys=True, default=str)
                        should_requeue = False
                    else:
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

                payload = json.dumps(data, sort_keys=True, default=str)
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
                     logger.warning("Target URL or API Key missing. Keeping sync item pending and sleeping.")
                     await requeue_if_needed(r, retry_queue, payload, should_requeue=should_requeue)
                     await asyncio.sleep(30)
                     continue

                try:
                    start_time = time.perf_counter()
                    run_id = None
                    with job_context("sync_worker", iteration=iteration, origin_queue=origin_queue, sync_item_hash=item_hash) as run_id:
                        response = await send_sync_item(client, data, target_url, api_key)
                    
                        if peer_response_is_success(response):
                            try:
                                marked_count = await mark_change_log_delivered(data)
                            except Exception as marker_err:
                                raise SyncDeliveryMarkerError(error_type=type(marker_err).__name__) from marker_err
                            logger.info(
                                "✅ Sync item delivered to peer.",
                                extra={
                                    "event": "job.item.delivered",
                                    "job_name": "sync_worker",
                                    "run_id": run_id,
                                    "iteration": iteration,
                                    "status_code": response.status_code,
                                    "marked_change_logs": marked_count,
                                    "duration_ms": duration_ms_since(start_time),
                                },
                            )
                        elif terminal_rejection_detail := terminal_policy_rejection_detail_for_item(response, data):
                            try:
                                marked_count = await mark_change_log_delivered(data)
                            except Exception as marker_err:
                                raise SyncDeliveryMarkerError(error_type=type(marker_err).__name__) from marker_err
                            peer_rejection_reason = str(terminal_rejection_detail.get("reason") or "unknown")
                            record_sync_terminal_policy_rejection(
                                server_mode=current_server(),
                                table=str(data.get("table") or "unknown"),
                                reason=peer_rejection_reason,
                            )
                            logger.warning(
                                "Dropped terminal policy-rejected sync item.",
                                extra={
                                    "event": "job.item.dropped_terminal_policy_rejection",
                                    "job_name": "sync_worker",
                                    "run_id": run_id,
                                    "iteration": iteration,
                                    "table": data.get("table"),
                                    "record_id": data.get("id"),
                                    "peer_rejection_reason": peer_rejection_reason,
                                    "marked_change_logs": marked_count,
                                    "duration_ms": duration_ms_since(start_time),
                                },
                            )
                        else:
                            raise SyncDeliveryError(
                                status_code=response.status_code,
                                response_summary=summarize_peer_response(response),
                            )

                except SyncDeliveryMarkerError as marker_err:
                    logger.error(
                        "❌ Sync delivery marker failed after peer acceptance",
                        extra={
                            "event": "job.item.marker_failed",
                            "job_name": "sync_worker",
                            "run_id": run_id,
                            "iteration": iteration,
                            "duration_ms": duration_ms_since(start_time),
                            "sync_item_hash": item_hash,
                            "error_type": marker_err.error_type,
                        },
                    )
                    await requeue_if_needed(r, retry_queue, payload, should_requeue=should_requeue)
                    await asyncio.sleep(1)
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
                    await requeue_if_needed(r, retry_queue, payload, should_requeue=should_requeue)
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
                    await requeue_if_needed(r, retry_queue, payload, should_requeue=should_requeue)
                    await asyncio.sleep(5) # Network retry backoff

            except Exception as e:
                _loop_errors.log(logger, "❌ Error in Sync Worker Loop: %s", e, job_name="sync_worker")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
