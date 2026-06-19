# core/events.py
"""
SQLAlchemy Event Listeners for Real-time Redis Publishing & Cross-Server Sync

This module automatically publishes Redis events when database models change,
enabling true real-time synchronization without manual publish calls.

Sync flow (optimized):
  1. Event fires → log_change() inserts into change_log (same DB transaction)
  2. log_change() pushes to Redis sync:outbound queue (persistent connection)
  3. log_change() fires direct HTTP push via thread pool (instant delivery)
  4. sync_worker drains committed change_log rows and also consumes Redis wake-ups/retries
"""
import json
import logging
from typing import Any, Dict
from datetime import datetime
from sqlalchemy import event
from sqlalchemy.orm import Session
import redis.asyncio as redis
from core.redis import pool
from sqlalchemy.sql import text
import hashlib
from core.utils import utc_now_naive
from core.sync_outbox_guard import mark_sync_outbox_recorded, register_sync_outbox_guards

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
    json_data = json.dumps(data, default=str)
    data_hash = hashlib.sha256(json_data.encode()).hexdigest()
    now = utc_now_naive()

    # 1. Insert into change_log (same transaction as the triggering change).
    # This is the durable sync outbox; failures must abort synced writes.
    sql = text("""
        INSERT INTO change_log (operation, table_name, record_id, data, timestamp, hash, synced, verified, created_at)
        VALUES (:op, :tbl, :rid, :data, :ts, :hash, false, false, NOW())
        RETURNING id
    """)
    result = connection.execute(sql, {
        "op": operation,
        "tbl": table_name,
        "rid": record_id,
        "data": json_data,
        "ts": now,
        "hash": data_hash
    })
    change_log_id = _extract_change_log_id(result)
    mark_sync_outbox_recorded(connection, table_name, operation, record_id, data)

    # Build sync payload
    payload = {
        "type": "db_change",
        "operation": operation,
        "table": table_name,
        "id": record_id,
        "data": data,
        "hash": data_hash,
        "timestamp": now.timestamp()
    }
    if change_log_id is not None:
        payload["change_log_id"] = change_log_id
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


def _extract_change_log_id(result) -> int | None:
    try:
        value = result.scalar_one_or_none()
    except Exception:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _isoformat_or_none(value):
    return value.isoformat() if value else None


def _chat_sync_payload(target) -> Dict[str, Any]:
    return {
        "id": target.id,
        "type": target.type.value if getattr(target, "type", None) else None,
        "title": target.title,
        "description": target.description,
        "created_by_id": target.created_by_id,
        "is_system": target.is_system,
        "is_mandatory": target.is_mandatory,
        "is_deleted": target.is_deleted,
        "deleted_at": _isoformat_or_none(getattr(target, "deleted_at", None)),
        "max_members": target.max_members,
        "pinned_message_id": getattr(target, "pinned_message_id", None),
        "pinned_message_at": _isoformat_or_none(getattr(target, "pinned_message_at", None)),
        "pinned_message_by_id": getattr(target, "pinned_message_by_id", None),
        "last_message_at": _isoformat_or_none(getattr(target, "last_message_at", None)),
        "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
        "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
    }


def _lookup_chat_sync_flags(connection, chat_id: int):
    try:
        result = connection.execute(
            text("SELECT type, is_system, is_mandatory FROM chats WHERE id = :chat_id"),
            {"chat_id": chat_id},
        )
        row = result.mappings().first()
        if row is None:
            return None, None, None
        return row.get("type"), row.get("is_system"), row.get("is_mandatory")
    except Exception:
        return None, None, None


