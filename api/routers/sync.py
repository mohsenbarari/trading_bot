from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from models.change_log import ChangeLog
from core.config import settings
import hmac
import hashlib
import time
import json
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Table processing order: dependencies first
TABLE_ORDER = {"users": 0, "user_blocks": 1, "commodities": 2, "commodity_aliases": 3, "trading_settings": 4, "offers": 5, "trades": 6}

async def verify_signature(request: Request):
    """Verify HMAC signature and timestamp"""
    api_key = request.headers.get("X-API-Key")
    timestamp = request.headers.get("X-Timestamp")
    signature = request.headers.get("X-Signature")
    
    if not api_key or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing authentication headers")
        
    
    if api_key != settings.sync_api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    # Check timestamp (max 5 minutes drift)
    try:
        ts = int(timestamp)
        now = int(time.time())
        if abs(now - ts) > 300:
            raise HTTPException(status_code=401, detail="Request expired")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")
        
    # Verify signature
    body = await request.body()
    try:
        body_str = body.decode()
        message = f"{timestamp}:{body_str}"
        
        expected_signature = hmac.new(
            settings.sync_api_key.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if signature != expected_signature:
             raise HTTPException(status_code=401, detail="Invalid signature")
             
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        raise HTTPException(status_code=401, detail="Verification failed")

from sqlalchemy import insert, update, delete, select, text as sa_text
from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from models.user import User
from models.offer import Offer
from models.trade import Trade
from models.commodity import Commodity, CommodityAlias
from models.trading_setting import TradingSetting
from models.user_block import UserBlock
from datetime import datetime

# Counter fields that should use MAX logic (greatest value wins) to avoid losing increments
USER_COUNTER_FIELDS = {"trades_count", "commodities_traded_count", "channel_messages_count"}

# Natural unique keys per table (used for fallback when ID-based upsert hits name conflict)
NATURAL_KEYS = {
    "commodities": "name",
    "commodity_aliases": "alias",
    "users": "telegram_id",
    "trades": "trade_number",
}

def get_model_class(table_name: str):
    mapping = {
        "users": User,
        "offers": Offer,
        "trades": Trade,
        "commodities": Commodity,
        "commodity_aliases": CommodityAlias,
        "trading_settings": TradingSetting,
        "user_blocks": UserBlock
    }
    return mapping.get(table_name)


def _build_upsert_stmt(model, table, data):
    """Build the INSERT ON CONFLICT statement for a given model and data."""
    stmt = pg_insert(model).values(**data)

    if table == "users":
        set_dict = {}
        for k in data:
            if k in USER_COUNTER_FIELDS:
                set_dict[k] = sa_func.greatest(getattr(model, k), stmt.excluded[k])
            else:
                set_dict[k] = stmt.excluded[k]
        return stmt.on_conflict_do_update(index_elements=['id'], set_=set_dict)
    else:
        return stmt.on_conflict_do_update(index_elements=['id'], set_=data)


async def _apply_item(db: AsyncSession, table: str, operation: str, record_id, data: dict, model, new_offers: list):
    """
    Apply a single sync item using SAVEPOINT so failures don't kill the transaction.
    Handles:
      1. Normal upsert by ID
      2. UniqueViolation fallback → update by natural key
      3. ForeignKeyViolation → returns 'deferred' for retry
    Returns: 'ok', 'deferred', or 'error'
    """
    if table == "trading_settings":
        setting_key = data.get('key')
        if not setting_key:
            logger.warning(f"Skipping trading_setting sync without key: {data}")
            return 'error'
        stmt = pg_insert(model).values(**data)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_=data)
        async with db.begin_nested():
            await db.execute(stmt, execution_options={"is_sync": True})
        return 'ok'

    if operation in ("INSERT", "UPDATE"):
        data['id'] = record_id

        # Never overwrite channel_message_id from sync — it's set locally by channel-send
        if table == "offers":
            data.pop("channel_message_id", None)
            if operation == "INSERT" and settings.server_mode != "iran":
                new_offers.append(record_id)

        stmt = _build_upsert_stmt(model, table, data)

        try:
            async with db.begin_nested():
                await db.execute(stmt, execution_options={"is_sync": True})
            return 'ok'
        except IntegrityError as e:
            err_str = str(e).lower()

            # --- Case A: Unique violation on natural key (e.g. same name, different ID) ---
            if "unique" in err_str or "duplicate key" in err_str:
                natural_key = NATURAL_KEYS.get(table)
                natural_value = data.get(natural_key) if natural_key else None

                if natural_value is not None:
                    try:
                        # Update existing record by natural key
                        natural_col = getattr(model, natural_key)
                        update_data = {k: v for k, v in data.items() if k != 'id' and k != natural_key}
                        if update_data:
                            async with db.begin_nested():
                                stmt_update = (
                                    update(model)
                                    .where(natural_col == natural_value)
                                    .values(**update_data)
                                )
                                await db.execute(stmt_update, execution_options={"is_sync": True})
                        logger.info(f"🔀 Sync merged by {natural_key}='{natural_value}': {table}:{record_id}")
                        return 'ok'
                    except Exception as merge_err:
                        logger.error(f"Failed to merge {table}:{record_id} by {natural_key}: {merge_err}")
                        return 'error'
                else:
                    logger.error(f"UniqueViolation on {table}:{record_id} but no natural key to merge: {e}")
                    return 'error'

            # --- Case B: FK violation (parent not yet synced) → defer for retry ---
            elif "foreign key" in err_str:
                logger.warning(f"⏳ FK violation for {table}:{record_id}, deferring for retry")
                return 'deferred'

            else:
                logger.error(f"IntegrityError on {table}:{record_id}: {e}")
                return 'error'

    elif operation == "DELETE":
        try:
            async with db.begin_nested():
                stmt = delete(model).where(model.id == record_id)
                await db.execute(stmt, execution_options={"is_sync": True})
            return 'ok'
        except IntegrityError as e:
            logger.error(f"Cannot delete {table}:{record_id} (FK dependency): {e}")
            return 'error'

    return 'error'


