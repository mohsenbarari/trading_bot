# trading_bot/schemas.py (کامل و اصلاح شده)

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from core.utils import normalize_persian_numerals
from core.enums import UserRole, NotificationLevel, NotificationCategory

# --- Token Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    telegram_id: int | None = None
    session_key: str | None = None
    role: UserRole | None = None
    source: str | None = None

# --- WebApp Schemas ---
class WebAppInitData(BaseModel):
    init_data: str

# --- ۲. اعتبارسنج (validator) اضافه شد ---
class OTPRequest(BaseModel):
    mobile_number: str = Field(..., pattern=r"^09[0-9]{9}$")

    @field_validator('mobile_number', mode='before')
    @classmethod
    def normalize_mobile(cls, v):
        return normalize_persian_numerals(v)

class OTPVerify(BaseModel):
    mobile_number: str = Field(..., pattern=r"^09[0-9]{9}$")
    otp_code: str

    @field_validator('mobile_number', mode='before')
    @classmethod
    def normalize_mobile_otp(cls, v):
        return normalize_persian_numerals(v)
# ---------------------------------------------
    
# --- AppConfig Schema ---
class AppConfig(BaseModel):
    bot_username: str

# --- User Schemas ---
class UserBase(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str
    account_name: str
    mobile_number: str

class UserCreate(UserBase):
    role: UserRole

class UserRead(UserBase):
    id: int
    role: UserRole
    has_bot_access: bool
    
    class Config:
        from_attributes = True

# --- ۳. اعتبارسنج (validator) اضافه شد ---
class InvitationBase(BaseModel):
    account_name: str
    mobile_number: str = Field(..., pattern=r"^09[0-9]{9}$")
    role: UserRole = UserRole.WATCH

    @field_validator('mobile_number', mode='before')
    @classmethod
    def normalize_mobile_invite(cls, v):
        return normalize_persian_numerals(v)

class InvitationCreate(InvitationBase):
    pass

class InvitationRead(InvitationBase):
    id: int
    token: str
    expires_at: datetime
    created_by_id: int
    
    class Config:
        from_attributes = True

class NotificationRead(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: datetime
    level: NotificationLevel     
    category: NotificationCategory

    class Config:
        from_attributes = True
        

# --- Commodity Schemas ---
class CommodityAliasBase(BaseModel):
    alias: str

class CommodityAliasCreate(CommodityAliasBase):
    pass

class CommodityAlias(CommodityAliasBase):
    id: int
    commodity_id: int

    class Config:
        from_attributes = True

class CommodityBase(BaseModel):
    name: str

class CommodityCreate(CommodityBase):
    pass

class Commodity(CommodityBase):
    id: int
    aliases: List[CommodityAlias] = []

    class Config:
        from_attributes = True

