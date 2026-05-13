# trading_bot/schemas.py (کامل و اصلاح شده)

from pydantic import BaseModel, Field, field_validator, field_serializer, computed_field
from typing import List, Optional
from datetime import datetime
from core.utils import normalize_persian_numerals, to_jalali_str
from core.enums import UserRole, NotificationLevel, NotificationCategory
from models.accountant_relation import AccountantRelationStatus

# --- Token Schemas ---
class Token(BaseModel):
    access_token: str
    token_type: str


class TokenPair(BaseModel):
    """جفت توکن - Access Token + Refresh Token"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800  # 30 دقیقه به ثانیه


class RefreshRequest(BaseModel):
    """درخواست تمدید توکن"""
    refresh_token: str


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
    telegram_id: int | None = None
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
    is_accountant: bool = False
    is_deleted: bool = False
    avatar_file_id: str | None = None
    created_at: datetime
    trading_restricted_until: datetime | None = None
    
    # Limitations - حداکثر مجاز
    max_daily_trades: int | None = None
    max_active_commodities: int | None = None
    max_daily_requests: int | None = None
    limitations_expire_at: datetime | None = None
    
    # Counters - شمارنده‌های مصرف
    trades_count: int = 0
    commodities_traded_count: int = 0
    channel_messages_count: int = 0

    last_seen_at: datetime | None = None
    
    # Block Settings
    can_block_users: bool = True
    max_blocked_users: int = 10
    max_sessions: int = 1
    max_accountants: int = 3
    
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

class UserPublicRead(BaseModel):
    """شمای عمومی کاربر برای نمایش به دیگران"""
    id: int
    account_name: str
    role: UserRole
    mobile_number: str
    address: str
    avatar_file_id: str | None = None
    created_at: datetime
    trades_count: int = 0
    last_seen_at: datetime | None = None
    resolved_from_accountant_id: int | None = None
    highlight_accountant_user_id: int | None = None
    highlight_accountant_relation_display_name: str | None = None
    
    @computed_field
    def created_at_jalali(self) -> str | None:
        return to_jalali_str(self.created_at)

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
    
    # Block Settings
    can_block_users: Optional[bool] = None
    max_blocked_users: Optional[int] = None
    max_sessions: Optional[int] = None
    max_accountants: Optional[int] = None


class UserAvatarUpdate(BaseModel):
    avatar_file_id: str | None = None

    @field_validator('avatar_file_id')
    @classmethod
    def normalize_avatar_file_id(cls, value: str | None):
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class AccountantRelationCreate(BaseModel):
    account_name: str
    relation_display_name: str = Field(..., min_length=1, max_length=120)
    mobile_number: str = Field(..., pattern=r"^09[0-9]{9}$")
    duty_description: str | None = Field(default=None, max_length=255)

    @field_validator('mobile_number', mode='before')
    @classmethod
    def normalize_mobile_accountant(cls, value):
        return normalize_persian_numerals(value)

    @field_validator('account_name', 'relation_display_name', mode='before')
    @classmethod
    def strip_accountant_strings(cls, value):
        if value is None:
            return value
        return str(value).strip()

    @field_validator('duty_description', mode='before')
    @classmethod
    def strip_duty_description(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class AccountantRelationUpdate(BaseModel):
    relation_display_name: str | None = Field(default=None, min_length=1, max_length=120)
    duty_description: str | None = Field(default=None, max_length=255)

    @field_validator('relation_display_name', mode='before')
    @classmethod
    def strip_relation_display_name(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator('duty_description', mode='before')
    @classmethod
    def strip_updated_duty_description(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class AccountantRelationRead(BaseModel):
    id: int
    owner_user_id: int
    accountant_user_id: int | None = None
    accountant_account_name: str | None = None
    global_account_name: str
    relation_display_name: str
    duty_description: str | None = None
    mobile_number: str
    status: AccountantRelationStatus
    invitation_token: str
    registration_link: str | None = None
    expires_at: datetime
    activated_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime

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

