# models/user.py
from sqlalchemy import Column, Integer, String, BigInteger, Enum, Boolean, DateTime
from .database import Base
from core.enums import UserRole
from sqlalchemy.sql import func

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, unique=True, index=True, nullable=False)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.WATCH)
    has_bot_access = Column(Boolean, default=True, nullable=False)
    trading_restricted_until = Column(DateTime, nullable=True, default=None)
    
    # Limitations - حداکثر مجاز
    max_daily_trades = Column(Integer, nullable=True, default=None)
    max_active_commodities = Column(Integer, nullable=True, default=None)
    max_daily_requests = Column(Integer, nullable=True, default=None)
    limitations_expire_at = Column(DateTime, nullable=True, default=None)
    
    # Counters - شمارنده‌های مصرف
    trades_count = Column(Integer, nullable=False, default=0)
    commodities_traded_count = Column(Integer, nullable=False, default=0)
    channel_messages_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)