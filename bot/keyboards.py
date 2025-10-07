# bot/keyboards.py (نسخه نهایی)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole

MINI_APP_URL = "https://telegram.362514.ir/" 

def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    """یک کیبورد برای انتخاب سطح دسترسی کاربر جدید می‌سازد."""
    buttons = []
    for role in UserRole:
        if role != UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_role_{role.name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard(role: UserRole) -> InlineKeyboardMarkup:
    """منوی اصلی را بر اساس سطح دسترسی می‌سازد."""
    buttons = []
    if role == UserRole.WATCH:
        buttons.append([InlineKeyboardButton(text="👤 پروفایل من", callback_data="my_profile")])
    elif role == UserRole.SUPER_ADMIN:
        buttons.append([InlineKeyboardButton(text="➕ ساخت توکن دعوت", callback_data="create_invitation")])
    else:
        buttons.append([InlineKeyboardButton(text="👤 پروفایل من", callback_data="my_profile")])
        buttons.append([InlineKeyboardButton(text="📈 معاملات من", callback_data="view_my_trades")])
        buttons.append([InlineKeyboardButton(text="💰 ثبت پیشنهاد جدید", callback_data="create_trade_offer")])
        if role == UserRole.MIDDLE_MANAGER:
            buttons.append([InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="manage_users")])
        buttons.append([InlineKeyboardButton(text="🔐 باز کردن پنل امن", web_app={"url": MINI_APP_URL})])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_mini_app_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": MINI_APP_URL})]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_persistent_menu_keyboard() -> ReplyKeyboardMarkup:
    """یک کیبورد دائمی با دکمه های 'پنل کاربری' و 'پنل تحت وب' ایجاد می‌کند."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 پنل تحت وب",
                    web_app=WebAppInfo(url=MINI_APP_URL)
                ),
                KeyboardButton(text="پنل کاربری")
            ]
        ],
        resize_keyboard=True
    )