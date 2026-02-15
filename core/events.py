# core/events.py
"""
SQLAlchemy Event Listeners for Real-time Redis Publishing & Cross-Server Sync

This module automatically publishes Redis events when database models change,
enabling true real-time synchronization without manual publish calls.

Sync flow (optimized):
  1. Event fires → log_change() inserts into change_log (same DB transaction)
  2. log_change() pushes to Redis sync:outbound queue (persistent connection)
  3. log_change() fires direct HTTP push via thread pool (instant delivery)
  4. sync_worker only handles retries for failed direct pushes
"""
import json
import logging
from typing import Any, Dict
from sqlalchemy import event
from sqlalchemy.orm import Session
import redis.asyncio as redis
from core.redis import pool
from sqlalchemy.sql import text
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)

# ─── Persistent Redis connection for sync pushes ───
_sync_redis = None


def _get_sync_redis():
    """Get or create a persistent synchronous Redis connection"""
    global _sync_redis
    if _sync_redis is None:
        import redis as sync_redis
        from core.config import settings
        _sync_redis = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=0,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_keepalive=True,
            retry_on_timeout=True
        )
    return _sync_redis


def log_change(connection, table_name: str, record_id: int, operation: str, data: Dict[str, Any]):
    """Log change to change_log + push to Redis + fire direct HTTP push"""
    try:
        json_data = json.dumps(data, default=str)
        data_hash = hashlib.sha256(json_data.encode()).hexdigest()

        # 1. Insert into change_log (same transaction as the triggering change)
        sql = text("""
            INSERT INTO change_log (operation, table_name, record_id, data, timestamp, hash, synced, verified, created_at)
            VALUES (:op, :tbl, :rid, :data, :ts, :hash, false, false, NOW())
        """)
        connection.execute(sql, {
            "op": operation,
            "tbl": table_name,
            "rid": record_id,
            "data": json_data,
            "ts": datetime.utcnow(),
            "hash": data_hash
        })

        # Build sync payload
        payload = {
            "type": "db_change",
            "operation": operation,
            "table": table_name,
            "id": record_id,
            "data": data,
            "hash": data_hash,
            "timestamp": datetime.utcnow().timestamp()
        }
        payload_json = json.dumps(payload, default=str)

        # 2. Push to Redis queue (persistent connection — backup for sync_worker retry)
        try:
            r = _get_sync_redis()
            r.lpush("sync:outbound", payload_json)
        except Exception as re:
            logger.error(f"❌ Redis sync push failed: {re}")
            # Reset connection on error
            global _sync_redis
            _sync_redis = None

        # 3. Direct HTTP push via thread pool (instant delivery — non-blocking)
        try:
            from core.sync_push import push_sync_direct
            push_sync_direct(payload)
        except Exception as pe:
            logger.warning(f"⚡ Direct push submit failed: {pe}")

    except Exception as e:
        logger.error(f"❌ Error logging change for {table_name}:{record_id}: {e}")


def publish_event_sync(event_type: str, data: Dict[str, Any]) -> None:
    """Synchronous Redis publish for real-time UI updates via WebSocket/SSE"""
    try:
        r = _get_sync_redis()
        channel = f"events:{event_type}"
        payload = json.dumps(data)
        r.publish(channel, payload)
        logger.info(f"📡 Published event: {event_type}")
    except Exception as e:
        logger.error(f"❌ Error publishing event {event_type}: {e}")


def setup_offer_events():
    """Setup event listeners for Offer model"""
    from models.offer import Offer

    @event.listens_for(Offer, 'after_insert')
    def on_offer_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "version_id": target.version_id or 1,
                "user_id": target.user_id,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "original_lot_sizes": target.original_lot_sizes,
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "channel_message_id": target.channel_message_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            log_change(connection, "offers", target.id, "INSERT", data)
            publish_event_sync("offer:created", data)
        except Exception as e:
            logger.error(f"Error in offer after_insert event: {e}")

    @event.listens_for(Offer, 'after_update')
    def on_offer_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "version_id": target.version_id or 1,
                "user_id": target.user_id,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "original_lot_sizes": target.original_lot_sizes,
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "channel_message_id": target.channel_message_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
                "idempotency_key": target.idempotency_key,
                "archived": target.archived
            }
            log_change(connection, "offers", target.id, "UPDATE", data)
            if target.status.value == "expired":
                publish_event_sync("offer:expired", {"id": target.id})
            else:
                publish_event_sync("offer:updated", data)
        except Exception as e:
            logger.error(f"Error in offer after_update event: {e}")

    @event.listens_for(Offer, 'after_delete')
    def on_offer_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id}
            log_change(connection, "offers", target.id, "DELETE", data)
            publish_event_sync("offer:deleted", data)
        except Exception as e:
            logger.error(f"Error in offer after_delete event: {e}")

    logger.info("✅ Offer event listeners registered")


