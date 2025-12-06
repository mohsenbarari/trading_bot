# trading_bot/schemas.py (کامل و اصلاح شده)

from pydantic import BaseModel, Field, field_validator, field_serializer
from typing import List, Optional
from datetime import datetime
from core.utils import normalize_persian_numerals, to_jalali_str
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

# --- OTP Schemas ---
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
    created_at: datetime
    trading_restricted_until: datetime | None = None
    
    # Limitations
    max_daily_trades: int | None = None
    max_active_commodities: int | None = None
    max_daily_requests: int | None = None
    limitations_expire_at: datetime | None = None
    
    @computed_field
    def created_at_jalali(self) -> str | None:
        return to_jalali_str(self.created_at)

    @computed_field
    def trading_restricted_until_jalali(self) -> str | None:
        return to_jalali_str(self.trading_restricted_until)

    @computed_field
    def limitations_expire_at_jalali(self) -> str | None:
        return to_jalali_str(self.limitations_expire_at)

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    role: Optional[UserRole] = None
    has_bot_access: Optional[bool] = None
    trading_restricted_until: Optional[datetime] = None
    
    # Limitations
    max_daily_trades: Optional[int] = None
    max_active_commodities: Optional[int] = None
    max_daily_requests: Optional[int] = None
    limitations_expire_at: Optional[datetime] = None


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
    
    @computed_field
    def expires_at_jalali(self) -> str | None:
        return to_jalali_str(self.expires_at)

    class Config:
        from_attributes = True

class NotificationRead(BaseModel):
    id: int
    message: str
    is_read: bool
    created_at: datetime
    level: NotificationLevel     
    category: NotificationCategory

    @computed_field
    def created_at_jalali(self) -> str | None:
        return to_jalali_str(self.created_at)

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

