# models/user_block.py
"""
مدل بلاک کاربران - برای مسدود کردن کاربران از معامله با یکدیگر
"""
from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class UserBlock(Base):
    """
    جدول بلاک کاربران
    
    هر رکورد نشان می‌دهد که blocker کاربر blocked را مسدود کرده است.
    این رابطه یک‌طرفه است از نظر داده، ولی از نظر منطق دوطرفه عمل می‌کند.
    """
    __tablename__ = "user_blocks"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # کاربری که بلاک کرده
    blocker_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # کاربری که بلاک شده
    blocked_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # روابط
    blocker = relationship("User", foreign_keys=[blocker_id], backref="blocks_made")
    blocked = relationship("User", foreign_keys=[blocked_id], backref="blocks_received")
    
    # Unique constraint: هر کاربر فقط یکبار می‌تواند کاربر دیگری را بلاک کند
    __table_args__ = (
        UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocker_blocked'),
    )
    
    def __repr__(self):
        return f"<UserBlock(blocker={self.blocker_id}, blocked={self.blocked_id})>"
