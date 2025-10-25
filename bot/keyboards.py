# bot/keyboards.py (نسخه نهایی با نام جدید دکمه)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole
from core.config import settings

# --- تابع get_main_menu_keyboard دیگر برای تنظیمات استفاده نمی‌شود ---
# --- فقط برای دکمه شیشه‌ای ساخت توکن باقی می‌ماند (اگر بخواهید) ---
def get_create_token_inline_keyboard() -> InlineKeyboardMarkup | None:
    """فقط دکمه شیشه‌ای ساخت توکن را برمی‌گرداند."""
    buttons = [[InlineKeyboardButton(text="➕ ارسال لینک دعوت (شیشه‌ای)", callback_data="create_invitation_inline")]] # <--- متن اینجا هم تغییر کرد
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- تابع اصلی منوی دائمی ---
def get_persistent_menu_keyboard(user_role: UserRole, mini_app_url: str) -> ReplyKeyboardMarkup:
    """
    کیبورد دائمی اصلی را با چیدمان جدید و سفارشی ایجاد می‌کند.
    """
    keyboard_layout = [
        [KeyboardButton(text="📈 معامله")]
    ]
    
    row_2_buttons = []
    
    if user_role == UserRole.SUPER_ADMIN:
        row_2_buttons.append(KeyboardButton(text="🔐 پنل مدیریت")) 
    
    row_2_buttons.append(KeyboardButton(text="👤 پنل کاربر")) 
    row_2_buttons.append(KeyboardButton(text="📱 نسخه تحت وب", web_app=WebAppInfo(url=mini_app_url)))
    
    keyboard_layout.append(row_2_buttons)

    # دکمه ساخت توکن دعوت (متنی) برای مدیر ارشد
    if user_role == UserRole.SUPER_ADMIN:
        # === تغییر متن دکمه در اینجا ===
        keyboard_layout.append([KeyboardButton(text="➕ ارسال لینک دعوت")]) # <--- متن تغییر کرد
        # === پایان تغییر ===

    return ReplyKeyboardMarkup(
        keyboard=keyboard_layout,
        resize_keyboard=True
    )

# --- کیبورد جدید برای زیرمنوی پنل کاربر ---
def get_user_panel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد مخصوص زمانی که کاربر در بخش پنل کاربری است."""
    keyboard_layout = [
        [KeyboardButton(text="⚙️ تنظیمات کاربری")], 
        [KeyboardButton(text="🔙 بازگشت")] 
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

# --- کیبورد جدید برای زیرمنوی پنل مدیریت ---
def get_admin_panel_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد مخصوص زمانی که مدیر ارشد در بخش پنل مدیریت است."""
    keyboard_layout = [
        [KeyboardButton(text="⚙️ تنظیمات مدیریت")], 
        # دکمه‌های مدیریتی دیگر می‌توانند اینجا اضافه شوند
        [KeyboardButton(text="🔙 بازگشت")] 
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

# --- تابع قدیمی get_role_selection_keyboard بدون تغییر ---
def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    """یک کیبورد برای انتخاب سطح دسترسی کاربر جدید می‌سازد."""
    buttons = []
    for role in UserRole:
        if role != UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_role_{role.name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- تابع قدیمی get_mini_app_keyboard بدون تغییر ---
def get_mini_app_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": mini_app_url})]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- تابع قدیمی get_share_contact_keyboard بدون تغییر ---
def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )