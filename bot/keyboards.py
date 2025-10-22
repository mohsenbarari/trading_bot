# bot/keyboards.py (نسخه نهایی با چیدمان جدید)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole
from core.config import settings

def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    """یک کیبورد برای انتخاب سطح دسترسی کاربر جدید می‌سازد."""
    buttons = []
    for role in UserRole:
        if role != UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_role_{role.name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard(role: UserRole) -> InlineKeyboardMarkup | None:
    """
    منوی اینلاین برای اقدامات خاص (مانند ساخت دعوتنامه برای ادمین).
    این منو دیگر منوی اصلی نیست و فقط برای کارهای خاص استفاده می‌شود.
    """
    buttons = []
    if role == UserRole.SUPER_ADMIN:
        buttons.append([InlineKeyboardButton(text="➕ ساخت توکن دعوت جدید", callback_data="create_invitation")])
    
    if not buttons:
        return None

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_mini_app_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": mini_app_url})]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_persistent_menu_keyboard(user_role: UserRole, mini_app_url: str) -> ReplyKeyboardMarkup:
    """
    کیبورد دائمی اصلی را با چیدمان جدید و سفارشی ایجاد می‌کند.
    """
    # ردیف اول: دکمه بزرگ معامله
    keyboard_layout = [
        [KeyboardButton(text="📈 معامله")]
    ]
    
    # ردیف دوم: سه دکمه دیگر در کنار هم
    keyboard_layout.append([
        KeyboardButton(text="⚙️ تنظیمات"),
        KeyboardButton(text="👤 پنل کاربر"),
        KeyboardButton(text="📱 نسخه تحت وب", web_app=WebAppInfo(url=mini_app_url)),
    ])

    # ردیف سوم: دکمه ساخت دعوت‌نامه فقط برای مدیر ارشد
    if user_role == UserRole.SUPER_ADMIN:
        keyboard_layout.append([KeyboardButton(text="➕ ساخت توکن دعوت")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard_layout,
        resize_keyboard=True
    )

