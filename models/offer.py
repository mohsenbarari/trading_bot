# models/offer.py
"""مدل لفظ (درخواست خرید/فروش در کانال)"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum


class OfferType(str, enum.Enum):
    BUY = "buy"    # خرید
    SELL = "sell"  # فروش


class OfferStatus(str, enum.Enum):
    ACTIVE = "active"      # فعال - در انتظار معامله
    COMPLETED = "completed" # تکمیل شده - معامله انجام شد
    CANCELLED = "cancelled" # لغو شده
    EXPIRED = "expired"    # منقضی شده


class Offer(Base):
    """لفظ - درخواست خرید/فروش در کانال"""
    __tablename__ = "offers"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # کاربر لفظ‌دهنده
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", foreign_keys=[user_id])
    
    # نوع لفظ
    offer_type = Column(Enum(OfferType), nullable=False)
    
    # کالا
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    commodity = relationship("Commodity")
    
    # تعداد و قیمت
    quantity = Column(Integer, nullable=False)
    price = Column(BigInteger, nullable=False)
    
    # وضعیت لفظ
    status = Column(Enum(OfferStatus), nullable=False, default=OfferStatus.ACTIVE)
    
    # آیدی پیام در کانال
    channel_message_id = Column(BigInteger, nullable=True)
    
    # زمان ایجاد
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
