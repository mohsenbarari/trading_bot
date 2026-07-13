# api/routers/realtime.py
"""
Real-time WebSocket and SSE for MiniApp - Instant Updates
"""
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import StreamingResponse
import redis.asyncio as redis
import asyncio
import json
import logging
import uuid
from typing import Any, List, Optional

from jose import jwt, JWTError

from core.redis import pool
from core.config import settings
from core.db import AsyncSessionLocal
from core.market_presence import (
    clear_market_page_presence,
    refresh_market_page_presence,
    is_market_route,
    set_market_page_presence,
)
from core.metrics import record_websocket_publish_failure, set_active_websocket_connections
from core.services.session_service import is_session_blacklisted
from core.production_test_isolation import should_block_webapp_user
from models.session import UserSession
from models.user import User
from api.deps import get_current_user



router = APIRouter(
    tags=["Real-time"],
)


# --- Realtime delivery policy ---
SENSITIVE_FIELDS = {
    "offer_user_mobile", "responder_user_mobile",
    "mobile_number", "address", "telegram_id",
    "idempotency_key",
}


PRIVATE_ONLY_EVENT_TYPES = frozenset({"trade:created"})

PUBLIC_EVENT_ALLOWED_FIELDS = {
    "offer:created": frozenset({
        "id",
        "offer_public_id",
        "public_link",
        "offer_type",
        "settlement_type",
        "commodity_id",
        "commodity_name",
        "quantity",
        "remaining_quantity",
        "price",
        "status",
        "created_at",
        "notes",
        "is_wholesale",
        "lot_sizes",
        "original_lot_sizes",
        "expires_at_ts",
    }),
    "offer:updated": frozenset({
        "id",
        "offer_public_id",
        "status",
        "remaining_quantity",
        "lot_sizes",
        "expire_reason",
        "expired_at",
    }),
    "offer:expired": frozenset({"id", "offer_public_id", "status", "expire_reason", "expired_at"}),
    "offer:cancelled": frozenset({"id", "offer_public_id", "status", "expire_reason", "expired_at"}),
    "offer:completed": frozenset({"id", "offer_public_id", "status", "remaining_quantity", "lot_sizes"}),
    "offer:deleted": frozenset({"id", "offer_public_id"}),
    "market:opened": frozenset({
        "is_open",
        "active_web_notice_visible",
        "offers_since_last_open",
        "last_transition_at",
        "next_transition_at",
        "transition",
        "notice_text",
    }),
    "market:closed": frozenset({
        "is_open",
        "active_web_notice_visible",
        "offers_since_last_open",
        "last_transition_at",
        "next_transition_at",
        "transition",
        "notice_text",
    }),
    "market:notice_hidden": frozenset({
        "is_open",
        "active_web_notice_visible",
        "offers_since_last_open",
        "last_transition_at",
        "next_transition_at",
        "transition",
        "notice_text",
    }),
    "market:admin_message_published": frozenset({
        "id",
        "content",
        "is_active",
        "published_at",
        "created_at",
        "reused_from_id",
    }),
}

WEBSOCKET_PUBLIC_EVENT_TYPES = (
    "offer:created",
    "offer:expired",
    "offer:updated",
    "offer:cancelled",
    "offer:completed",
    "market:opened",
    "market:closed",
    "market:notice_hidden",
    "market:admin_message_published",
)

SSE_PUBLIC_EVENT_TYPES = (
    "offer:created",
    "offer:expired",
    "offer:updated",
    "market:opened",
    "market:closed",
    "market:notice_hidden",
    "market:admin_message_published",
)


def sanitize_payload(data: Any) -> Any:
    """Recursively remove explicitly sensitive fields from recipient-scoped data."""
    if isinstance(data, dict):
        return {
            key: sanitize_payload(value)
            for key, value in data.items()
            if key not in SENSITIVE_FIELDS
        }
    if isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    if isinstance(data, tuple):
        return [sanitize_payload(item) for item in data]
    return data


def project_public_event_payload(event_type: str, data: Any) -> dict | None:
    """Return the event-specific public projection; unknown/private events fail closed."""
    if data is None:
        return None
    if not isinstance(data, dict):
        return {}
    allowed_fields = PUBLIC_EVENT_ALLOWED_FIELDS.get(event_type)
    if allowed_fields is None:
        return {}
    return sanitize_payload({key: value for key, value in data.items() if key in allowed_fields})


