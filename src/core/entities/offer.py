# src/core/entities/offer.py
"""موجودیت لفظ - بدون وابستگی به ORM"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum


class OfferType(str, Enum):
    """نوع لفظ"""
    BUY = "buy"
    SELL = "sell"


class OfferStatus(str, Enum):
    """وضعیت لفظ"""
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class OfferEntity:
    """موجودیت لفظ - Pure Python"""
    id: Optional[int] = None
    user_id: int = 0
    offer_type: OfferType = OfferType.BUY
    commodity_id: int = 0
    quantity: int = 0
    price: int = 0
    remaining_quantity: Optional[int] = None
    is_wholesale: bool = True
    lot_sizes: Optional[List[int]] = None
    status: OfferStatus = OfferStatus.ACTIVE
    notes: Optional[str] = None
    channel_message_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
