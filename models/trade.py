# models/trade.py
"""مدل ذخیره معاملات"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum


class TradeType(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # کاربر لفظ‌دهنده
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user = relationship("User", foreign_keys=[user_id])
    
    # نوع معامله
    trade_type = Column(Enum(TradeType), nullable=False)
    
    # کالا
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    commodity = relationship("Commodity")
    
    # تعداد و قیمت
    quantity = Column(Integer, nullable=False)
    price = Column(BigInteger, nullable=False)
    
    # آیدی پیام در کانال (برای رفرنس)
    channel_message_id = Column(BigInteger, nullable=True)
    
    # زمان ایجاد
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
