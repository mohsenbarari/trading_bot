# models/offer.py
"""مدل لفظ (درخواست خرید/فروش در کانال)"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, DateTime, ForeignKey, Boolean, JSON
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
    
    # کاربر لفظ‌دهنده - nullable برای حفظ رکورد پس از حذف کاربر
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user = relationship("User", foreign_keys=[user_id])
    
    # نوع لفظ
    offer_type = Column(Enum(OfferType), nullable=False)
    
    # کالا
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    commodity = relationship("Commodity")
    
    # تعداد و قیمت
    quantity = Column(Integer, nullable=False)  # تعداد اولیه
    price = Column(BigInteger, nullable=False)
    
    # تعداد باقیمانده (برای فروش خُرد)
    remaining_quantity = Column(Integer, nullable=True)
    
    # فروش یکجا یا خُرد
    is_wholesale = Column(Boolean, nullable=False, default=True)  # True = یکجا، False = خُرد
    
    # لیست بخش‌ها برای فروش خُرد (JSON array مثل [10, 15, 25])
    lot_sizes = Column(JSON, nullable=True)
    
    # وضعیت لفظ
    status = Column(Enum(OfferStatus), nullable=False, default=OfferStatus.ACTIVE)
    
    # آیدی پیام در کانال
    channel_message_id = Column(BigInteger, nullable=True)
    
    # زمان ایجاد
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
