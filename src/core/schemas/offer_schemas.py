# src/core/schemas/offer_schemas.py
"""Pydantic schemas برای لفظ"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from src.core.entities.offer import OfferType, OfferStatus


class OfferCreate(BaseModel):
    """ایجاد لفظ جدید"""
    user_id: int
    offer_type: OfferType
    commodity_id: int
    quantity: int = Field(..., ge=1)
    price: int = Field(..., ge=10000)
    is_wholesale: bool = True
    lot_sizes: Optional[List[int]] = None
    notes: Optional[str] = Field(None, max_length=200)


class OfferUpdate(BaseModel):
    """بروزرسانی لفظ"""
    remaining_quantity: Optional[int] = None
    lot_sizes: Optional[List[int]] = None
    status: Optional[OfferStatus] = None


class OfferResponse(BaseModel):
    """پاسخ لفظ"""
    id: int
    user_id: int
    offer_type: OfferType
    commodity_id: int
    commodity_name: Optional[str] = None
    quantity: int
    remaining_quantity: int
    price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]] = None
    status: OfferStatus
    notes: Optional[str] = None
    channel_message_id: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OfferBrief(BaseModel):
    """خلاصه لفظ"""
    id: int
    offer_type: OfferType
    commodity_name: str
    quantity: int
    price: int
    status: OfferStatus

    class Config:
        from_attributes = True
