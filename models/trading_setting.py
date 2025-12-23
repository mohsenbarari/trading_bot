# models/trading_setting.py
"""مدل تنظیمات معاملاتی در دیتابیس"""

from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime
from models.database import Base


class TradingSetting(Base):
    """
    جدول تنظیمات معاملاتی (Key-Value Store)
    
    هر تنظیم به صورت یک سطر با کلید و مقدار ذخیره می‌شود.
    مقدار به صورت JSON encode شده است.
    """
    __tablename__ = "trading_settings"
    
    # کلید تنظیم (مثلاً "offer_expiry_minutes")
    key = Column(String(100), primary_key=True, index=True)
    
    # مقدار تنظیم (JSON encoded)
    value = Column(Text, nullable=False)
    
    # زمان آخرین بروزرسانی
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<TradingSetting {self.key}={self.value}>"
