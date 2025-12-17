# src/core/services/offer_service.py
"""سرویس لفظ - بدون وابستگی به ORM یا Framework"""

from typing import Optional, List
from src.core.repositories.offer_repository import IOfferRepository
from src.core.repositories.user_repository import IUserRepository
from src.core.entities.offer import OfferEntity, OfferStatus, OfferType
from src.core.schemas.offer_schemas import OfferCreate, OfferUpdate, OfferResponse
from src.core.exceptions.base import NotFoundError, ValidationError
from src.core.exceptions.user_exceptions import MaxActiveOffersError


class OfferService:
    """
    سرویس لفظ - Use Cases
    
    بدون وابستگی به aiogram، FastAPI یا SQLAlchemy.
    """
    
    def __init__(
        self, 
        offer_repo: IOfferRepository,
        user_repo: IUserRepository,
        max_active_offers: int = 3
    ):
        self._offer_repo = offer_repo
        self._user_repo = user_repo
        self._max_active = max_active_offers
    
    async def get_offer(self, offer_id: int) -> OfferResponse:
        """دریافت لفظ با آیدی"""
        offer = await self._offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("لفظ", offer_id)
        return self._to_response(offer)
    
    async def get_user_offers(
        self, 
        user_id: int, 
        status: Optional[OfferStatus] = None
    ) -> List[OfferResponse]:
        """دریافت لفظ‌های کاربر"""
        offers = await self._offer_repo.get_by_user(user_id, status)
        return [self._to_response(o) for o in offers]
    
    async def get_active_offers(self, limit: int = 100) -> List[OfferResponse]:
        """دریافت لفظ‌های فعال"""
        offers = await self._offer_repo.get_active(limit)
        return [self._to_response(o) for o in offers]
    
    async def create_offer(self, data: OfferCreate) -> OfferResponse:
        """ایجاد لفظ جدید"""
        # بررسی حداکثر لفظ‌های فعال
        active_count = await self._offer_repo.count_active_by_user(data.user_id)
        if active_count >= self._max_active:
            raise MaxActiveOffersError(self._max_active)
        
        # اعتبارسنجی خُرد
        if data.lot_sizes:
            if sum(data.lot_sizes) != data.quantity:
                raise ValidationError(
                    f"جمع بخش‌ها ({sum(data.lot_sizes)}) با تعداد کل ({data.quantity}) برابر نیست"
                )
        
        entity = OfferEntity(
            user_id=data.user_id,
            offer_type=data.offer_type,
            commodity_id=data.commodity_id,
            quantity=data.quantity,
            remaining_quantity=data.quantity,
            price=data.price,
            is_wholesale=data.is_wholesale,
            lot_sizes=data.lot_sizes,
            notes=data.notes,
            status=OfferStatus.ACTIVE,
        )
        
        created = await self._offer_repo.create(entity)
        return self._to_response(created)
    
    async def expire_offer(self, offer_id: int, user_id: int) -> OfferResponse:
        """منقضی کردن لفظ"""
        offer = await self._offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("لفظ", offer_id)
        
        if offer.user_id != user_id:
            raise ValidationError("شما مالک این لفظ نیستید")
        
        if offer.status != OfferStatus.ACTIVE:
            raise ValidationError("این لفظ قبلاً منقضی شده است")
        
        offer.status = OfferStatus.EXPIRED
        updated = await self._offer_repo.update(offer)
        return self._to_response(updated)
    
    async def process_trade(
        self, 
        offer_id: int, 
        amount: int, 
        responder_user_id: int
    ) -> OfferResponse:
        """پردازش معامله روی لفظ"""
        offer = await self._offer_repo.get_by_id(offer_id)
        if not offer:
            raise NotFoundError("لفظ", offer_id)
        
        if offer.status != OfferStatus.ACTIVE:
            raise ValidationError("این لفظ فعال نیست")
        
        if offer.user_id == responder_user_id:
            raise ValidationError("نمی‌توانید با لفظ خود معامله کنید")
        
        remaining = offer.remaining_quantity or offer.quantity
        if amount > remaining:
            raise ValidationError(f"تعداد درخواستی ({amount}) بیشتر از موجودی ({remaining}) است")
        
        # بروزرسانی
        offer.remaining_quantity = remaining - amount
        
        # بروزرسانی لات‌ها
        if offer.lot_sizes and amount in offer.lot_sizes:
            offer.lot_sizes = [s for s in offer.lot_sizes if s != amount]
            if not offer.lot_sizes:
                offer.lot_sizes = None
        
        if offer.remaining_quantity <= 0:
            offer.status = OfferStatus.COMPLETED
        
        updated = await self._offer_repo.update(offer)
        return self._to_response(updated)
    
    def _to_response(self, entity: OfferEntity) -> OfferResponse:
        """تبدیل Entity به Response"""
        return OfferResponse(
            id=entity.id,
            user_id=entity.user_id,
            offer_type=entity.offer_type,
            commodity_id=entity.commodity_id,
            quantity=entity.quantity,
            remaining_quantity=entity.remaining_quantity or entity.quantity,
            price=entity.price,
            is_wholesale=entity.is_wholesale,
            lot_sizes=entity.lot_sizes,
            status=entity.status,
            notes=entity.notes,
            channel_message_id=entity.channel_message_id,
            created_at=entity.created_at,
        )
