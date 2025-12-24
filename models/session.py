# models/session.py
"""مدل سشن کاربر"""
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import enum


class Platform(str, enum.Enum):
    TELEGRAM_MINI_APP = "telegram_mini_app"
    WEB = "web"
    ANDROID = "android"


class UserSession(Base):
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(SAEnum(Platform), nullable=False)
    device_fingerprint = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # رابطه با کاربر
    user = relationship("User", backref="sessions")