# --- WebSocket Connection Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        set_active_websocket_connections(len(self.active_connections))
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        set_active_websocket_connections(len(self.active_connections))
    
    async def broadcast(self, message: dict):
        """ارسال پیام به همه کانکشن‌های فعال"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                disconnected.append(connection)
                record_websocket_publish_failure(message.get("type", "broadcast"))
        
        # حذف کانکشن‌های بسته شده
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()

REALTIME_SOURCE_LOCAL = "local"
REALTIME_SOURCE_SYNC_APPLY = "sync_apply"


def realtime_publish_writes_outbound_sync(_source: str = REALTIME_SOURCE_LOCAL) -> bool:
    """Realtime fanout is a local delivery side effect, not a sync producer."""
    return False


async def _handle_client_message(
    data: str,
    *,
    user_id: int,
    connection_id: str,
    market_page_presence_active: bool,
) -> bool:
    if data == "ping":
        await refresh_market_page_presence(
            user_id,
            connection_id,
            active=market_page_presence_active,
        )
        return market_page_presence_active

    try:
        message = json.loads(data)
    except json.JSONDecodeError:
        return market_page_presence_active

    if not isinstance(message, dict) or message.get("type") != "presence:update":
        return market_page_presence_active

    payload = message.get("data")
    if not isinstance(payload, dict):
        payload = {}
    path = payload.get("path") if isinstance(payload.get("path"), str) else None
    visible = bool(payload.get("visible"))
    await set_market_page_presence(
        user_id,
        connection_id,
        path=path,
        visible=visible,
    )
    return bool(visible and is_market_route(path))


def verify_ws_token(token: str) -> Optional[tuple[int, Optional[str]]]:
    """
    Validate JWT token and return user_id.
    Returns None if token is invalid.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub), payload.get("sid")
    except (JWTError, ValueError, Exception):
        return None


# --- WebSocket Endpoint (Authenticated) ---
@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None)
):
    """
    WebSocket endpoint برای real-time updates آنی
    Requires JWT token as query parameter: /ws?token=<jwt>
    """
    # --- Authentication ---
    if not token:
        await websocket.close(code=4001, reason="Missing authentication token")
        return
    
    auth_result = verify_ws_token(token)
    if auth_result is None:
        await websocket.close(code=4003, reason="Invalid or expired token")
        return
    user_id, session_id = auth_result

    if session_id and await is_session_blacklisted(session_id):
        await websocket.close(code=4003, reason="Session has been revoked")
        return

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or user.is_deleted:
            await websocket.close(code=4003, reason="User is inactive")
            return
        if await should_block_webapp_user(session, user):
            await websocket.close(code=1013, reason="WebApp temporarily unavailable")
            return

        if session_id:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                await websocket.close(code=4003, reason="Invalid session")
                return

            active_session = await session.get(UserSession, session_uuid)
            if not active_session or not active_session.is_active or active_session.user_id != user_id:
                await websocket.close(code=4003, reason="Session has been revoked")
                return
    
    connection_id = uuid.uuid4().hex
    market_page_presence_active = False
    logging.info(f"✅ WebSocket authenticated: user_id={user_id}")
    await manager.connect(websocket)
    
    try:
        # شروع گوش دادن به Redis Pub/Sub در یک task جداگانه
        redis_task = asyncio.create_task(listen_redis_events(websocket, user_id))
        
        # گوش دادن به پیام‌های کلاینت (برای keep-alive)
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                market_page_presence_active = await _handle_client_message(
                    data,
                    user_id=user_id,
                    connection_id=connection_id,
                    market_page_presence_active=market_page_presence_active,
                )
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # ارسال heartbeat
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.warning(f"WebSocket error for user {user_id}: {e}")
    finally:
        manager.disconnect(websocket)
        await clear_market_page_presence(user_id, connection_id)
        redis_task.cancel()


