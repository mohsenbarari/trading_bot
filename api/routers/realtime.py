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
from typing import List, Dict, Set, Optional

from jose import jwt, JWTError

from core.redis import pool
from core.config import settings
from models.user import User
from api.deps import get_current_user_optional



router = APIRouter(
    tags=["Real-time"],
)


# --- Sensitive fields to strip from broadcast payloads ---
SENSITIVE_FIELDS = {
    "offer_user_mobile", "responder_user_mobile",
    "mobile_number", "address", "telegram_id",
    "idempotency_key",
}


def sanitize_payload(data: dict) -> dict:
    """Remove sensitive fields from broadcast data"""
    if not isinstance(data, dict):
        return data
    return {k: v for k, v in data.items() if k not in SENSITIVE_FIELDS}


# --- WebSocket Connection Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        """ارسال پیام به همه کانکشن‌های فعال"""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                disconnected.append(connection)
        
        # حذف کانکشن‌های بسته شده
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


def verify_ws_token(token: str) -> Optional[int]:
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
        return int(sub)
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
    
    user_id = verify_ws_token(token)
    if user_id is None:
        await websocket.close(code=4003, reason="Invalid or expired token")
        return
    
    logging.info(f"✅ WebSocket authenticated: user_id={user_id}")
    await manager.connect(websocket)
    
    try:
        # شروع گوش دادن به Redis Pub/Sub در یک task جداگانه
        redis_task = asyncio.create_task(listen_redis_events(websocket, user_id))
        
        # گوش دادن به پیام‌های کلاینت (برای keep-alive)
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # پیام ping/pong برای keep alive
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
        redis_task.cancel()


async def listen_redis_events(websocket: WebSocket, user_id: int = None):
    """گوش دادن به رویدادهای Redis و ارسال به WebSocket"""
    logging.info(f"🔴 Redis listener started for WebSocket (user_id={user_id})")
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            pubsub = redis_client.pubsub()
            
            channels = [
                "events:offer:created",
                "events:offer:expired",
                "events:offer:updated",
                "events:offer:cancelled",
                "events:offer:completed",
                "events:trade:created"
            ]
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
                            
                            # User-specific notifications channel has different format
                            if channel.startswith("notifications:"):
                                event_type = parsed_data.get("event", "notification")
                                safe_data = sanitize_payload(parsed_data.get("data", {}))
                            else:
                                event_type = channel.replace("events:", "")
                                safe_data = sanitize_payload(parsed_data)
                            
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
        
        await pubsub.subscribe(
            "events:offer:created",
            "events:offer:expired",
            "events:offer:updated",
            "events:trade:created"
        )
        
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
                    
                    # Sanitize SSE data too
                    try:
                        parsed = json.loads(data)
                        safe = sanitize_payload(parsed)
                        data = json.dumps(safe)
                    except:
                        pass
                    
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
async def sse_stream(request: Request, current_user: User = Depends(get_current_user_optional)):
    """SSE endpoint (backup for browsers without WebSocket)"""
    user_id = current_user.id if current_user else 0
    
    return StreamingResponse(
        event_generator(user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# --- Helper function برای broadcast رویدادها ---
async def publish_event(event_type: str, data: dict):
    """Publish یک رویداد به Redis برای real-time sync"""
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            channel = f"events:{event_type}"
            await redis_client.publish(channel, json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        logging.warning(f"⚠️ Error publishing event {event_type}: {e}")

    try:
        # Sanitize before direct broadcast too
        safe_data = sanitize_payload(data)
        await manager.broadcast({
            "type": event_type,
            "data": safe_data
        })
    except Exception as e:
        logging.warning(f"⚠️ Error broadcasting event {event_type}: {e}")
