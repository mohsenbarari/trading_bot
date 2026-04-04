import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Enum, BigInteger, Text, DateTime
from sqlalchemy.sql import func
from .database import Base

class UserRole(str, enum.Enum):
    WATCH = "تماشا"
    STANDARD = "عادی"
    POLICE = "پلیس"
    MIDDLE_MANAGER = "مدیر میانی"
    SUPER_ADMIN = "مدیر ارشد"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, unique=True, index=True, nullable=False)
    
    # telegram_id nullable to support web-only users
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    
    username = Column(String, nullable=True)
    full_name = Column(String, nullable=False)
    address = Column(Text, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.WATCH)
    has_bot_access = Column(Boolean, default=True, nullable=False)
    
    is_deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime, nullable=True)

    # Admin Password Management (SUPER_ADMIN / MIDDLE_MANAGER)
    admin_password_hash = Column(String(255), nullable=True)
    must_change_password = Column(Boolean, default=False, nullable=False)

    # Trading restrictions
    trading_restricted_until = Column(DateTime, nullable=True)
    max_daily_trades = Column(Integer, nullable=True)
    max_active_commodities = Column(Integer, nullable=True)
    max_daily_requests = Column(Integer, nullable=True)
    limitations_expire_at = Column(DateTime, nullable=True)

    # Denormalized counters
    trades_count = Column(Integer, default=0, nullable=False)
    commodities_traded_count = Column(Integer, default=0, nullable=False)
    channel_messages_count = Column(Integer, default=0, nullable=False)

    # Session management
    max_sessions = Column(Integer, default=1, nullable=False)

    can_block_users = Column(Boolean, default=True)
    max_blocked_users = Column(Integer, default=10)

    last_seen_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def soft_delete(self):
        """Soft delete: mark user as deleted without removing from DB."""
        self.is_deleted = True
        self.deleted_at = datetime.utcnow()
