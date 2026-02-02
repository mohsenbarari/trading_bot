# api/routers/realtime.py
"""
Real-time WebSocket and SSE for MiniApp - Instant Updates
"""
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import redis.asyncio as redis
import asyncio
import json
import logging
from typing import List, Dict, Set

from core.redis import pool
from models.user import User
from .auth import get_current_user_optional


router = APIRouter(
    prefix="/realtime",
    tags=["Real-time"],
)


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


# --- WebSocket Endpoint ---
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint برای real-time updates آنی
    """
    await manager.connect(websocket)
    
    try:
        # شروع گوش دادن به Redis Pub/Sub در یک task جداگانه
        redis_task = asyncio.create_task(listen_redis_events(websocket))
        
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
        print(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)
        redis_task.cancel()


async def listen_redis_events(websocket: WebSocket):
    """گوش دادن به رویدادهای Redis و ارسال به WebSocket"""
    logging.info(f"🔴 Redis listener started for WebSocket")
    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            pubsub = redis_client.pubsub()
            
            await pubsub.subscribe(
                "events:offer:created",
                "events:offer:expired",
                "events:offer:updated",
                "events:trade:created"
            )
            logging.info(f"✅ Subscribed to Redis channels")
            
            while True:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message:
                        print(f"🔍 DEBUG APP: RAW REDIS MSG: {message}", flush=True)
                    
                    if message and message.get("type") == "message":
                        channel = message.get("channel", b"").decode("utf-8")
                        data_str = message.get("data", b"").decode("utf-8")
                        event_type = channel.replace("events:", "")
                        
                        logging.info(f"📩 Redis message received: {event_type}")
                        
                        try:
                            # Send plain JSON, client parses it
                            await websocket.send_json({
                                "type": event_type,
                                "data": json.loads(data_str)
                            })
                            logging.info(f"✅ Sent to WebSocket: {event_type}")
                        except Exception as send_err:
                            logging.error(f"❌ Error sending to WebSocket: {send_err}")
                            break
                    
                    await asyncio.sleep(0.1) 
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logging.error(f"❌ Redis listener loop error: {e}")
                    # Don't break immediately on minor errors, but break on critical ones?
                    # For now, sleep and retry to be robust
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
                    channel = message.get("channel", b"").decode("utf-8")
                    data = message.get("data", b"").decode("utf-8")
                    event_type = channel.replace("events:", "")
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
            await redis_client.publish(channel, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        print(f"⚠️ Error publishing event {event_type}: {e}")


    await manager.broadcast({
        "type": event_type,
        "data": data
    })