def _chat_member_sync_payload(connection, target) -> Dict[str, Any]:
    chat_type = getattr(target, "chat_type", None)
    chat_is_system = getattr(target, "chat_is_system", None)
    chat_is_mandatory = getattr(target, "chat_is_mandatory", None)
    if chat_type is None or chat_is_system is None or chat_is_mandatory is None:
        chat_type, chat_is_system, chat_is_mandatory = _lookup_chat_sync_flags(connection, target.chat_id)

    return {
        "id": target.id,
        "chat_id": target.chat_id,
        "user_id": target.user_id,
        "role": target.role.value if getattr(target, "role", None) else None,
        "membership_status": target.membership_status.value if getattr(target, "membership_status", None) else None,
        "chat_type": chat_type,
        "chat_is_system": chat_is_system,
        "chat_is_mandatory": chat_is_mandatory,
        "joined_at": _isoformat_or_none(getattr(target, "joined_at", None)),
        "left_at": _isoformat_or_none(getattr(target, "left_at", None)),
        "last_read_at": _isoformat_or_none(getattr(target, "last_read_at", None)),
        "is_muted": target.is_muted,
        "is_marked_unread": target.is_marked_unread,
        "is_pinned": target.is_pinned,
        "pinned_at": _isoformat_or_none(getattr(target, "pinned_at", None)),
        "pin_order": getattr(target, "pin_order", None),
        "is_hidden": target.is_hidden,
        "hidden_at": _isoformat_or_none(getattr(target, "hidden_at", None)),
        "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
        "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
    }


def setup_chat_events():
    """Setup event listeners for Chat model."""
    from models.chat import Chat

    @event.listens_for(Chat, 'after_insert')
    def on_chat_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chats", target.id, "INSERT", _chat_sync_payload(target))
        except Exception as e:
            logger.error(f"Error in chat after_insert event: {e}")

    @event.listens_for(Chat, 'after_update')
    def on_chat_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chats", target.id, "UPDATE", _chat_sync_payload(target))
        except Exception as e:
            logger.error(f"Error in chat after_update event: {e}")

    @event.listens_for(Chat, 'after_delete')
    def on_chat_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chats", target.id, "DELETE", _chat_sync_payload(target))
        except Exception as e:
            logger.error(f"Error in chat after_delete event: {e}")

    logger.info("✅ Chat event listeners registered")


def setup_chat_member_events():
    """Setup event listeners for ChatMember model."""
    from models.chat_member import ChatMember

    @event.listens_for(ChatMember, 'after_insert')
    def on_chat_member_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chat_members", target.id, "INSERT", _chat_member_sync_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in chat_member after_insert event: {e}")

    @event.listens_for(ChatMember, 'after_update')
    def on_chat_member_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chat_members", target.id, "UPDATE", _chat_member_sync_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in chat_member after_update event: {e}")

    @event.listens_for(ChatMember, 'after_delete')
    def on_chat_member_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "chat_members", target.id, "DELETE", _chat_member_sync_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in chat_member after_delete event: {e}")

    logger.info("✅ ChatMember event listeners registered")


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
                "offer_public_id": getattr(target, "offer_public_id", None),
                "version_id": target.version_id or 1,
                "user_id": target.user_id,
                "actor_user_id": getattr(target, "actor_user_id", None),
                "home_server": target.home_server,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "original_lot_sizes": target.original_lot_sizes,
                "expire_reason": getattr(target, "expire_reason", None),
                "expired_by_user_id": getattr(target, "expired_by_user_id", None),
                "expired_by_actor_user_id": getattr(target, "expired_by_actor_user_id", None),
                "expire_source_surface": getattr(target, "expire_source_surface", None),
                "expire_source_server": getattr(target, "expire_source_server", None),
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "channel_message_id": target.channel_message_id,
                "republished_offer_id": target.republished_offer_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
                "expired_at": target.expired_at.isoformat() if getattr(target, "expired_at", None) else None,
                "idempotency_key": target.idempotency_key,
                "archived": target.archived,
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
                "offer_public_id": getattr(target, "offer_public_id", None),
                "version_id": target.version_id or 1,
                "user_id": target.user_id,
                "actor_user_id": getattr(target, "actor_user_id", None),
                "home_server": target.home_server,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "original_lot_sizes": target.original_lot_sizes,
                "expire_reason": getattr(target, "expire_reason", None),
                "expired_by_user_id": getattr(target, "expired_by_user_id", None),
                "expired_by_actor_user_id": getattr(target, "expired_by_actor_user_id", None),
                "expire_source_surface": getattr(target, "expire_source_surface", None),
                "expire_source_server": getattr(target, "expire_source_server", None),
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "channel_message_id": target.channel_message_id,
                "republished_offer_id": target.republished_offer_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
                "expired_at": target.expired_at.isoformat() if getattr(target, "expired_at", None) else None,
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


