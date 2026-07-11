# core/events.py
"""
SQLAlchemy Event Listeners for Real-time Redis Publishing & Cross-Server Sync

This module automatically records durable change_log rows when database models
change, enabling committed outbox synchronization without manual calls.

Sync flow:
  1. Event fires → log_change() inserts into change_log (same DB transaction)
  2. The source transaction commits or rolls back normally
  3. sync_worker drains committed change_log rows and delivers them to the peer

log_change() must not publish peer-deliverable payloads before commit. Redis
publishing in this module is reserved for local realtime UI events.
"""
import json
import logging
from typing import Any, Dict
from datetime import datetime
from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql import text
import hashlib
from core.utils import utc_now_naive
from core.offer_sync_payload import build_offer_sync_payload
from core.sync_outbox_guard import mark_sync_outbox_recorded, register_sync_outbox_guards
from core.sync_field_policy import sanitize_sync_payload
from core.config import settings
from core.registration_sync_policy import (
    REGISTRATION_USER_REFERENCES_FIELD,
    USER_SYNC_COUNTER_FIELDS,
    allowed_user_fields_for_source,
)
from core.user_counter_sync import (
    USER_SYNC_IDENTITY_FIELD,
    InvalidUserCounterMutation,
    build_user_counter_event,
    build_user_sync_identity,
    normalize_counter_event_occurred_at,
    user_counter_event_content_hash,
    user_counter_event_payload,
)

logger = logging.getLogger(__name__)

# ─── Persistent Redis connection for sync pushes ───
_sync_redis = None


def _changed_column_fields(target) -> set[str]:
    try:
        state = sa_inspect(target)
        return {
            attribute.key
            for attribute in state.mapper.column_attrs
            if state.attrs[attribute.key].history.has_changes()
        }
    except Exception:
        return set()


def _bump_sync_version(target) -> None:
    try:
        current = int(getattr(target, "sync_version", 1) or 1)
    except (TypeError, ValueError):
        current = 1
    target.sync_version = max(current, 1) + 1


def _registration_sync_v2_enabled() -> bool:
    return bool(getattr(settings, "registration_sync_v2_enabled", False))


def _registration_user_references(connection, references: dict[str, int | None]) -> dict:
    if not _registration_sync_v2_enabled():
        return {}
    result: dict[str, dict] = {}
    for field_name, user_id in references.items():
        if user_id is None:
            continue
        row = connection.execute(
            text(
                "SELECT account_name, mobile_number, telegram_id "
                "FROM users WHERE id = :user_id"
            ),
            {"user_id": int(user_id)},
        ).mappings().one_or_none()
        if row is None:
            raise RuntimeError(f"registration_user_reference_missing:{field_name}")
        identity = {
            key: row[key]
            for key in ("account_name", "mobile_number", "telegram_id")
            if row[key] not in (None, "")
        }
        if not identity:
            raise RuntimeError(f"registration_user_reference_identity_missing:{field_name}")
        result[field_name] = {"current": identity, "previous": {}}
    return result


def _get_sync_redis():
    """Get or create a persistent synchronous Redis connection"""
    global _sync_redis
    if _sync_redis is None:
        import redis as sync_redis
        from core.config import settings
        try:
            socket_timeout = max(0.05, float(getattr(settings, "sync_signal_redis_timeout_seconds", 0.25)))
        except (TypeError, ValueError):
            socket_timeout = 0.25
        _sync_redis = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=0,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
            socket_keepalive=True,
            retry_on_timeout=True
        )
    return _sync_redis


def log_change(connection, table_name: str, record_id: int, operation: str, data: Dict[str, Any]):
    """Record a committed-outbox candidate in change_log.

    This function runs inside SQLAlchemy flush-time listeners. It intentionally
    does not push to Redis or direct HTTP because the enclosing transaction may
    still roll back. sync_worker is responsible for reading committed rows.
    """
    data = sanitize_sync_payload(table_name, data)
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
    _extract_change_log_id(result)
    mark_sync_outbox_recorded(connection, table_name, operation, record_id, data)