def setup_trade_events():
    """Setup event listeners for Trade model"""
    from models.trade import Trade

    @event.listens_for(Trade, 'after_insert')
    def on_trade_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            # Handle Enum or String
            trade_type_val = target.trade_type.value if hasattr(target.trade_type, 'value') else target.trade_type
            status_val = target.status.value if hasattr(target.status, 'value') else target.status

            data = {
                "id": target.id,
                "version_id": target.version_id or 1,
                "trade_number": target.trade_number,
                "offer_id": target.offer_id,
                "offer_user_id": target.offer_user_id,
                "offer_user_mobile": target.offer_user_mobile,
                "responder_user_id": target.responder_user_id,
                "responder_user_mobile": target.responder_user_mobile,
                "commodity_id": target.commodity_id,
                "trade_type": trade_type_val,
                "quantity": target.quantity,
                "price": target.price,
                "status": status_val,
                "note": target.note,
                "created_at": target.created_at.isoformat() if target.created_at else None,
                "confirmed_at": target.confirmed_at.isoformat() if target.confirmed_at else None,
                "completed_at": target.completed_at.isoformat() if target.completed_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
                "idempotency_key": target.idempotency_key,
                "archived": target.archived
            }
            log_change(connection, "trades", target.id, "INSERT", data)
        except Exception as e:
            logger.error(f"Error in trade after_insert event: {e}")

    @event.listens_for(Trade, 'after_update')
    def on_trade_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            trade_type_val = target.trade_type.value if hasattr(target.trade_type, 'value') else target.trade_type
            status_val = target.status.value if hasattr(target.status, 'value') else target.status

            data = {
                "id": target.id,
                "version_id": target.version_id or 1,
                "trade_number": target.trade_number,
                "offer_id": target.offer_id,
                "offer_user_id": target.offer_user_id,
                "offer_user_mobile": target.offer_user_mobile,
                "responder_user_id": target.responder_user_id,
                "responder_user_mobile": target.responder_user_mobile,
                "commodity_id": target.commodity_id,
                "trade_type": trade_type_val,
                "quantity": target.quantity,
                "price": target.price,
                "status": status_val,
                "note": target.note,
                "created_at": target.created_at.isoformat() if target.created_at else None,
                "confirmed_at": target.confirmed_at.isoformat() if target.confirmed_at else None,
                "completed_at": target.completed_at.isoformat() if target.completed_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
                "idempotency_key": target.idempotency_key,
                "archived": target.archived
            }
            log_change(connection, "trades", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in trade after_update event: {e}")

    logger.info("✅ Trade event listeners registered")


def setup_user_events():
    """Setup event listeners for User model"""
    from models.user import User

    @event.listens_for(User, 'after_insert')
    def on_user_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "telegram_id": target.telegram_id,
                "username": target.username,
                "full_name": target.full_name,
                "mobile_number": target.mobile_number,
                "account_name": target.account_name,
                "address": target.address,
                "role": target.role.value if target.role else None,
                "has_bot_access": target.has_bot_access,
                "is_deleted": target.is_deleted,
                "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
                "can_block_users": target.can_block_users,
                "max_blocked_users": target.max_blocked_users,
                "max_daily_trades": target.max_daily_trades,
                "max_active_commodities": target.max_active_commodities,
                "max_daily_requests": target.max_daily_requests,
                "trading_restricted_until": target.trading_restricted_until.isoformat() if target.trading_restricted_until else None,
                "limitations_expire_at": target.limitations_expire_at.isoformat() if target.limitations_expire_at else None,
                "trades_count": target.trades_count,
                "commodities_traded_count": target.commodities_traded_count,
                "channel_messages_count": target.channel_messages_count,
                "last_seen_at": target.last_seen_at.isoformat() if target.last_seen_at else None,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            log_change(connection, "users", target.id, "INSERT", data)
            logger.info(f"📡 Published sync: user:created ID={target.id}")
        except Exception as e:
            logger.error(f"Error in user after_insert event: {e}")

    @event.listens_for(User, 'after_update')
    def on_user_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "telegram_id": target.telegram_id,
                "username": target.username,
                "full_name": target.full_name,
                "mobile_number": target.mobile_number,
                "account_name": target.account_name,
                "address": target.address,
                "role": target.role.value if target.role else None,
                "has_bot_access": target.has_bot_access,
                "is_deleted": target.is_deleted,
                "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
                "can_block_users": target.can_block_users,
                "max_blocked_users": target.max_blocked_users,
                "max_daily_trades": target.max_daily_trades,
                "max_active_commodities": target.max_active_commodities,
                "max_daily_requests": target.max_daily_requests,
                "trading_restricted_until": target.trading_restricted_until.isoformat() if target.trading_restricted_until else None,
                "limitations_expire_at": target.limitations_expire_at.isoformat() if target.limitations_expire_at else None,
                "trades_count": target.trades_count,
                "commodities_traded_count": target.commodities_traded_count,
                "channel_messages_count": target.channel_messages_count,
                "last_seen_at": target.last_seen_at.isoformat() if target.last_seen_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
            }
            log_change(connection, "users", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in user after_update event: {e}")

    logger.info("✅ User event listeners registered")


def setup_commodity_events():
    """Setup event listeners for Commodity model"""
    from models.commodity import Commodity

    @event.listens_for(Commodity, 'after_insert')
    def on_commodity_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "name": target.name
            }
            log_change(connection, "commodities", target.id, "INSERT", data)
            # Invalidate cache
            try:
                from core.cache import invalidate_commodities_cache
                # We can't await here, so we rely on the sync worker or cache TTL
                # Or use a sync redis call if strictly necessary, but cache TTL (5min) is acceptable
            except:
                pass
        except Exception as e:
            logger.error(f"Error in commodity after_insert event: {e}")

    @event.listens_for(Commodity, 'after_update')
    def on_commodity_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "name": target.name
            }
            log_change(connection, "commodities", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in commodity after_update event: {e}")

    @event.listens_for(Commodity, 'after_delete')
    def on_commodity_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id}
            log_change(connection, "commodities", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in commodity after_delete event: {e}")

    logger.info("✅ Commodity event listeners registered")


def setup_commodity_alias_events():
    """Setup event listeners for CommodityAlias model"""
    from models.commodity import CommodityAlias

    @event.listens_for(CommodityAlias, 'after_insert')
    def on_alias_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "alias": target.alias,
                "commodity_id": target.commodity_id
            }
            log_change(connection, "commodity_aliases", target.id, "INSERT", data)
        except Exception as e:
            logger.error(f"Error in alias after_insert event: {e}")

    @event.listens_for(CommodityAlias, 'after_update')
    def on_alias_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "alias": target.alias,
                "commodity_id": target.commodity_id
            }
            log_change(connection, "commodity_aliases", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in alias after_update event: {e}")

    @event.listens_for(CommodityAlias, 'after_delete')
    def on_alias_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id}
            log_change(connection, "commodity_aliases", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in alias after_delete event: {e}")

    logger.info("✅ CommodityAlias event listeners registered")



def setup_user_block_events():
    """Setup event listeners for UserBlock model"""
    from models.user_block import UserBlock

    @event.listens_for(UserBlock, 'after_insert')
    def on_block_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "blocker_id": target.blocker_id,
                "blocked_id": target.blocked_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            log_change(connection, "user_blocks", target.id, "INSERT", data)
        except Exception as e:
            logger.error(f"Error in user_block after_insert event: {e}")

    @event.listens_for(UserBlock, 'after_delete')
    def on_block_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id}
            log_change(connection, "user_blocks", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in user_block after_delete event: {e}")

    logger.info("✅ UserBlock event listeners registered")


def setup_trading_settings_events():
    """Setup event listeners for TradingSetting model"""
    from models.trading_setting import TradingSetting

    @event.listens_for(TradingSetting, 'after_insert')
    def on_setting_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "key": target.key,
                "value": target.value,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None
            }
            # For trading_settings, the key is the ID, but change_log requires int record_id
            # We pass 0 as dummy ID, the real key is in data['key']
            log_change(connection, "trading_settings", 0, "INSERT", data)
        except Exception as e:
            logger.error(f"Error in trading_setting after_insert event: {e}")

    @event.listens_for(TradingSetting, 'after_update')
    def on_setting_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "key": target.key,
                "value": target.value,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None
            }
            # For trading_settings, the key is the ID, but change_log requires int record_id
            # We pass 0 as dummy ID, the real key is in data['key']
            log_change(connection, "trading_settings", 0, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in trading_setting after_update event: {e}")

    logger.info("✅ TradingSetting event listeners registered")

def setup_all_events():
    """Setup all event listeners"""
    setup_user_events()
    setup_offer_events()
    setup_trade_events()
    setup_commodity_events()
    setup_commodity_alias_events()
    setup_trading_settings_events()
    setup_user_block_events()
    logger.info("🎯 All event listeners initialized")