def _offer_request_sync_payload(target) -> Dict[str, Any]:
    source_surface = getattr(target, "request_source_surface", None)
    result_status = getattr(target, "result_status", None)
    commission_rate = getattr(target, "customer_commission_rate_snapshot", None)
    return {
        "id": target.id,
        "version_id": getattr(target, "version_id", None) or 1,
        "request_home_server": target.request_home_server,
        "local_offer_id": target.local_offer_id,
        "offer_public_id": target.offer_public_id,
        "requester_user_id": target.requester_user_id,
        "actor_user_id": target.actor_user_id,
        "request_source_surface": source_surface.value if hasattr(source_surface, "value") else source_surface,
        "request_source_server": target.request_source_server,
        "requested_quantity": target.requested_quantity,
        "idempotency_key": target.idempotency_key,
        "received_at": _isoformat_or_none(getattr(target, "received_at", None)),
        "decided_at": _isoformat_or_none(getattr(target, "decided_at", None)),
        "result_status": result_status.value if hasattr(result_status, "value") else result_status,
        "public_failure_code": target.public_failure_code,
        "public_failure_message": target.public_failure_message,
        "internal_failure_code": target.internal_failure_code,
        "internal_failure_context": target.internal_failure_context,
        "resulting_trade_id": target.resulting_trade_id,
        "customer_relation_id": target.customer_relation_id,
        "customer_owner_user_id": target.customer_owner_user_id,
        "customer_tier_snapshot": target.customer_tier_snapshot,
        "customer_management_name_snapshot": target.customer_management_name_snapshot,
        "customer_commission_rate_snapshot": str(commission_rate) if commission_rate is not None else None,
        "customer_commission_context": target.customer_commission_context,
        "archived": target.archived,
        "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
        "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
    }


