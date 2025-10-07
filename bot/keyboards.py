# bot/keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from core.enums import UserRole

MINI_APP_URL = "https://telegram.362514.ir/" 

def get_main_menu_keyboard(role: UserRole) -> InlineKeyboardMarkup:
    """منوی اصلی داخل بات را بر اساس سطح دسترسی کاربر می‌سازد."""
    buttons = []
    
    # --- کیبورد اختصاصی و ساده‌شده برای کاربران سطح "تماشا" ---
    if role == UserRole.WATCH:
        buttons.append([InlineKeyboardButton(text="👤 پروفایل من", callback_data="my_profile")])
    
    # --- کیبورد برای سایر سطوح دسترسی ---
    else:
        # (منطق قبلی برای سایر کاربران بدون تغییر باقی می‌ماند)
        buttons.append([InlineKeyboardButton(text="👤 پروفایل من", callback_data="my_profile")])
        buttons.append([InlineKeyboardButton(text="📈 معاملات من", callback_data="view_my_trades")])
        if role in [UserRole.STANDARD, UserRole.POLICE, UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN]:
            buttons.append([InlineKeyboardButton(text="💰 ثبت پیشنهاد جدید", callback_data="create_trade_offer")])
        if role in [UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN]:
            buttons.append([InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="manage_users")])
        if role == UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text="➕ ساخت توکن دعوت", callback_data="create_invitation")])
        
        # دکمه ورود به Mini App فقط برای کاربران غیر-تماشا نمایش داده می‌شود
        buttons.append([InlineKeyboardButton(text="🔐 باز کردن پنل امن", web_app={"url": MINI_APP_URL})])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ... (بقیه توابع بدون تغییر باقی می‌مانند) ...
def get_mini_app_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": MINI_APP_URL})],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )