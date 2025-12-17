# src/core/repositories/offer_repository.py
"""Offer Repository Interface"""

from typing import Protocol, Optional, List
from src.core.entities.offer import OfferEntity, OfferStatus


class IOfferRepository(Protocol):
    """اینترفیس Repository لفظ"""
    
    async def get_by_id(self, offer_id: int) -> Optional[OfferEntity]:
        """دریافت لفظ با آیدی"""
        ...
    
    async def get_by_user(self, user_id: int, status: Optional[OfferStatus] = None) -> List[OfferEntity]:
        """دریافت لفظ‌های کاربر"""
        ...
    
    async def get_active(self, limit: int = 100) -> List[OfferEntity]:
        """دریافت لفظ‌های فعال"""
        ...
    
    async def create(self, offer: OfferEntity) -> OfferEntity:
        """ایجاد لفظ جدید"""
        ...
    
    async def update(self, offer: OfferEntity) -> OfferEntity:
        """بروزرسانی لفظ"""
        ...
    
    async def delete(self, offer_id: int) -> bool:
        """حذف لفظ"""
        ...
    
    async def count_active_by_user(self, user_id: int) -> int:
        """شمارش لفظ‌های فعال کاربر"""
        ...
    
    async def get_expired(self, minutes: int) -> List[OfferEntity]:
        """دریافت لفظ‌های منقضی شده"""
        ...