def setup_offer_request_events():
    """Setup event listeners for the offer request ledger."""
    from models.offer_request import OfferRequest

    @event.listens_for(OfferRequest, 'after_insert')
    def on_offer_request_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "offer_requests", target.id, "INSERT", _offer_request_sync_payload(target))
        except Exception as e:
            logger.error(f"Error in offer_request after_insert event: {e}")

    @event.listens_for(OfferRequest, 'after_update')
    def on_offer_request_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "offer_requests", target.id, "UPDATE", _offer_request_sync_payload(target))
        except Exception as e:
            logger.error(f"Error in offer_request after_update event: {e}")

    logger.info("✅ OfferRequest event listeners registered")


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
                "actor_user_id": getattr(target, "actor_user_id", None),
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
                "actor_user_id": getattr(target, "actor_user_id", None),
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
                "account_status": target.account_status.value if getattr(target, "account_status", None) else None,
                "deactivated_at": target.deactivated_at.isoformat() if getattr(target, "deactivated_at", None) else None,
                "messenger_grace_expires_at": target.messenger_grace_expires_at.isoformat() if getattr(target, "messenger_grace_expires_at", None) else None,
                "messenger_blocked_at": target.messenger_blocked_at.isoformat() if getattr(target, "messenger_blocked_at", None) else None,
                "global_lock_grace_expires_at": target.messenger_grace_expires_at.isoformat() if getattr(target, "messenger_grace_expires_at", None) else None,
                "global_web_locked_at": target.messenger_blocked_at.isoformat() if getattr(target, "messenger_blocked_at", None) else None,
                "has_bot_access": target.has_bot_access,
                "admin_password_hash": target.admin_password_hash,
                "must_change_password": target.must_change_password,
                "home_server": target.home_server,
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
                "max_sessions": target.max_sessions,
                "max_accountants": getattr(target, "max_accountants", 3),
                    "max_customers": getattr(target, "max_customers", 5),
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
                "account_status": target.account_status.value if getattr(target, "account_status", None) else None,
                "deactivated_at": target.deactivated_at.isoformat() if getattr(target, "deactivated_at", None) else None,
                "messenger_grace_expires_at": target.messenger_grace_expires_at.isoformat() if getattr(target, "messenger_grace_expires_at", None) else None,
                "messenger_blocked_at": target.messenger_blocked_at.isoformat() if getattr(target, "messenger_blocked_at", None) else None,
                "global_lock_grace_expires_at": target.messenger_grace_expires_at.isoformat() if getattr(target, "messenger_grace_expires_at", None) else None,
                "global_web_locked_at": target.messenger_blocked_at.isoformat() if getattr(target, "messenger_blocked_at", None) else None,
                "has_bot_access": target.has_bot_access,
                "admin_password_hash": target.admin_password_hash,
                "must_change_password": target.must_change_password,
                "home_server": target.home_server,
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
                "max_sessions": target.max_sessions,
                "max_accountants": getattr(target, "max_accountants", 3),
                "max_customers": getattr(target, "max_customers", 5),
                "last_seen_at": target.last_seen_at.isoformat() if target.last_seen_at else None,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
            }
            log_change(connection, "users", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in user after_update event: {e}")

    logger.info("✅ User event listeners registered")


def setup_accountant_relation_events():
    """Setup event listeners for AccountantRelation model."""
    from models.accountant_relation import AccountantRelation

    def accountant_relation_payload(target):
        status = target.status.value if hasattr(target.status, "value") else target.status
        return {
            "id": target.id,
            "owner_user_id": target.owner_user_id,
            "accountant_user_id": target.accountant_user_id,
            "created_by_user_id": target.created_by_user_id,
            "invitation_token": target.invitation_token,
            "global_account_name": target.global_account_name,
            "relation_display_name": target.relation_display_name,
            "duty_description": target.duty_description,
            "mobile_number": target.mobile_number,
            "status": status,
            "expires_at": target.expires_at.isoformat() if target.expires_at else None,
            "activated_at": target.activated_at.isoformat() if target.activated_at else None,
            "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
            "created_at": target.created_at.isoformat() if target.created_at else None,
            "updated_at": target.updated_at.isoformat() if target.updated_at else None,
        }

    @event.listens_for(AccountantRelation, 'after_insert')
    def on_accountant_relation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "accountant_relations", target.id, "INSERT", accountant_relation_payload(target))
        except Exception as e:
            logger.error(f"Error in accountant_relation after_insert event: {e}")

    @event.listens_for(AccountantRelation, 'after_update')
    def on_accountant_relation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "accountant_relations", target.id, "UPDATE", accountant_relation_payload(target))
        except Exception as e:
            logger.error(f"Error in accountant_relation after_update event: {e}")

    @event.listens_for(AccountantRelation, 'after_delete')
    def on_accountant_relation_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "accountant_relations", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in accountant_relation after_delete event: {e}")

    logger.info("✅ AccountantRelation event listeners registered")


