# core/events.py
"""
SQLAlchemy Event Listeners for Real-time Redis Publishing

This module automatically publishes Redis events when database models change,
enabling true real-time synchronization without manual publish calls.
"""
import json
import logging
from typing import Any, Dict
from sqlalchemy import event
from sqlalchemy.orm import Session
import redis.asyncio as redis
from core.redis import pool

logger = logging.getLogger(__name__)


def publish_event_sync(event_type: str, data: Dict[str, Any]) -> None:
    """
    Synchronous Redis publish for use in SQLAlchemy event listeners.
    
    Args:
        event_type: Event name (e.g., "offer:created")
        data: Event payload
    """
    try:
        # Create sync Redis client for use in sync context
        import redis as sync_redis
        from core.config import settings
        
        client = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True
        )
        
        channel = f"events:{event_type}"
        payload = json.dumps(data)
        client.publish(channel, payload)
        client.close()
        
        logger.info(f"📡 Published event: {event_type}")
    except Exception as e:
        logger.error(f"❌ Error publishing event {event_type}: {e}")


def setup_offer_events():
    """Setup event listeners for Offer model"""
    from models.offer import Offer
    
    @event.listens_for(Offer, 'after_insert')
    def on_offer_created(mapper, connection, target):
        """Publish offer:created event after INSERT"""
        try:
            data = {
                "id": target.id,
                "user_id": target.user_id,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            publish_event_sync("offer:created", data)
        except Exception as e:
            logger.error(f"Error in after_insert event: {e}")
    
    @event.listens_for(Offer, 'after_update')
    def on_offer_updated(mapper, connection, target):
        """Publish offer:updated event after UPDATE"""
        try:
            data = {
                "id": target.id,
                "user_id": target.user_id,
                "offer_type": target.offer_type.value if target.offer_type else None,
                "commodity_id": target.commodity_id,
                "quantity": target.quantity,
                "remaining_quantity": target.remaining_quantity,
                "price": target.price,
                "is_wholesale": target.is_wholesale,
                "lot_sizes": target.lot_sizes,
                "notes": target.notes,
                "status": target.status.value if target.status else None,
                "created_at": target.created_at.isoformat() if target.created_at else None,
            }
            
            # Check if offer was expired
            if target.status.value == "expired":
                publish_event_sync("offer:expired", {"id": target.id})
            else:
                publish_event_sync("offer:updated", data)
        except Exception as e:
            logger.error(f"Error in after_update event: {e}")
    
    @event.listens_for(Offer, 'after_delete')
    def on_offer_deleted(mapper, connection, target):
        """Publish offer:deleted event after DELETE"""
        try:
            data = {"id": target.id}
            publish_event_sync("offer:deleted", data)
        except Exception as e:
            logger.error(f"Error in after_delete event: {e}")
    
    logger.info("✅ Offer event listeners registered")


def setup_all_events():
    """Setup all event listeners"""
    setup_offer_events()
    # Add more model event setups here if needed
    logger.info("🎯 All event listeners initialized")
