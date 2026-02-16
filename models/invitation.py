from sqlalchemy import Column, Integer, String, Boolean, Enum, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
from .user import UserRole

class Invitation(Base):
    __tablename__ = "invitations"

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, unique=True, index=True, nullable=False)
    
    token = Column(String, unique=True, index=True, nullable=False)
    
    # New short code for SMS-friendly links
    short_code = Column(String(8), unique=True, index=True, nullable=True)
    
    role = Column(Enum(UserRole), nullable=False, default=UserRole.WATCH)
    
    created_by_id = Column(Integer, ForeignKey("users.id"))
    created_by = relationship("User", foreign_keys=[created_by_id])
    
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
