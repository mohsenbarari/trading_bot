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
TABLE_ORDER = {"users": 0, "commodities": 1, "offers": 2, "trades": 3}

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

from sqlalchemy import insert, update, delete
from models.user import User
from models.offer import Offer
from models.trade import Trade
from models.commodity import Commodity

def get_model_class(table_name: str):
    mapping = {
        "users": User,
        "offers": Offer,
        "trades": Trade,
        "commodities": Commodity
    }
    return mapping.get(table_name)

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
    
    try:
        for item in sorted_items:
            # Handle Notification Relay
            if item.get("type") == "notification":
                try:
                    from core.notifications import send_telegram_message
                    chat_id = item.get("chat_id")
                    text = item.get("text")
                    parse_mode = item.get("parse_mode", "Markdown")
                    
                    if chat_id and text:
                        # On foreign server, this acts as direct sender
                        await send_telegram_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                        processed_count += 1
                        logger.info(f"✅ Notification relayed to {chat_id}")
                    else:
                        logger.warning(f"Invalid notification payload: {item}")
                except Exception as e:
                    logger.error(f"Failed to relay notification: {e}")
                    pass
                continue


            table = item.get('table')
            operation = item.get('operation')
            data = item.get('data')
            record_id = item.get('id')
            
            model = get_model_class(table)
            if not model:
                logger.warning(f"Unknown table in sync: {table}")
                continue
                
            if isinstance(data, str):
                data = json.loads(data)
                
            # Handle datetime parsing
            from datetime import datetime
            
            for key, value in data.items():
                if isinstance(value, str) and key.endswith('_at'):
                     try:
                         data[key] = datetime.fromisoformat(value)
                     except ValueError:
                         pass
            

            try:
                if operation in ("INSERT", "UPDATE"):
                    data['id'] = record_id
                    
                    from sqlalchemy.dialects.postgresql import insert as pg_insert
                    stmt = pg_insert(model).values(**data)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['id'],
                        set_=data
                    )
                    await db.execute(stmt, execution_options={"is_sync": True})
                    
                elif operation == "DELETE":
                    stmt = delete(model).where(model.id == record_id)
                    await db.execute(stmt, execution_options={"is_sync": True})
                    
                processed_count += 1
                logger.info(f"✅ Sync Item Applied: {table}:{record_id} ({operation})")
                
            except Exception as e:
                error_msg = f"Failed to apply item {table}:{record_id}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
        
        await db.commit()
        
        if errors:
            return {"status": "partial", "processed": processed_count, "errors": len(errors)}
        return {"status": "success", "processed": processed_count}
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error processing sync batch: {e}")
        raise HTTPException(status_code=500, detail=str(e))
