# models/trade.py
"""مدل معامله (تراکنش واقعی بین دو کاربر)"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum


class TradeType(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, enum.Enum):
    PENDING = "pending"      # در انتظار تایید
    CONFIRMED = "confirmed"  # تایید شده
    COMPLETED = "completed"  # تکمیل شده
    CANCELLED = "cancelled"  # لغو شده


class Trade(Base):
    """معامله - تراکنش واقعی بین دو کاربر"""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # لفظ مربوطه
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=False)
    offer = relationship("Offer")
    
    # کاربر لفظ‌دهنده (صاحب لفظ)
    offer_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    offer_user = relationship("User", foreign_keys=[offer_user_id])
    
    # کاربر پاسخ‌دهنده (کسی که با لفظ موافقت کرده)
    responder_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    responder_user = relationship("User", foreign_keys=[responder_user_id])
    
    # کالا
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    commodity = relationship("Commodity")
    
    # نوع معامله (از دید responder)
    trade_type = Column(Enum(TradeType), nullable=False)
    
    # تعداد و قیمت توافق شده
    quantity = Column(Integer, nullable=False)
    price = Column(BigInteger, nullable=False)
    
    # وضعیت معامله
    status = Column(Enum(TradeStatus), nullable=False, default=TradeStatus.PENDING)
    
    # یادداشت
    note = Column(Text, nullable=True)
    
    # زمان‌ها
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
