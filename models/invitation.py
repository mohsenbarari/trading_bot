# models/invitation.py
from sqlalchemy import Boolean, Column, Integer, String
from .database import Base

class Invitation(Base):
    __tablename__ = "invitations"
    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True, nullable=False)
    mobile_number = Column(String, unique=True, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    is_used = Column(Boolean, default=False)