def _record_local_user_counter_event(connection, target, counter_event, source_server: str) -> None:
    """Persist the producer-side ledger row in the User transaction."""
    if counter_event.kind == "reset":
        latest_reset = connection.execute(
            text(
                """
                SELECT event_epoch, occurred_at
                FROM user_counter_event_receipts
                WHERE user_id = :user_id AND event_kind = 'reset'
                ORDER BY event_epoch DESC
                LIMIT 1
                """
            ),
            {"user_id": int(target.id)},
        ).first()
        expected_previous_epoch = int(counter_event.epoch) - 1
        if expected_previous_epoch == 1:
            if latest_reset is not None:
                raise InvalidUserCounterMutation(
                    "first counter reset conflicts with existing reset history"
                )
        elif latest_reset is None or int(latest_reset[0]) != expected_previous_epoch:
            raise InvalidUserCounterMutation(
                "counter reset history is incomplete or non-sequential"
            )
        if latest_reset is not None:
            previous_boundary = normalize_counter_event_occurred_at(latest_reset[1])
            if counter_event.occurred_at <= previous_boundary:
                raise InvalidUserCounterMutation(
                    "counter reset boundary must advance monotonically"
                )

    event_hash = user_counter_event_content_hash(
        source_server=source_server,
        event_id=counter_event.event_id,
        kind=counter_event.kind,
        epoch=counter_event.epoch,
        deltas=counter_event.deltas,
        occurred_at=counter_event.occurred_at,
    )
    connection.execute(
        text(
            """
            INSERT INTO user_counter_event_receipts (
                event_id, source_server, user_id, event_hash, event_kind,
                event_epoch, occurred_at, deltas
            ) VALUES (
                :event_id, :source_server, :user_id, :event_hash, :event_kind,
                :event_epoch, :occurred_at, CAST(:deltas AS jsonb)
            )
            """
        ),
        {
            "event_id": counter_event.event_id,
            "source_server": source_server,
            "user_id": int(target.id),
            "event_hash": event_hash,
            "event_kind": counter_event.kind,
            "event_epoch": int(counter_event.epoch),
            "occurred_at": counter_event.occurred_at,
            "deltas": json.dumps(counter_event.deltas, sort_keys=True),
        },
    )


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


def _lookup_offer_public_id(connection, offer_id: int | None) -> str | None:
    if not offer_id:
        return None
    try:
        result = connection.execute(
            text("SELECT offer_public_id FROM offers WHERE id = :offer_id"),
            {"offer_id": offer_id},
        )
        row = result.first()
        if row is None:
            return None
        value = row[0]
        return value if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def _lookup_commodity_name(connection, commodity_id: int | None) -> str | None:
    if not commodity_id:
        return None
    try:
        result = connection.execute(
            text("SELECT name FROM commodities WHERE id = :commodity_id"),
            {"commodity_id": commodity_id},
        )
        row = result.first()
        if row is None:
            return None
        value = row[0]
        return value if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def _lookup_customer_relation_invitation_token(connection, relation_id: int | None) -> str | None:
    if not relation_id:
        return None
    try:
        result = connection.execute(
            text("SELECT invitation_token FROM customer_relations WHERE id = :relation_id"),
            {"relation_id": relation_id},
        )
        row = result.first()
        if row is None:
            return None
        value = row[0]
        return value if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


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
            data = build_offer_sync_payload(target)
            data["commodity_name"] = _lookup_commodity_name(connection, target.commodity_id)
            data["republished_offer_public_id"] = _lookup_offer_public_id(
                connection,
                getattr(target, "republished_offer_id", None),
            )
            log_change(connection, "offers", target.id, "INSERT", data)
            publish_event_sync("offer:created", data)
        except Exception as e:
            logger.error(f"Error in offer after_insert event: {e}")

    @event.listens_for(Offer, 'after_update')
    def on_offer_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = build_offer_sync_payload(target)
            data["commodity_name"] = _lookup_commodity_name(connection, target.commodity_id)
            data["republished_offer_public_id"] = _lookup_offer_public_id(
                connection,
                getattr(target, "republished_offer_id", None),
            )
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
            offer_public_id = getattr(target, "offer_public_id", None)
            if offer_public_id:
                data["offer_public_id"] = offer_public_id
            log_change(connection, "offers", target.id, "DELETE", data)
            publish_event_sync("offer:deleted", data)
        except Exception as e:
            logger.error(f"Error in offer after_delete event: {e}")

    logger.info("✅ Offer event listeners registered")


def _offer_request_sync_payload(target, connection=None) -> Dict[str, Any]:
    source_surface = getattr(target, "request_source_surface", None)
    result_status = getattr(target, "result_status", None)
    commission_rate = getattr(target, "customer_commission_rate_snapshot", None)
    resulting_trade_number = _offer_request_resulting_trade_number(target, connection=connection)
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
        "resulting_trade_number": resulting_trade_number,
        "customer_relation_id": target.customer_relation_id,
        "customer_relation_invitation_token": _lookup_customer_relation_invitation_token(
            connection,
            target.customer_relation_id,
        ) if connection is not None else None,
        "customer_owner_user_id": target.customer_owner_user_id,
        "customer_tier_snapshot": target.customer_tier_snapshot,
        "customer_management_name_snapshot": target.customer_management_name_snapshot,
        "customer_commission_rate_snapshot": str(commission_rate) if commission_rate is not None else None,
        "customer_commission_context": target.customer_commission_context,
        "archived": target.archived,
        "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
        "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
    }