def setup_customer_relation_events():
    """Setup event listeners for CustomerRelation model."""
    from models.customer_relation import CustomerRelation

    def customer_relation_payload(target):
        status = target.status.value if hasattr(target.status, "value") else target.status
        customer_tier = target.customer_tier.value if hasattr(target.customer_tier, "value") else target.customer_tier
        commission_rate = target.commission_rate
        if commission_rate is not None:
            commission_rate = str(commission_rate)
        return {
            "id": target.id,
            "owner_user_id": target.owner_user_id,
            "customer_user_id": target.customer_user_id,
            "created_by_user_id": target.created_by_user_id,
            "invitation_token": target.invitation_token,
            "management_name": target.management_name,
            "customer_tier": customer_tier,
            "commission_rate": commission_rate,
            "min_trade_quantity": target.min_trade_quantity,
            "max_trade_quantity": target.max_trade_quantity,
            "max_daily_trades": target.max_daily_trades,
            "max_daily_commodity_volume": target.max_daily_commodity_volume,
            "trading_restricted_until": target.trading_restricted_until.isoformat() if target.trading_restricted_until else None,
            "status": status,
            "expires_at": target.expires_at.isoformat() if target.expires_at else None,
            "activated_at": target.activated_at.isoformat() if target.activated_at else None,
            "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
            "created_at": target.created_at.isoformat() if target.created_at else None,
            "updated_at": target.updated_at.isoformat() if target.updated_at else None,
        }

    @event.listens_for(CustomerRelation, 'after_insert')
    def on_customer_relation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "customer_relations", target.id, "INSERT", customer_relation_payload(target))
        except Exception as e:
            logger.error(f"Error in customer_relation after_insert event: {e}")

    @event.listens_for(CustomerRelation, 'after_update')
    def on_customer_relation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "customer_relations", target.id, "UPDATE", customer_relation_payload(target))
        except Exception as e:
            logger.error(f"Error in customer_relation after_update event: {e}")

    @event.listens_for(CustomerRelation, 'after_delete')
    def on_customer_relation_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "customer_relations", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in customer_relation after_delete event: {e}")

    logger.info("✅ CustomerRelation event listeners registered")


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


def setup_market_schedule_override_events():
    """Setup event listeners for MarketScheduleOverride model."""
    from models.market_schedule_override import MarketScheduleOverride

    def market_schedule_override_payload(target):
        override_type = target.override_type.value if getattr(target, "override_type", None) else None
        return {
            "id": target.id,
            "date": target.date.isoformat() if getattr(target, "date", None) else None,
            "override_type": override_type,
            "open_time_local": target.open_time_local.isoformat() if getattr(target, "open_time_local", None) else None,
            "close_time_local": target.close_time_local.isoformat() if getattr(target, "close_time_local", None) else None,
            "note": target.note,
            "created_by_user_id": target.created_by_user_id,
            "created_at": target.created_at.isoformat() if getattr(target, "created_at", None) else None,
            "updated_at": target.updated_at.isoformat() if getattr(target, "updated_at", None) else None,
        }

    @event.listens_for(MarketScheduleOverride, 'after_insert')
    def on_market_schedule_override_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_schedule_overrides", target.id, "INSERT", market_schedule_override_payload(target))
        except Exception as e:
            logger.error(f"Error in market_schedule_override after_insert event: {e}")

    @event.listens_for(MarketScheduleOverride, 'after_update')
    def on_market_schedule_override_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_schedule_overrides", target.id, "UPDATE", market_schedule_override_payload(target))
        except Exception as e:
            logger.error(f"Error in market_schedule_override after_update event: {e}")

    @event.listens_for(MarketScheduleOverride, 'after_delete')
    def on_market_schedule_override_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_schedule_overrides", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in market_schedule_override after_delete event: {e}")

    logger.info("✅ MarketScheduleOverride event listeners registered")


