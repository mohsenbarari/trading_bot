# core/enums.py
"""
Enum تعریف‌های مشترک اپلیکیشن
"""
import enum

__all__ = [
    "UserRole",
    "UserAccountStatus",
    "NotificationLevel",
    "NotificationCategory",
    "MessageType",
    "ChatType",
    "ChatMemberRole",
    "ChatMembershipStatus",
]

class UserRole(str, enum.Enum):
    WATCH = "تماشا"
    STANDARD = "عادی"
    POLICE = "پلیس"
    MIDDLE_MANAGER = "مدیر میانی"
    SUPER_ADMIN = "مدیر ارشد"


class UserAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

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


class MessageType(str, enum.Enum):
    """نوع محتوای پیام چت"""
    TEXT = "text"
    IMAGE = "image"
    STICKER = "sticker"
    VIDEO = "video"
    LOCATION = "location"
    VOICE = "voice"
    DOCUMENT = "document"


class ChatType(str, enum.Enum):
    """نوع فضای پیام‌رسانی"""
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"


class ChatMemberRole(str, enum.Enum):
    """نقش عضو داخل chat"""
    ADMIN = "admin"
    MEMBER = "member"


class ChatMembershipStatus(str, enum.Enum):
    """وضعیت عضویت کاربر در chat"""
    ACTIVE = "active"
    LEFT = "left"
    REMOVED = "removed"
    INACTIVE = "inactive"