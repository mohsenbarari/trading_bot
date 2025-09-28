from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.enums import UserRole

def get_main_menu_keyboard(role: UserRole) -> InlineKeyboardMarkup:
    buttons = []
    buttons.append([InlineKeyboardButton(text="👤 پروفایل من", callback_data="my_profile")])
    buttons.append([InlineKeyboardButton(text="📈 مشاهده معاملات", callback_data="view_trades")])
    if role in [UserRole.STANDARD, UserRole.POLICE, UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN]:
        buttons.append([InlineKeyboardButton(text="💰 شروع معامله", callback_data="start_trade")])
    if role in [UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN]:
        buttons.append([InlineKeyboardButton(text="👥 مدیریت کاربران", callback_data="manage_users")])
    if role == UserRole.SUPER_ADMIN:
        buttons.append([InlineKeyboardButton(text="➕ ساخت توکن دعوت", callback_data="create_invitation")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard