# bot/keyboards.py (نسخه نهایی و اصلاح شده)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole

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
    else:
        buttons.append([InlineKeyboardButton(text="📈 معامله", callback_data="start_trade")])
        buttons.append([InlineKeyboardButton(text="👤 پنل کاربر", callback_data="my_profile")])
        buttons.append([InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="settings")])

        if role == UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text="➕ ساخت توکن دعوت", callback_data="create_invitation")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_mini_app_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    # اصلاح: آدرس هاردکد شده حذف و به صورت ورودی دریافت شد
    buttons = [[InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": mini_app_url})]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_persistent_menu_keyboard(mini_app_url: str) -> ReplyKeyboardMarkup:
    """یک کیبورد دائمی با دکمه های 'پنل کاربری' و 'پنل تحت وب' ایجاد می‌کند."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 پنل تحت وب",
                    web_app=WebAppInfo(url=mini_app_url)
                ),
                KeyboardButton(text="پنل کاربری")
            ]
        ],
        resize_keyboard=True
    )
