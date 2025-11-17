# models/invitation.py
from sqlalchemy import Boolean, Column, Integer, String, Enum, DateTime, ForeignKey
from .database import Base
from core.enums import UserRole

class Invitation(Base):
    __tablename__ = "invitations"
    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, unique=True, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    is_used = Column(Boolean, default=False)
    role = Column(Enum(UserRole), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)