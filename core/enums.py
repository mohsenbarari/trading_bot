# core/enums.py
"""
Enum تعریف‌های مشترک اپلیکیشن
"""
import enum

__all__ = ["UserRole", "NotificationLevel", "NotificationCategory"]

class UserRole(str, enum.Enum):
    WATCH = "تماشا"
    STANDARD = "عادی"
    POLICE = "پلیس"
    MIDDLE_MANAGER = "مدیر میانی"
    SUPER_ADMIN = "مدیر ارشد"

class NotificationLevel(str, enum.Enum):
    # 👇 مقادیر باید با دیتابیس (مایگریشن) یکسان باشند (حروف بزرگ)
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"

class NotificationCategory(str, enum.Enum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    TRADE = "TRADE"