def _offer_request_resulting_trade_number(target, *, connection=None) -> int | None:
    resulting_trade = getattr(target, "resulting_trade", None)
    if resulting_trade is not None:
        trade_number = getattr(resulting_trade, "trade_number", None)
        if trade_number is not None:
            try:
                return int(trade_number)
            except (TypeError, ValueError):
                return None
    trade_id = getattr(target, "resulting_trade_id", None)
    if trade_id is None or connection is None:
        return None
    try:
        result = connection.execute(
            text("SELECT trade_number FROM trades WHERE id = :trade_id"),
            {"trade_id": trade_id},
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None
    except Exception:
        logger.warning(
            "Could not resolve offer request resulting trade_number for sync payload",
            extra={
                "event": "sync.offer_request_trade_number_resolution_failed",
                "offer_request_id": getattr(target, "id", None),
                "resulting_trade_id": trade_id,
            },
        )
    return None


def setup_offer_request_events():
    """Setup event listeners for the offer request ledger."""
    from models.offer_request import OfferRequest

    @event.listens_for(OfferRequest, 'after_insert')
    def on_offer_request_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "offer_requests", target.id, "INSERT", _offer_request_sync_payload(target, connection))
        except Exception as e:
            logger.error(f"Error in offer_request after_insert event: {e}")

    @event.listens_for(OfferRequest, 'after_update')
    def on_offer_request_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "offer_requests", target.id, "UPDATE", _offer_request_sync_payload(target, connection))
        except Exception as e:
            logger.error(f"Error in offer_request after_update event: {e}")

    logger.info("✅ OfferRequest event listeners registered")


def _offer_publication_state_sync_payload(target) -> Dict[str, Any]:
    surface = getattr(target, "surface", None)
    status = getattr(target, "status", None)
    return {
        "id": target.id,
        "version_id": getattr(target, "version_id", None) or 1,
        "offer_id": target.offer_id,
        "offer_public_id": target.offer_public_id,
        "offer_home_server": target.offer_home_server,
        "surface": surface.value if hasattr(surface, "value") else surface,
        "publication_owner_server": target.publication_owner_server,
        "status": status.value if hasattr(status, "value") else status,
        "dedupe_key": target.dedupe_key,
        "surface_resource_id": target.surface_resource_id,
        "telegram_chat_id": target.telegram_chat_id,
        "telegram_message_id": target.telegram_message_id,
        "offer_version_id": target.offer_version_id,
        "last_known_offer_status": target.last_known_offer_status,
        "last_attempt_at": _isoformat_or_none(getattr(target, "last_attempt_at", None)),
        "last_success_at": _isoformat_or_none(getattr(target, "last_success_at", None)),
        "next_retry_at": _isoformat_or_none(getattr(target, "next_retry_at", None)),
        "disabled_at": _isoformat_or_none(getattr(target, "disabled_at", None)),
        "lagged_at": _isoformat_or_none(getattr(target, "lagged_at", None)),
        "error_code": target.error_code,
        "error_message": target.error_message,
        "state_metadata": target.state_metadata,
        "archived": target.archived,
        "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
        "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
    }


def setup_offer_publication_state_events():
    """Setup event listeners for offer surface publication state."""
    from models.offer_publication_state import OfferPublicationState

    @event.listens_for(OfferPublicationState, 'after_insert')
    def on_offer_publication_state_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "offer_publication_states",
                target.id,
                "INSERT",
                _offer_publication_state_sync_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in offer_publication_state after_insert event: {e}")

    @event.listens_for(OfferPublicationState, 'after_update')
    def on_offer_publication_state_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "offer_publication_states",
                target.id,
                "UPDATE",
                _offer_publication_state_sync_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in offer_publication_state after_update event: {e}")

    @event.listens_for(OfferPublicationState, 'after_delete')
    def on_offer_publication_state_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "offer_publication_states",
                target.id,
                "DELETE",
                _offer_publication_state_sync_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in offer_publication_state after_delete event: {e}")

    logger.info("✅ OfferPublicationState event listeners registered")


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
                "offer_public_id": _lookup_offer_public_id(connection, target.offer_id),
                "offer_user_id": target.offer_user_id,
                "offer_user_mobile": target.offer_user_mobile,
                "responder_user_id": target.responder_user_id,
                "responder_user_mobile": target.responder_user_mobile,
                "actor_user_id": getattr(target, "actor_user_id", None),
                "commodity_id": target.commodity_id,
                "commodity_name": _lookup_commodity_name(connection, target.commodity_id),
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
                "offer_public_id": _lookup_offer_public_id(connection, target.offer_id),
                "offer_user_id": target.offer_user_id,
                "offer_user_mobile": target.offer_user_mobile,
                "responder_user_id": target.responder_user_id,
                "responder_user_mobile": target.responder_user_mobile,
                "actor_user_id": getattr(target, "actor_user_id", None),
                "commodity_id": target.commodity_id,
                "commodity_name": _lookup_commodity_name(connection, target.commodity_id),
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