def setup_market_runtime_state_events():
    """Setup event listeners for MarketRuntimeState model."""
    from models.market_runtime_state import MarketRuntimeState

    def market_runtime_state_payload(target):
        return {
            "id": target.id,
            "is_open": target.is_open,
            "active_web_notice_visible": target.active_web_notice_visible,
            "offers_since_last_open": target.offers_since_last_open,
            "last_transition_at": target.last_transition_at.isoformat() if getattr(target, "last_transition_at", None) else None,
            "created_at": target.created_at.isoformat() if getattr(target, "created_at", None) else None,
            "updated_at": target.updated_at.isoformat() if getattr(target, "updated_at", None) else None,
        }

    @event.listens_for(MarketRuntimeState, 'after_insert')
    def on_market_runtime_state_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_runtime_state", target.id, "INSERT", market_runtime_state_payload(target))
        except Exception as e:
            logger.error(f"Error in market_runtime_state after_insert event: {e}")

    @event.listens_for(MarketRuntimeState, 'after_update')
    def on_market_runtime_state_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_runtime_state", target.id, "UPDATE", market_runtime_state_payload(target))
        except Exception as e:
            logger.error(f"Error in market_runtime_state after_update event: {e}")

    @event.listens_for(MarketRuntimeState, 'after_delete')
    def on_market_runtime_state_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "market_runtime_state", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in market_runtime_state after_delete event: {e}")

    logger.info("✅ MarketRuntimeState event listeners registered")


def setup_invitation_events():
    """Setup event listeners for Invitation model"""
    from models.invitation import Invitation

    def invitation_payload(target):
        return {
            "id": target.id,
            "account_name": target.account_name,
            "mobile_number": target.mobile_number,
            "token": target.token,
            "short_code": target.short_code,
            "role": target.role.value if target.role else None,
            "created_by_id": target.created_by_id,
            "is_used": target.is_used,
            "expires_at": target.expires_at.isoformat() if target.expires_at else None,
            "created_at": target.created_at.isoformat() if target.created_at else None,
        }

    @event.listens_for(Invitation, 'after_insert')
    def on_invitation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "invitations", target.id, "INSERT", invitation_payload(target))
        except Exception as e:
            logger.error(f"Error in invitation after_insert event: {e}")

    @event.listens_for(Invitation, 'after_update')
    def on_invitation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "invitations", target.id, "UPDATE", invitation_payload(target))
        except Exception as e:
            logger.error(f"Error in invitation after_update event: {e}")

    @event.listens_for(Invitation, 'after_delete')
    def on_invitation_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id, "token": target.token, "short_code": target.short_code}
            log_change(connection, "invitations", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in invitation after_delete event: {e}")

    logger.info("✅ Invitation event listeners registered")


def setup_notification_events():
    """Setup event listeners for Notification model"""
    from models.notification import Notification

    def notification_payload(target):
        level = target.level.value if hasattr(target.level, "value") else target.level
        category = target.category.value if hasattr(target.category, "value") else target.category
        return {
            "id": target.id,
            "user_id": target.user_id,
            "message": target.message,
            "is_read": target.is_read,
            "created_at": (target.created_at or utc_now_naive()).isoformat(),
            "level": level,
            "category": category,
        }

    @event.listens_for(Notification, 'after_insert')
    def on_notification_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "notifications", target.id, "INSERT", notification_payload(target))
        except Exception as e:
            logger.error(f"Error in notification after_insert event: {e}")

    @event.listens_for(Notification, 'after_update')
    def on_notification_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "notifications", target.id, "UPDATE", notification_payload(target))
        except Exception as e:
            logger.error(f"Error in notification after_update event: {e}")

    @event.listens_for(Notification, 'after_delete')
    def on_notification_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id, "user_id": target.user_id}
            log_change(connection, "notifications", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in notification after_delete event: {e}")

    logger.info("✅ Notification event listeners registered")


def setup_user_notification_preference_events():
    """Setup event listeners for UserNotificationPreference model."""
    from models.user_notification_preference import UserNotificationPreference

    def preference_payload(target):
        return {
            "id": target.id,
            "user_id": target.user_id,
            "market_offer_push_enabled": target.market_offer_push_enabled,
            "created_at": target.created_at.isoformat() if getattr(target, "created_at", None) else None,
            "updated_at": target.updated_at.isoformat() if getattr(target, "updated_at", None) else None,
        }

    @event.listens_for(UserNotificationPreference, 'after_insert')
    def on_user_notification_preference_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "user_notification_preferences",
                target.id,
                "INSERT",
                preference_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in user_notification_preference after_insert event: {e}")

    @event.listens_for(UserNotificationPreference, 'after_update')
    def on_user_notification_preference_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "user_notification_preferences",
                target.id,
                "UPDATE",
                preference_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in user_notification_preference after_update event: {e}")

    @event.listens_for(UserNotificationPreference, 'after_delete')
    def on_user_notification_preference_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "user_notification_preferences",
                target.id,
                "DELETE",
                {"id": target.id, "user_id": target.user_id},
            )
        except Exception as e:
            logger.error(f"Error in user_notification_preference after_delete event: {e}")

    logger.info("✅ UserNotificationPreference event listeners registered")


