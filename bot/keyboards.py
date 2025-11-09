# bot/keyboards.py (نسخه نهایی با دکمه ویرایش نام کالا)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole
from core.config import settings

# --- توابع کیبورد دائمی (بدون تغییر) ---
def get_create_token_inline_keyboard() -> InlineKeyboardMarkup | None:
    buttons = [[InlineKeyboardButton(text="➕ ارسال لینک دعوت (شیشه‌ای)", callback_data="create_invitation_inline")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_persistent_menu_keyboard(user_role: UserRole, mini_app_url: str) -> ReplyKeyboardMarkup:
    keyboard_layout = [[KeyboardButton(text="📈 معامله")]]
    row_2_buttons = []
    if user_role == UserRole.SUPER_ADMIN:
        row_2_buttons.append(KeyboardButton(text="🔐 پنل مدیریت")) 
    row_2_buttons.append(KeyboardButton(text="👤 پنل کاربر")) 
    row_2_buttons.append(KeyboardButton(text="📱 نسخه تحت وب", web_app=WebAppInfo(url=mini_app_url)))
    keyboard_layout.append(row_2_buttons)
    if user_role == UserRole.SUPER_ADMIN:
        keyboard_layout.append([KeyboardButton(text="➕ ارسال لینک دعوت")])
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

def get_user_panel_keyboard() -> ReplyKeyboardMarkup:
    keyboard_layout = [[KeyboardButton(text="⚙️ تنظیمات کاربری")], [KeyboardButton(text="🔙 بازگشت")]]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

def get_admin_panel_keyboard() -> ReplyKeyboardMarkup:
    keyboard_layout = [
        [KeyboardButton(text="📦 مدیریت کالاها")],
        [KeyboardButton(text="⚙️ تنظیمات مدیریت")],
        [KeyboardButton(text="🔙 بازگشت")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for role in UserRole:
        if role != UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_role_{role.name}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_mini_app_keyboard(mini_app_url: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🔐 ورود به پنل امن تحت وب", web_app={"url": mini_app_url})]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_share_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📲 اشتراک شماره تماس برای تایید هویت", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_commodity_fsm_cancel_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="❌ لغو عملیات", callback_data="comm_fsm_cancel")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_commodity_delete_confirm_keyboard(commodity_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f" بله، حذف شود", callback_data=f"comm_delete_confirm_yes_{commodity_id}")],
        [InlineKeyboardButton(text=" خیر، لغو", callback_data="comm_fsm_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# === شروع تغییر در این تابع ===
def get_aliases_list_keyboard(commodity: dict) -> InlineKeyboardMarkup:
    """
    "جدول" نام‌های مستعار را به همراه دکمه‌های مدیریت هر alias می‌سازد.
    """
    buttons = []
    commodity_id = commodity.get('id')
    
    for alias in commodity.get('aliases', []):
        buttons.append([
            InlineKeyboardButton(text=f"{alias['alias']}", callback_data="noop"),
            InlineKeyboardButton(text="✏️ ویرایش", callback_data=f"alias_edit_{commodity_id}_{alias['id']}"),
            InlineKeyboardButton(text="❌ حذف", callback_data=f"alias_delete_{commodity_id}_{alias['id']}")
        ])
    
    # دکمه افزودن نام مستعار جدید
    buttons.append([
        InlineKeyboardButton(text="➕ افزودن نام مستعار جدید", callback_data=f"alias_add_{commodity_id}")
    ])
    
    # === دکمه جدید: ویرایش نام اصلی کالا ===
    buttons.append([
        InlineKeyboardButton(text="✏️ ویرایش نام اصلی کالا", callback_data=f"comm_edit_name_{commodity_id}")
    ])
    # === پایان افزودن ===
    
    # دکمه حذف کل کالا
    buttons.append([
        InlineKeyboardButton(text="❌ حذف کامل این کالا", callback_data=f"comm_delete_{commodity_id}")
    ])
    
    # دکمه بازگشت به لیست اصلی کالاها
    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به لیست کالاها", callback_data="comm_back_to_list")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
# === پایان تغییر ===

def get_alias_delete_confirm_keyboard(commodity_id: int, alias_id: int) -> InlineKeyboardMarkup:
    """دکمه‌های تأیید یا لغو حذف یک نام مستعار."""
    buttons = [
        [InlineKeyboardButton(text=f" بله، این نام مستعار حذف شود", callback_data=f"alias_delete_confirm_yes_{commodity_id}_{alias_id}")],
        [InlineKeyboardButton(text=" خیر، لغو", callback_data="comm_fsm_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)