def setup_trade_delivery_receipt_events():
    """Setup event listeners for TradeDeliveryReceipt model."""
    from models.trade_delivery_receipt import TradeDeliveryReceipt

    def receipt_payload(target) -> Dict[str, Any]:
        channel = target.channel.value if hasattr(target.channel, "value") else target.channel
        status = target.status.value if hasattr(target.status, "value") else target.status
        return {
            "id": target.id,
            "event_type": target.event_type,
            "dedupe_key": target.dedupe_key,
            "trade_number": target.trade_number,
            "recipient_user_id": target.recipient_user_id,
            "recipient_role": target.recipient_role,
            "channel": channel,
            "destination_server": target.destination_server,
            "status": status,
            "reason": target.reason,
            "telegram_message_id": target.telegram_message_id,
            "worker_id": target.worker_id,
            "lease_until": _isoformat_or_none(getattr(target, "lease_until", None)),
            "attempt_count": target.attempt_count,
            "next_retry_at": _isoformat_or_none(getattr(target, "next_retry_at", None)),
            "last_error": target.last_error,
            "last_error_class": target.last_error_class,
            "audit_payload": target.audit_payload,
            "event_created_at": _isoformat_or_none(getattr(target, "event_created_at", None)),
            "sent_at": _isoformat_or_none(getattr(target, "sent_at", None)),
            "terminal_at": _isoformat_or_none(getattr(target, "terminal_at", None)),
            "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
            "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
        }

    @event.listens_for(TradeDeliveryReceipt, 'after_insert')
    def on_trade_delivery_receipt_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "trade_delivery_receipts", target.id, "INSERT", receipt_payload(target))
        except Exception as e:
            logger.error(f"Error in trade_delivery_receipt after_insert event: {e}")

    @event.listens_for(TradeDeliveryReceipt, 'after_update')
    def on_trade_delivery_receipt_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "trade_delivery_receipts", target.id, "UPDATE", receipt_payload(target))
        except Exception as e:
            logger.error(f"Error in trade_delivery_receipt after_update event: {e}")

    @event.listens_for(TradeDeliveryReceipt, 'after_delete')
    def on_trade_delivery_receipt_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id, "dedupe_key": target.dedupe_key, "trade_number": target.trade_number}
            log_change(connection, "trade_delivery_receipts", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in trade_delivery_receipt after_delete event: {e}")

    logger.info("✅ TradeDeliveryReceipt event listeners registered")


def setup_telegram_admin_broadcast_events():
    """Setup event listeners for Telegram admin broadcast models."""
    from models.telegram_admin_broadcast import TelegramAdminBroadcast, TelegramAdminBroadcastReceipt

    def _enum_value(value):
        return value.value if hasattr(value, "value") else value

    def broadcast_payload(target) -> Dict[str, Any]:
        return {
            "id": target.id,
            "content": target.content,
            "created_by_id": target.created_by_id,
            "audience_type": _enum_value(target.audience_type),
            "target_groups": target.target_groups or [],
            "recipient_count": target.recipient_count,
            "status": _enum_value(target.status),
            "queued_at": _isoformat_or_none(getattr(target, "queued_at", None)),
            "completed_at": _isoformat_or_none(getattr(target, "completed_at", None)),
            "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
            "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
        }

    def receipt_payload(target) -> Dict[str, Any]:
        return {
            "id": target.id,
            "broadcast_id": target.broadcast_id,
            "recipient_user_id": target.recipient_user_id,
            "telegram_id_at_enqueue": target.telegram_id_at_enqueue,
            "telegram_id_at_send": target.telegram_id_at_send,
            "dedupe_key": target.dedupe_key,
            "status": _enum_value(target.status),
            "reason": target.reason,
            "telegram_message_id": target.telegram_message_id,
            "attempt_count": target.attempt_count,
            "next_retry_at": _isoformat_or_none(getattr(target, "next_retry_at", None)),
            "last_error_class": target.last_error_class,
            "last_error_message": target.last_error_message,
            "worker_id": target.worker_id,
            "lease_until": _isoformat_or_none(getattr(target, "lease_until", None)),
            "sent_at": _isoformat_or_none(getattr(target, "sent_at", None)),
            "terminal_at": _isoformat_or_none(getattr(target, "terminal_at", None)),
            "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
            "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
        }

    @event.listens_for(TelegramAdminBroadcast, 'after_insert')
    def on_telegram_admin_broadcast_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_admin_broadcasts", target.id, "INSERT", broadcast_payload(target))
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast after_insert event: {e}")

    @event.listens_for(TelegramAdminBroadcast, 'after_update')
    def on_telegram_admin_broadcast_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_admin_broadcasts", target.id, "UPDATE", broadcast_payload(target))
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast after_update event: {e}")

    @event.listens_for(TelegramAdminBroadcast, 'after_delete')
    def on_telegram_admin_broadcast_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_admin_broadcasts", target.id, "DELETE", {"id": target.id})
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast after_delete event: {e}")

    @event.listens_for(TelegramAdminBroadcastReceipt, 'after_insert')
    def on_telegram_admin_broadcast_receipt_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "telegram_admin_broadcast_receipts",
                target.id,
                "INSERT",
                receipt_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast_receipt after_insert event: {e}")

    @event.listens_for(TelegramAdminBroadcastReceipt, 'after_update')
    def on_telegram_admin_broadcast_receipt_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "telegram_admin_broadcast_receipts",
                target.id,
                "UPDATE",
                receipt_payload(target),
            )
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast_receipt after_update event: {e}")

    @event.listens_for(TelegramAdminBroadcastReceipt, 'after_delete')
    def on_telegram_admin_broadcast_receipt_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id, "dedupe_key": target.dedupe_key, "broadcast_id": target.broadcast_id}
            log_change(connection, "telegram_admin_broadcast_receipts", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in telegram_admin_broadcast_receipt after_delete event: {e}")

    logger.info("✅ TelegramAdminBroadcast event listeners registered")


def setup_telegram_notification_outbox_events():
    """Setup event listeners for generic Telegram notification outbox rows."""
    from models.telegram_notification_outbox import TelegramNotificationOutbox

    def _enum_value(value):
        return value.value if hasattr(value, "value") else value

    def outbox_payload(target) -> Dict[str, Any]:
        return {
            "id": target.id,
            "dedupe_key": target.dedupe_key,
            "source_type": target.source_type,
            "source_id": target.source_id,
            "recipient_user_id": target.recipient_user_id,
            "telegram_id_at_enqueue": target.telegram_id_at_enqueue,
            "telegram_id_at_send": target.telegram_id_at_send,
            "text": target.text,
            "parse_mode": target.parse_mode,
            "status": _enum_value(target.status),
            "reason": target.reason,
            "telegram_message_id": target.telegram_message_id,
            "attempt_count": target.attempt_count,
            "next_retry_at": _isoformat_or_none(getattr(target, "next_retry_at", None)),
            "last_error_class": target.last_error_class,
            "last_error_message": target.last_error_message,
            "worker_id": target.worker_id,
            "lease_until": _isoformat_or_none(getattr(target, "lease_until", None)),
            "sent_at": _isoformat_or_none(getattr(target, "sent_at", None)),
            "terminal_at": _isoformat_or_none(getattr(target, "terminal_at", None)),
            "extra_payload": target.extra_payload,
            "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
            "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
        }

    @event.listens_for(TelegramNotificationOutbox, 'after_insert')
    def on_telegram_notification_outbox_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_notification_outbox", target.id, "INSERT", outbox_payload(target))
        except Exception as e:
            logger.error(f"Error in telegram_notification_outbox after_insert event: {e}")

    @event.listens_for(TelegramNotificationOutbox, 'after_update')
    def on_telegram_notification_outbox_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_notification_outbox", target.id, "UPDATE", outbox_payload(target))
        except Exception as e:
            logger.error(f"Error in telegram_notification_outbox after_update event: {e}")

    @event.listens_for(TelegramNotificationOutbox, 'after_delete')
    def on_telegram_notification_outbox_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {"id": target.id, "dedupe_key": target.dedupe_key}
            log_change(connection, "telegram_notification_outbox", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in telegram_notification_outbox after_delete event: {e}")

    logger.info("✅ TelegramNotificationOutbox event listeners registered")


def setup_user_events():
    """Setup event listeners for User model"""
    from models.user import User

    def user_payload(target, *, include_created_at: bool) -> Dict[str, Any]:
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
            "bot_onboarding_required_step": getattr(target, "bot_onboarding_required_step", 0),
            "bot_onboarding_completed_step": getattr(target, "bot_onboarding_completed_step", 0),
            "bot_onboarding_completed_at": target.bot_onboarding_completed_at.isoformat() if getattr(target, "bot_onboarding_completed_at", None) else None,
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
            "counter_epoch": int(getattr(target, "counter_epoch", 1) or 1),
            "max_sessions": target.max_sessions,
            "max_accountants": getattr(target, "max_accountants", 3),
            "max_customers": getattr(target, "max_customers", 5),
            "last_seen_at": target.last_seen_at.isoformat() if target.last_seen_at else None,
            "sync_version": int(getattr(target, "sync_version", 1) or 1),
        }
        if include_created_at:
            data["created_at"] = target.created_at.isoformat() if target.created_at else None
        else:
            data["updated_at"] = target.updated_at.isoformat() if target.updated_at else None
        return data

    @event.listens_for(User, 'before_update')
    def bump_user_sync_version(mapper, connection, target):
        if not _registration_sync_v2_enabled():
            return
        source_server = str(getattr(settings, "server_mode", "") or "").strip().lower()
        changed = _changed_column_fields(target)
        if source_server == "foreign":
            allowed = set(allowed_user_fields_for_source("foreign"))
            allowed.update(USER_SYNC_COUNTER_FIELDS)
            unauthorized = changed - allowed - {"sync_version", "updated_at"}
            if unauthorized:
                raise RuntimeError(
                    "foreign_user_write_authority_forbidden:"
                    + ",".join(sorted(unauthorized))
                )
            return
        if source_server != "iran":
            return
        allowed = allowed_user_fields_for_source("iran")
        if changed & (set(allowed) - {"id", "created_at", "updated_at", "sync_version"}):
            _bump_sync_version(target)

    @event.listens_for(User, 'after_insert')
    def on_user_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            source_server = str(getattr(settings, "server_mode", "") or "").strip().lower()
            if _registration_sync_v2_enabled() and source_server != "iran":
                logger.warning(
                    "Suppressed non-authoritative User insert sync event",
                    extra={"event": "sync.user_insert_suppressed", "source_server": settings.server_mode},
                )
                return
            data = user_payload(target, include_created_at=True)
            if _registration_sync_v2_enabled():
                allowed = allowed_user_fields_for_source(source_server)
                data = {key: value for key, value in data.items() if key in allowed}
                data[USER_SYNC_IDENTITY_FIELD] = build_user_sync_identity(
                    target,
                    include_previous=False,
                )
            else:
                # Keep the compatibility stream genuinely unversioned. A peer
                # may have v2 code deployed but its emitter flag still off.
                data.pop("sync_version", None)
            log_change(connection, "users", target.id, "INSERT", data)
            logger.info(f"📡 Published sync: user:created ID={target.id}")
        except Exception as e:
            logger.error(f"Error in user after_insert event: {e}")

    @event.listens_for(User, 'after_update')
    def on_user_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = user_payload(target, include_created_at=False)
            if _registration_sync_v2_enabled():
                source_server = str(getattr(settings, "server_mode", "") or "").strip().lower()
                changed = _changed_column_fields(target)
                counter_event = build_user_counter_event(target, changed)
                allowed = allowed_user_fields_for_source(source_server)
                changed_allowed = changed & set(allowed)
                unauthorized = sorted(
                    changed
                    - set(allowed)
                    - set(USER_SYNC_COUNTER_FIELDS)
                    - {"counter_epoch"}
                    - {"sync_version", "updated_at"}
                )
                if unauthorized:
                    logger.warning(
                        "Dropped unauthorized User sync fields",
                        extra={
                            "event": "sync.user_patch_fields_dropped",
                            "source_server": source_server,
                            "dropped_fields": unauthorized,
                        },
                    )
                data = {
                    key: value
                    for key, value in data.items()
                    if key in changed_allowed or key in {"id", "sync_version", "updated_at"}
                }
                for counter_field in USER_SYNC_COUNTER_FIELDS:
                    data.pop(counter_field, None)
                has_user_patch = bool(set(data) - {"id", "sync_version", "updated_at"})
                if has_user_patch:
                    data[USER_SYNC_IDENTITY_FIELD] = build_user_sync_identity(
                        target,
                        include_previous=True,
                    )
                    log_change(connection, "users", target.id, "UPDATE", data)
                if counter_event is not None:
                    _record_local_user_counter_event(
                        connection,
                        target,
                        counter_event,
                        source_server,
                    )
                    log_change(
                        connection,
                        "users",
                        target.id,
                        "UPDATE",
                        user_counter_event_payload(target, counter_event),
                    )
                return
            data.pop("sync_version", None)
            log_change(connection, "users", target.id, "UPDATE", data)
        except InvalidUserCounterMutation:
            raise
        except Exception as e:
            logger.error(f"Error in user after_update event: {e}")
            if _registration_sync_v2_enabled():
                raise

    logger.info("✅ User event listeners registered")


def setup_accountant_relation_events():
    """Setup event listeners for AccountantRelation model."""
    from models.accountant_relation import AccountantRelation

    def accountant_relation_payload(connection, target):
        status = target.status.value if hasattr(target.status, "value") else target.status
        payload = {
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
            "sync_version": int(getattr(target, "sync_version", 1) or 1),
        }
        references = _registration_user_references(
            connection,
            {
                "owner_user_id": target.owner_user_id,
                "accountant_user_id": target.accountant_user_id,
                "created_by_user_id": target.created_by_user_id,
            },
        )
        if references:
            payload[REGISTRATION_USER_REFERENCES_FIELD] = references
        return payload

    @event.listens_for(AccountantRelation, 'before_update')
    def bump_accountant_relation_sync_version(mapper, connection, target):
        if (
            _registration_sync_v2_enabled()
            and str(getattr(settings, "server_mode", "") or "").strip().lower() == "iran"
            and (_changed_column_fields(target) - {"sync_version", "updated_at"})
        ):
            _bump_sync_version(target)

    @event.listens_for(AccountantRelation, 'after_insert')
    def on_accountant_relation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "accountant_relations", target.id, "INSERT", accountant_relation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in accountant_relation after_insert event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(AccountantRelation, 'after_update')
    def on_accountant_relation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "accountant_relations", target.id, "UPDATE", accountant_relation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in accountant_relation after_update event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(AccountantRelation, 'after_delete')
    def on_accountant_relation_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "accountant_relations",
                target.id,
                "DELETE",
                {
                    "id": target.id,
                    "invitation_token": target.invitation_token,
                    "owner_user_id": target.owner_user_id,
                    "accountant_user_id": target.accountant_user_id,
                },
            )
        except Exception as e:
            logger.error(f"Error in accountant_relation after_delete event: {e}")

    logger.info("✅ AccountantRelation event listeners registered")