def _parse_item(item: dict):
    """Parse a sync item: extract table, operation, model, data, record_id."""
    table = item.get('table')
    operation = item.get('operation')
    data = item.get('data')
    record_id = item.get('id')

    model = get_model_class(table)
    if not model:
        return None

    if isinstance(data, str):
        data = json.loads(data)

    # Parse datetime fields
    for key, value in list(data.items()):
        if isinstance(value, str) and key.endswith('_at'):
            try:
                data[key] = datetime.fromisoformat(value)
            except ValueError:
                pass

    return table, operation, model, data, record_id


@router.post("/receive")
async def receive_sync_data(
    items: list[dict], 
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ = Depends(verify_signature)
):
    """Receive sync data from other server"""
    logger.info(f"Received sync batch with {len(items)} items")
    
    # Sort items by dependency order: users first, then commodities, then offers, then trades
    sorted_items = sorted(items, key=lambda x: TABLE_ORDER.get(x.get('table', ''), 99))
    
    processed_count = 0
    errors = []
    deferred_items = []
    new_offers = []

    try:
        # --- Pass 1: Process all items ---
        for item in sorted_items:
            # Handle Notification Relay
            if item.get("type") == "notification":
                try:
                    from core.notifications import send_telegram_message
                    chat_id = item.get("chat_id")
                    text = item.get("text")
                    parse_mode = item.get("parse_mode", "Markdown")
                    if chat_id and text:
                        await send_telegram_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                        processed_count += 1
                        logger.info(f"✅ Notification relayed to {chat_id}")
                except Exception as e:
                    logger.error(f"Failed to relay notification: {e}")
                continue

            parsed = _parse_item(item)
            if not parsed:
                logger.warning(f"Unknown table in sync: {item.get('table')}")
                continue

            table, operation, model, data, record_id = parsed

            try:
                result = await _apply_item(db, table, operation, record_id, data, model, new_offers)
                if result == 'ok':
                    processed_count += 1
                    logger.info(f"✅ Sync Item Applied: {table}:{record_id} ({operation})")
                elif result == 'deferred':
                    deferred_items.append((table, operation, model, data, record_id))
                else:
                    errors.append(f"{table}:{record_id}")
            except Exception as e:
                logger.error(f"Unexpected error on {table}:{record_id}: {e}")
                errors.append(f"{table}:{record_id}")

        # --- Pass 2: Retry deferred items (FK violations) ---
        if deferred_items:
            logger.info(f"🔄 Retrying {len(deferred_items)} deferred items...")
            for table, operation, model, data, record_id in deferred_items:
                try:
                    result = await _apply_item(db, table, operation, record_id, data, model, new_offers)
                    if result == 'ok':
                        processed_count += 1
                        logger.info(f"✅ Deferred item applied: {table}:{record_id}")
                    else:
                        errors.append(f"{table}:{record_id} (deferred)")
                        logger.error(f"Deferred item still failed: {table}:{record_id}")
                except Exception as e:
                    logger.error(f"Deferred retry error {table}:{record_id}: {e}")
                    errors.append(f"{table}:{record_id}")

        await db.commit()

        # --- Fix sequences after sync to avoid ID collision ---
        # Synced records use explicit IDs which don't advance PostgreSQL sequences.
        # Without this fix, next local INSERT may try an ID that already exists.
        items_tables = {i.get('table') for i in sorted_items}
        SEQUENCE_MAP = {
            "users": ("users_id_seq", "users"),
            "offers": ("offers_id_seq", "offers"),
            "trades": ("trades_id_seq", "trades"),
            "commodities": ("commodities_id_seq", "commodities"),
            "commodity_aliases": ("commodity_aliases_id_seq", "commodity_aliases"),
            "user_blocks": ("user_blocks_id_seq", "user_blocks"),
        }
        for tbl_name in items_tables:
            seq_info = SEQUENCE_MAP.get(tbl_name)
            if seq_info:
                seq_name, real_table = seq_info
                try:
                    await db.execute(
                        sa_text(f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM {real_table}), 1))")
                    )
                    logger.info(f"🔢 Sequence {seq_name} synced to MAX(id)")
                except Exception as seq_err:
                    logger.warning(f"⚠️ Failed to fix sequence {seq_name}: {seq_err}")
        await db.commit()

        # Refresh caches for affected tables

        if "trading_settings" in items_tables:
            try:
                from core.trading_settings import refresh_settings_cache_async
                await refresh_settings_cache_async()
                logger.info("🔄 Trading settings cache refreshed")
            except Exception as e:
                 logger.error(f"Failed to refresh settings cache: {e}")

        if items_tables & {"commodities", "commodity_aliases"}:
            try:
                from core.cache import invalidate_commodities_cache
                await invalidate_commodities_cache()
                logger.info("🔄 Commodities cache invalidated after sync")
            except Exception as e:
                logger.error(f"Failed to invalidate commodities cache: {e}")
            # Also invalidate bot's commodity cache
            try:
                from bot.utils.redis_helpers import invalidate_commodity_cache
                await invalidate_commodity_cache()
            except Exception:
                pass
        
        # --- Handle Offer Publishing on Foreign Server ---
        # Uses SELECT FOR UPDATE SKIP LOCKED to prevent duplicate sends
        # (same sync item may arrive via both direct-push and sync_worker)
        if settings.server_mode != "iran" and new_offers:
             try:
                 from sqlalchemy.orm import selectinload
                 from models.offer import OfferStatus
                 from api.routers.offers import send_offer_to_channel

                 # Deduplicate offer IDs
                 unique_offer_ids = list(set(new_offers))
                 logger.info(f"📋 Channel publish candidates: {unique_offer_ids}")

                 for oid in unique_offer_ids:
                     try:
                         # Each offer in its own mini-transaction with row lock
                         async with db.begin_nested():
                             stmt = select(Offer).options(
                                 selectinload(Offer.user), 
                                 selectinload(Offer.commodity)
                             ).where(
                                 Offer.id == oid, 
                                 Offer.status == OfferStatus.ACTIVE,
                                 Offer.channel_message_id.is_(None)
                             ).with_for_update(skip_locked=True)
                             result = await db.execute(stmt)
                             offer = result.scalars().first()
                             
                             if offer:
                                 msg_id = await send_offer_to_channel(offer, offer.user)
                                 if msg_id:
                                     offer.channel_message_id = msg_id
                                     logger.info(f"📣 Published synced offer {offer.id} to Telegram. MsgID: {msg_id}")
                                 else:
                                     logger.warning(f"⚠️ send_offer_to_channel returned None for offer {oid}")
                             else:
                                 logger.info(f"⏭️ Offer {oid} already published or locked by another request")
                     except Exception as e:
                         logger.error(f"Failed to publish synced offer {oid}: {e}")
                 
                 await db.commit()
                     
             except ImportError:
                 logger.error("Could not import send_offer_to_channel")
             except Exception as e:
                 logger.error(f"Error publishing synced offers: {e}")
        
        if errors:
            return {"status": "partial", "processed": processed_count, "errors": len(errors)}
        return {"status": "success", "processed": processed_count}
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error processing sync batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resync")
async def resync_from_changelog(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
    table_filter: str = None,
):
    """
    Resync unsynced change_log entries to the target server.
    Requires Dev API Key (X-Dev-Api-Key header).
    Optional: ?table_filter=users&limit=200
    """
    dev_key = request.headers.get("X-Dev-Api-Key")
    if dev_key != settings.dev_api_key:
        raise HTTPException(status_code=403, detail="Dev API Key required")

    target_url = getattr(settings, "foreign_server_url", None)
    api_key = getattr(settings, "sync_api_key", None)

    if not target_url or not api_key:
        raise HTTPException(status_code=500, detail="Sync not configured (FOREIGN_SERVER_URL or SYNC_API_KEY missing)")

    if target_url.endswith("/"):
        target_url = target_url[:-1]

    # Read unsynced change_log entries
    query = select(ChangeLog).where(ChangeLog.synced == False).order_by(ChangeLog.id)
    if table_filter:
        query = query.where(ChangeLog.table_name == table_filter)
    query = query.limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    if not entries:
        return {"status": "ok", "message": "No unsynced entries found", "processed": 0}

    import httpx as httpx_mod

    processed = 0
    errors = 0
    batch_size = 50  # Send items in batches for efficiency

    async with httpx_mod.AsyncClient(timeout=30.0, verify=False) as client:
        # Group entries into batches
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            items = []
            for entry in batch:
                try:
                    data = json.loads(entry.data) if isinstance(entry.data, str) else entry.data
                    items.append({
                        "type": "db_change",
                        "operation": entry.operation,
                        "table": entry.table_name,
                        "id": entry.record_id,
                        "data": data,
                        "hash": entry.hash,
                        "timestamp": entry.timestamp.timestamp() if entry.timestamp else time.time()
                    })
                except Exception as e:
                    logger.error(f"Resync parse error for {entry.table_name}:{entry.record_id}: {e}")
                    errors += 1

            if not items:
                continue

            try:
                json_body = json.dumps(items, sort_keys=True, default=str)
                ts = int(time.time())
                message = f"{ts}:{json_body}"
                signature = hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()

                response = await client.post(
                    f"{target_url}/api/sync/receive",
                    content=json_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": api_key,
                        "X-Timestamp": str(ts),
                        "X-Signature": signature
                    }
                )

                if response.status_code == 200:
                    for entry in batch:
                        entry.synced = True
                    processed += len(batch)
                else:
                    logger.warning(f"Resync batch failed: {response.status_code} - {response.text[:200]}")
                    errors += len(batch)

            except Exception as e:
                logger.error(f"Resync batch error: {e}")
                errors += len(batch)

    await db.commit()
    return {"status": "ok", "processed": processed, "errors": errors, "total_entries": len(entries)}
