from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from .database import Base

class ChatFile(Base):
    """مدل فایل‌های چت (آپلود شده در S3)"""
    __tablename__ = "chat_files"
    
    # استفاده از UUID به عنوان کلید اصلی برای جلوگیری از حدس زدن شناسه فایل
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    uploader_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # مسیر فایل در S3
    s3_key = Column(String(512), nullable=False)
    file_name = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    
    # سایز فایل به بایت
    size = Column(Integer, nullable=False, default=0)
    
    # پیش‌نمایش تار و فشرده (مثلا Base64 از یک عکس 20x20)
    thumbnail = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    
    # روابط
    uploader = relationship("User", foreign_keys=[uploader_id])