def setup_customer_relation_events():
    """Setup event listeners for CustomerRelation model."""
    from models.customer_relation import CustomerRelation

    def customer_relation_payload(connection, target):
        status = target.status.value if hasattr(target.status, "value") else target.status
        customer_tier = target.customer_tier.value if hasattr(target.customer_tier, "value") else target.customer_tier
        commission_rate = target.commission_rate
        if commission_rate is not None:
            commission_rate = str(commission_rate)
        payload = {
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
            "sync_version": int(getattr(target, "sync_version", 1) or 1),
        }
        references = _registration_user_references(
            connection,
            {
                "owner_user_id": target.owner_user_id,
                "customer_user_id": target.customer_user_id,
                "created_by_user_id": target.created_by_user_id,
            },
        )
        if references:
            payload[REGISTRATION_USER_REFERENCES_FIELD] = references
        return payload

    @event.listens_for(CustomerRelation, 'before_update')
    def bump_customer_relation_sync_version(mapper, connection, target):
        if (
            _registration_sync_v2_enabled()
            and str(getattr(settings, "server_mode", "") or "").strip().lower() == "iran"
            and (_changed_column_fields(target) - {"sync_version", "updated_at"})
        ):
            _bump_sync_version(target)

    @event.listens_for(CustomerRelation, 'after_insert')
    def on_customer_relation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "customer_relations", target.id, "INSERT", customer_relation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in customer_relation after_insert event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(CustomerRelation, 'after_update')
    def on_customer_relation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "customer_relations", target.id, "UPDATE", customer_relation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in customer_relation after_update event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(CustomerRelation, 'after_delete')
    def on_customer_relation_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "customer_relations",
                target.id,
                "DELETE",
                {
                    "id": target.id,
                    "invitation_token": target.invitation_token,
                    "owner_user_id": target.owner_user_id,
                    "customer_user_id": target.customer_user_id,
                },
            )
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
            data = {"id": target.id, "name": target.name}
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
                "commodity_id": target.commodity_id,
                "commodity_name": _lookup_commodity_name(connection, target.commodity_id),
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
                "commodity_id": target.commodity_id,
                "commodity_name": _lookup_commodity_name(connection, target.commodity_id),
            }
            log_change(connection, "commodity_aliases", target.id, "UPDATE", data)
        except Exception as e:
            logger.error(f"Error in alias after_update event: {e}")

    @event.listens_for(CommodityAlias, 'after_delete')
    def on_alias_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            data = {
                "id": target.id,
                "alias": target.alias,
                "commodity_id": target.commodity_id,
                "commodity_name": _lookup_commodity_name(connection, target.commodity_id),
            }
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
            data = {
                "id": target.id,
                "blocker_id": target.blocker_id,
                "blocked_id": target.blocked_id,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            log_change(connection, "user_blocks", target.id, "DELETE", data)
        except Exception as e:
            logger.error(f"Error in user_block after_delete event: {e}")

    logger.info("✅ UserBlock event listeners registered")


def setup_telegram_link_token_events():
    """Setup event listeners for WebApp-issued Telegram account-link tokens."""
    from models.telegram_link_token import TelegramLinkToken

    def telegram_link_token_payload(connection, target):
        status = getattr(target, "status", None)
        payload = {
            "id": target.id,
            "user_id": target.user_id,
            "token_hash": target.token_hash,
            "status": status.value if hasattr(status, "value") else status,
            "issued_by_server": target.issued_by_server,
            "expires_at": _isoformat_or_none(getattr(target, "expires_at", None)),
            "used_at": _isoformat_or_none(getattr(target, "used_at", None)),
            "used_telegram_id": getattr(target, "used_telegram_id", None),
            "revoked_at": _isoformat_or_none(getattr(target, "revoked_at", None)),
            "created_at": _isoformat_or_none(getattr(target, "created_at", None)),
            "updated_at": _isoformat_or_none(getattr(target, "updated_at", None)),
        }
        references = _registration_user_references(
            connection,
            {"user_id": target.user_id},
        )
        if references:
            payload[REGISTRATION_USER_REFERENCES_FIELD] = references
        return payload

    @event.listens_for(TelegramLinkToken, 'after_insert')
    def on_telegram_link_token_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_link_tokens", target.id, "INSERT", telegram_link_token_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in telegram_link_token after_insert event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(TelegramLinkToken, 'after_update')
    def on_telegram_link_token_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "telegram_link_tokens", target.id, "UPDATE", telegram_link_token_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in telegram_link_token after_update event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(TelegramLinkToken, 'after_delete')
    def on_telegram_link_token_deleted(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(
                connection,
                "telegram_link_tokens",
                target.id,
                "DELETE",
                {"id": target.id, "token_hash": target.token_hash, "user_id": target.user_id},
            )
        except Exception as e:
            logger.error(f"Error in telegram_link_token after_delete event: {e}")

    logger.info("✅ TelegramLinkToken event listeners registered")


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
            log_change(
                connection,
                "market_schedule_overrides",
                target.id,
                "DELETE",
                {
                    "id": target.id,
                    "date": target.date.isoformat() if getattr(target, "date", None) else None,
                },
            )
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

    def invitation_payload(connection, target):
        payload = {
            "id": target.id,
            "account_name": target.account_name,
            "mobile_number": target.mobile_number,
            "token": target.token,
            "short_code": target.short_code,
            "role": target.role.value if target.role else None,
            "created_by_id": target.created_by_id,
            "is_used": target.is_used,
            "expires_at": target.expires_at.isoformat() if target.expires_at else None,
            "kind": getattr(getattr(target, "kind", None), "value", getattr(target, "kind", None)),
            "registered_user_id": getattr(target, "registered_user_id", None),
            "completed_at": target.completed_at.isoformat() if getattr(target, "completed_at", None) else None,
            "completed_via": getattr(
                getattr(target, "completed_via", None),
                "value",
                getattr(target, "completed_via", None),
            ),
            "revoked_at": target.revoked_at.isoformat() if getattr(target, "revoked_at", None) else None,
            "sync_version": int(getattr(target, "sync_version", 1) or 1),
            "created_at": target.created_at.isoformat() if target.created_at else None,
            "updated_at": target.updated_at.isoformat() if getattr(target, "updated_at", None) else None,
        }
        references = _registration_user_references(
            connection,
            {
                "created_by_id": target.created_by_id,
                "registered_user_id": getattr(target, "registered_user_id", None),
            },
        )
        if references:
            payload[REGISTRATION_USER_REFERENCES_FIELD] = references
        return payload

    @event.listens_for(Invitation, 'before_update')
    def bump_invitation_sync_version(mapper, connection, target):
        if (
            _registration_sync_v2_enabled()
            and str(getattr(settings, "server_mode", "") or "").strip().lower() == "iran"
            and (_changed_column_fields(target) - {"sync_version", "updated_at"})
        ):
            _bump_sync_version(target)

    @event.listens_for(Invitation, 'after_insert')
    def on_invitation_created(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "invitations", target.id, "INSERT", invitation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in invitation after_insert event: {e}")
            if _registration_sync_v2_enabled():
                raise

    @event.listens_for(Invitation, 'after_update')
    def on_invitation_updated(mapper, connection, target):
        if connection.get_execution_options().get("is_sync"):
            return
        try:
            log_change(connection, "invitations", target.id, "UPDATE", invitation_payload(connection, target))
        except Exception as e:
            logger.error(f"Error in invitation after_update event: {e}")
            if _registration_sync_v2_enabled():
                raise

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
            "dedupe_key": target.dedupe_key,
            "extra_payload": target.extra_payload,
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
            data = {"id": target.id, "user_id": target.user_id, "dedupe_key": target.dedupe_key}
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
    setup_offer_publication_state_events()
    setup_trade_events()
    setup_trade_delivery_receipt_events()
    setup_telegram_admin_broadcast_events()
    setup_telegram_notification_outbox_events()
    setup_commodity_events()
    setup_commodity_alias_events()
    setup_trading_settings_events()
    setup_market_schedule_override_events()
    setup_market_runtime_state_events()
    setup_user_block_events()
    setup_telegram_link_token_events()
    setup_notification_events()
    setup_user_notification_preference_events()
    setup_admin_message_events()
    logger.info("🎯 All event listeners initialized")


# Alias for main.py / startup
setup_event_listeners = setup_all_events
