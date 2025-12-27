# core/security.py
"""
ماژول امنیت - مدیریت JWT Access Token و Refresh Token

Access Token: کوتاه‌مدت (30 دقیقه) - برای احراز هویت درخواست‌ها
Refresh Token: بلندمدت (30 روز) - برای دریافت Access Token جدید بدون لاگین مجدد
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
from .config import settings

__all__ = [
    "create_access_token",
    "create_refresh_token",
    "verify_token",
    "verify_refresh_token",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
]

# ===== تنظیمات JWT =====
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = settings.jwt_algorithm

# زمان انقضای توکن‌ها
ACCESS_TOKEN_EXPIRE_MINUTES = 30  # 30 دقیقه
REFRESH_TOKEN_EXPIRE_DAYS = 30    # 30 روز برای کلاینت‌های موبایل/وب


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    ساخت Access Token برای احراز هویت درخواست‌ها.
    
    Args:
        data: دیتای payload (باید شامل 'sub' باشد)
        expires_delta: مدت انقضا (اختیاری، پیش‌فرض: ACCESS_TOKEN_EXPIRE_MINUTES)
        
    Returns:
        JWT Token رمزنگاری شده
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({
        "exp": expire,
        "type": "access"  # نوع توکن برای تشخیص
    })
    
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    ساخت Refresh Token برای دریافت Access Token جدید.
    
    این توکن 30 روز اعتبار دارد و کاربران موبایل/وب نیازی به لاگین مجدد ندارند.
    
    Args:
        data: دیتای payload (باید شامل 'sub' باشد)
        expires_delta: مدت انقضا (اختیاری، پیش‌فرض: REFRESH_TOKEN_EXPIRE_DAYS)
        
    Returns:
        JWT Refresh Token رمزنگاری شده
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({
        "exp": expire,
        "type": "refresh"  # نوع توکن برای تشخیص
    })
    
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str, credentials_exception) -> dict:
    """
    اعتبارسنجی Access Token.
    
    Args:
        token: JWT Token
        credentials_exception: Exception برای خطای اعتبارسنجی
        
    Returns:
        Payload توکن
        
    Raises:
        credentials_exception: اگر توکن نامعتبر باشد
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # بررسی وجود subject
        if payload.get("sub") is None:
            raise credentials_exception
        
        # بررسی نوع توکن (باید access باشد)
        if payload.get("type") != "access":
            raise credentials_exception
            
        return payload
        
    except JWTError:
        raise credentials_exception


def verify_refresh_token(token: str, credentials_exception) -> dict:
    """
    اعتبارسنجی Refresh Token.
    
    Args:
        token: JWT Refresh Token
        credentials_exception: Exception برای خطای اعتبارسنجی
        
    Returns:
        Payload توکن
        
    Raises:
        credentials_exception: اگر توکن نامعتبر باشد
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # بررسی وجود subject
        if payload.get("sub") is None:
            raise credentials_exception
        
        # بررسی نوع توکن (باید refresh باشد)
        if payload.get("type") != "refresh":
            raise credentials_exception
            
        return payload
        
    except JWTError:
        raise credentials_exception


def create_token_pair(user_id: int, telegram_id: int) -> Tuple[str, str]:
    """
    ساخت جفت توکن (Access + Refresh) برای کاربر.
    
    Args:
        user_id: شناسه کاربر در دیتابیس
        telegram_id: شناسه تلگرام کاربر
        
    Returns:
        (access_token, refresh_token)
    """
    # Use telegram_id as sub since get_current_user_from_token extracts telegram_id from sub
    data = {"sub": str(telegram_id), "user_id": user_id, "telegram_id": telegram_id}
    
    access_token = create_access_token(data)
    refresh_token = create_refresh_token(data)
    
    return access_token, refresh_token