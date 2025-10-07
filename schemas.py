# schemas.py
from pydantic import BaseModel
from core.enums import UserRole
from models.session import Platform

class InvitationBase(BaseModel):
    account_name: str  
    mobile_number: str
    role: UserRole

class InvitationCreate(InvitationBase):
    pass

class Invitation(InvitationBase):
    id: int
    token: str
    is_used: bool
    class Config:
        from_attributes = True

class UserBase(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str
    account_name: str 
    mobile_number: str

class User(UserBase):
    id: int
    role: UserRole
    class Config:
        from_attributes = True
        use_enum_values = True

class OTPRequest(BaseModel):
    mobile_number: str

class OTPVerify(BaseModel):
    mobile_number: str
    otp: str
    platform: Platform
    device_fingerprint: str

class Token(BaseModel):
    access_token: str
    token_type: str

class WebAppInitData(BaseModel):
    init_data: str