def setup_admin_message_events():
    """Setup event listeners for admin management message history models."""
    from models.admin_message import AdminBroadcastMessage, AdminMarketMessage

    def market_message_payload(target):
        now_iso = utc_now_naive().isoformat()
        return {
            "id": target.id,
            "content": target.content,
            "created_by_id": target.created_by_id,
            "reused_from_id": target.reused_from_id,
            "is_active": target.is_active,
            "notified_recipients_count": target.notified_recipients_count,
            "published_at": target.published_at.isoformat() if target.published_at else now_iso,
            "created_at": target.created_at.isoformat() if target.created_at else now_iso,
            "updated_at": target.updated_at.isoformat() if target.updated_at else None,
        }

    def broadcast_message_payload(target):
        now_iso = utc_now_naive().isoformat()
        return {
            "id": target.id,
            "content": target.content,
            "created_by_id": target.created_by_id,
            "target_groups": target.target_groups or [],
            "recipient_count": target.recipient_count,
            "published_at": target.published_at.isoformat() if target.published_at else now_iso,
            "created_at": target.created_at.isoformat() if target.created_at else now_iso,
        }

    @event.listens_for(AdminMarketMessage, 'after_insert')
    def on_admin_market_message_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_market_messages", target.id, "INSERT", market_message_payload(target))
        except Exception as e:
            logger.error(f"Error in admin_market_message after_insert event: {e}")

    @event.listens_for(AdminMarketMessage, 'after_update')
    def on_admin_market_message_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_market_messages", target.id, "UPDATE", market_message_payload(target))
        except Exception as e:
            logger.error(f"Error in admin_market_message after_update event: {e}")

    @event.listens_for(AdminMarketMessage, 'after_delete')
    def on_admin_market_message_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_market_messages", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in admin_market_message after_delete event: {e}")

    @event.listens_for(AdminBroadcastMessage, 'after_insert')
    def on_admin_broadcast_message_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_broadcast_messages", target.id, "INSERT", broadcast_message_payload(target))
        except Exception as e:
            logger.error(f"Error in admin_broadcast_message after_insert event: {e}")

    @event.listens_for(AdminBroadcastMessage, 'after_update')
    def on_admin_broadcast_message_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_broadcast_messages", target.id, "UPDATE", broadcast_message_payload(target))
        except Exception as e:
            logger.error(f"Error in admin_broadcast_message after_update event: {e}")

    @event.listens_for(AdminBroadcastMessage, 'after_delete')
    def on_admin_broadcast_message_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "admin_broadcast_messages", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in admin_broadcast_message after_delete event: {e}")

    logger.info("✅ AdminMessage event listeners registered")

def setup_all_events():
    """Setup all event listeners"""
    register_sync_outbox_guards()
    setup_user_events()
    setup_accountant_relation_events()
    setup_customer_relation_events()
    setup_chat_events()
    setup_chat_member_events()
    setup_invitation_events()
    setup_offer_events()
    setup_offer_request_events()
    setup_trade_events()
    setup_commodity_events()
    setup_commodity_alias_events()
    setup_trading_settings_events()
    setup_market_schedule_override_events()
    setup_market_runtime_state_events()
    setup_user_block_events()
    setup_notification_events()
    setup_user_notification_preference_events()
    setup_admin_message_events()
    logger.info("🎯 All event listeners initialized")


# Alias for main.py / startup
setup_event_listeners = setup_all_events
