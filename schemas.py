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

class AppConfig(BaseModel):
    bot_username: str

class CommodityAliasBase(BaseModel):
    alias: str

class CommodityAliasCreate(CommodityAliasBase):
    pass

class CommodityAliasRead(CommodityAliasBase):
    id: int
    commodity_id: int
    
    class Config:
        from_attributes = True

# --- Schemas for Commodity ---
class CommodityAliasUpdate(BaseModel):
    alias: str
    
class CommodityBase(BaseModel):
    name: str

class CommodityCreate(CommodityBase):
    aliases: list[str] = [] # لیستی از نام‌های مستعار هنگام ایجاد

class CommodityUpdate(CommodityBase):
    name: str | None = None # نام را می‌توان آپدیت کرد
    aliases: list[str] | None = None # لیست نام‌های مستعار را می‌توان آپدیت کرد

class CommodityRead(CommodityBase):
    id: int
    aliases: list[CommodityAliasRead] = [] # نمایش نام‌های مستعار مرتبط
    
    class Config:
        from_attributes = True