async def listen_redis_events(websocket: WebSocket, user_id: int = None):
    """گوش دادن به رویدادهای Redis و ارسال به WebSocket"""
    logging.info(f"🔴 Redis listener started for WebSocket (user_id={user_id})")
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            pubsub = redis_client.pubsub()
            
            channels = [f"events:{event_type}" for event_type in WEBSOCKET_PUBLIC_EVENT_TYPES]
            # Subscribe to user-specific notification channel
            if user_id:
                channels.append(f"notifications:{user_id}")
            
            await pubsub.subscribe(*channels)
            logging.info(f"✅ Subscribed to Redis channels: {channels}")
            
            while True:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    
                    if message and message.get("type") == "message":
                        raw_channel = message.get("channel", "")
                        channel = raw_channel.decode("utf-8") if isinstance(raw_channel, bytes) else str(raw_channel)
                        raw_data = message.get("data", "")
                        data_str = raw_data.decode("utf-8") if isinstance(raw_data, bytes) else str(raw_data)
                        
                        try:
                            parsed_data = json.loads(data_str)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        if not isinstance(parsed_data, dict):
                            continue

                        try:
                            # User-specific notifications channel has different format.
                            if channel.startswith("notifications:"):
                                event_type = parsed_data.get("event", "notification")
                                safe_data = sanitize_payload(parsed_data.get("data", {}))
                            else:
                                event_type = channel.replace("events:", "")
                                safe_data = project_public_event_payload(event_type, parsed_data)

                            await websocket.send_json({
                                "type": event_type,
                                "data": safe_data
                            })
                        except Exception as send_err:
                            logging.error(f"❌ Error sending to WebSocket: {send_err}")
                            break
                    
                    await asyncio.sleep(0.1) 
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logging.error(f"❌ Redis listener loop error: {e}")
                    await asyncio.sleep(1)
                    
    except asyncio.CancelledError:
        logging.info("🔴 Redis listener cancelled")
    except Exception as e:
        logging.error(f"❌ Redis listener critical error: {e}")


# --- SSE Endpoint (Backup) ---
async def event_generator(user_id: int):
    """Generator برای SSE events"""
    async with redis.Redis(connection_pool=pool) as redis_client:
        pubsub = redis_client.pubsub()

        channels = [f"events:{event_type}" for event_type in SSE_PUBLIC_EVENT_TYPES]
        if user_id:
            channels.append(f"notifications:{user_id}")

        await pubsub.subscribe(*channels)
        
        heartbeat_interval = 15
        last_heartbeat = asyncio.get_event_loop().time()
        
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                
                if message and message.get("type") == "message":
                    raw_channel = message.get("channel", "")
                    channel = raw_channel.decode("utf-8") if isinstance(raw_channel, bytes) else str(raw_channel)
                    raw_data = message.get("data", "")
                    data = raw_data.decode("utf-8") if isinstance(raw_data, bytes) else str(raw_data)
                    event_type = channel.replace("events:", "")

                    # Project public events and sanitize recipient-scoped events.
                    try:
                        parsed = json.loads(data)
                        if not isinstance(parsed, dict):
                            raise ValueError("Realtime event payload must be an object")
                        if channel.startswith("notifications:"):
                            event_type = parsed.get("event", "notification")
                            safe = sanitize_payload(parsed.get("data", {}))
                        else:
                            safe = project_public_event_payload(event_type, parsed)
                        data = json.dumps(safe)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        data = "{}"
                    
                    yield f"event: {event_type}\ndata: {data}\n\n"
                
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat > heartbeat_interval:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
                    last_heartbeat = current_time
                    
                await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            await pubsub.unsubscribe()
            raise
        finally:
            await pubsub.unsubscribe()


@router.get("/stream")
async def sse_stream(request: Request, current_user: User = Depends(get_current_user)):
    """SSE endpoint (backup for browsers without WebSocket)"""
    return StreamingResponse(
        event_generator(current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# --- Helper function برای broadcast رویدادها ---
async def publish_event(event_type: str, data: dict | None, *, source: str = REALTIME_SOURCE_LOCAL):
    """Publish a local realtime event without writing an outbound sync event."""
    if event_type in PRIVATE_ONLY_EVENT_TYPES or event_type not in PUBLIC_EVENT_ALLOWED_FIELDS:
        logging.warning("Blocked non-public event from public realtime channel: %s", event_type)
        return

    public_data = project_public_event_payload(event_type, data)
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            channel = f"events:{event_type}"
            await redis_client.publish(channel, json.dumps(public_data, ensure_ascii=False, default=str))
    except Exception as e:
        record_websocket_publish_failure(event_type)
        logging.warning(f"⚠️ Error publishing event {event_type}: {e}")

    try:
        await manager.broadcast({
            "type": event_type,
            "data": public_data
        })
    except Exception as e:
        record_websocket_publish_failure(event_type)
        logging.warning(f"⚠️ Error broadcasting event {event_type}: {e}")


async def publish_user_event(user_id: int | None, event_type: str, data: dict):
    """Publish a user-scoped realtime event via the notifications channel."""
    if not user_id:
        return

    payload = {
        "event": event_type,
        "data": data,
    }

    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            await redis_client.publish(
                f"notifications:{int(user_id)}",
                json.dumps(payload, ensure_ascii=False, default=str),
            )
    except Exception as e:
        record_websocket_publish_failure(event_type)
        logging.warning(f"⚠️ Error publishing user event {event_type} for user {user_id}: {e}")
