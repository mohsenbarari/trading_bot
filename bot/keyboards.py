# trading_bot/bot/keyboards.py (کد کامل و به‌روزرسانی شده)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from core.enums import UserRole
from core.config import settings
from math import ceil

# --- توابع کیبورد دائمی ---
def get_create_token_inline_keyboard() -> InlineKeyboardMarkup | None:
    buttons = [[InlineKeyboardButton(text="➕ ارسال لینک دعوت (شیشه‌ای)", callback_data="create_invitation_inline")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_persistent_menu_keyboard(user_role: UserRole, mini_app_url: str) -> ReplyKeyboardMarkup:
    keyboard_layout = [[KeyboardButton(text="📈 معامله")]]
    row_2_buttons = []
    if user_role == UserRole.SUPER_ADMIN:
        row_2_buttons.append(KeyboardButton(text="🔐 پنل مدیریت")) 
    row_2_buttons.append(KeyboardButton(text="👤 پنل کاربر"))
    # دکمه تنظیمات فقط برای کاربران با نقش عادی در منوی اصلی
    if user_role == UserRole.STANDARD:
        row_2_buttons.append(KeyboardButton(text="⚙️ تنظیمات"))
    keyboard_layout.append(row_2_buttons)
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

def get_user_panel_keyboard(user_role: UserRole = None) -> ReplyKeyboardMarkup:
    keyboard_layout = []
    # دکمه تنظیمات فقط برای نقش‌های غیر عادی (مدیر ارشد، مدیر میانی، پلیس)
    if user_role and user_role != UserRole.STANDARD:
        keyboard_layout.append([KeyboardButton(text="⚙️ تنظیمات کاربری")])
    keyboard_layout.append([KeyboardButton(text="🔙 بازگشت")])
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

def get_admin_panel_keyboard() -> ReplyKeyboardMarkup:
    keyboard_layout = [
        [KeyboardButton(text="➕ ارسال لینک دعوت")],
        [KeyboardButton(text="📦 مدیریت کالاها")],
        [KeyboardButton(text="👥 مدیریت کاربران")],
        [KeyboardButton(text="⚙️ تنظیمات مدیریت")],
        [KeyboardButton(text="🔙 بازگشت")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)

# --- تابع جدید برای کیبورد مدیریت کاربران ---
def get_users_management_keyboard() -> ReplyKeyboardMarkup:
    keyboard_layout = [
        [KeyboardButton(text="📋 لیست کاربران")],
        [KeyboardButton(text="🔍 جستجوی کاربر")],
        [KeyboardButton(text="🔙 بازگشت به پنل مدیریت")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard_layout, resize_keyboard=True)
# ------------------------------------------

def get_users_list_inline_keyboard(users: list, page: int, total_count: int, limit: int = 10) -> InlineKeyboardMarkup:
    keyboard_rows = []
    
    # 1. ساخت دکمه‌ها برای کاربران
    user_buttons = []
    for user in users:
        # نمایش نام کاربری (account_name) طبق درخواست
        # اگر account_name نداشت، نام کامل یا موبایل را نمایش می‌دهیم
        display_name = user.account_name or user.full_name or user.mobile_number or f"User {user.id}"
        
        user_buttons.append(InlineKeyboardButton(text=display_name, callback_data=f"user_profile_{user.id}"))
    
    # 2. تقسیم دکمه‌ها به ردیف‌های 3 تایی (3 ستونه)
    # این کار باعث می‌شود دکمه‌ها کوچکتر و فشرده‌تر دیده شوند
    for i in range(0, len(user_buttons), 3):
        keyboard_rows.append(user_buttons[i:i+3])
    
    # 3. دکمه‌های صفحه‌بندی (Pagination)
    pagination_buttons = []
    total_pages = ceil(total_count / limit)
    
    if total_pages > 1:
        # دکمه صفحه قبل
        if page > 1:
            pagination_buttons.append(InlineKeyboardButton(text="➡️ قبلی", callback_data=f"users_page_{page - 1}"))
        else:
            pagination_buttons.append(InlineKeyboardButton(text="⏺", callback_data="noop"))

        # نشانگر صفحه فعلی
        pagination_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
        
        # دکمه صفحه بعد
        if page < total_pages:
            pagination_buttons.append(InlineKeyboardButton(text="بعدی ⬅️", callback_data=f"users_page_{page + 1}"))
        else:
            pagination_buttons.append(InlineKeyboardButton(text="⏺", callback_data="noop"))

        # افزودن ردیف صفحه‌بندی به کیبورد
        keyboard_rows.append(pagination_buttons)

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

def get_user_profile_return_keyboard(user_id: int, back_to_page: int = 1) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="⚙️ تنظیمات کاربر", callback_data=f"user_settings_{user_id}")
        ],
        [
            InlineKeyboardButton(text="❌ حذف کاربر", callback_data=f"user_ask_delete_{user_id}")
        ],
        [
            InlineKeyboardButton(text="🔙 بازگشت به لیست", callback_data=f"users_page_{back_to_page}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_user_settings_keyboard(user_id: int, is_restricted: bool = False, has_limitations: bool = False) -> InlineKeyboardMarkup:
    # تعیین متن و اکشن دکمه مسدودسازی
    block_text = "🔓 رفع مسدودیت" if is_restricted else "⛔ مسدود کردن"
    block_callback = f"user_unblock_{user_id}" if is_restricted else f"user_block_{user_id}"
    
    # تعیین متن و اکشن دکمه محدودیت
    limit_text = "✅ رفع محدودیت" if has_limitations else "⚠️ اعمال محدودیت"
    limit_callback = f"user_unlimit_{user_id}" if has_limitations else f"user_limit_{user_id}"

    keyboard = [
        [
            InlineKeyboardButton(text="🤖 تغییر دسترسی بات", callback_data=f"user_toggle_bot_{user_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ ویرایش نقش", callback_data=f"user_edit_role_{user_id}")
        ],
        [
            InlineKeyboardButton(text=block_text, callback_data=block_callback)
        ],
        [
            InlineKeyboardButton(text=limit_text, callback_data=limit_callback)
        ],
    ]
    
    keyboard.append([
        InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"user_profile_{user_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_block_duration_keyboard(user_id: int) -> InlineKeyboardMarkup:
    # گزینه‌های زمانی: (متن، دقیقه)
    # 0 دقیقه به معنی نامحدود (مثلاً 100 سال)
    durations = [
        ("1 ساعت", 60),
        ("6 ساعت", 360),
        ("12 ساعت", 720),
        ("1 روز", 1440),
        ("3 روز", 4320),
        ("1 هفته", 10080),
        ("نامحدود", 0)
    ]
    
    keyboard = []
    row = []
    for text, minutes in durations:
        row.append(InlineKeyboardButton(text=text, callback_data=f"user_block_apply_{user_id}_{minutes}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"user_settings_{user_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_role_selection_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for role in UserRole:
        if role != UserRole.SUPER_ADMIN:
            buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_role_{role.name}")])
    # دکمه انصراف برای فلو دعوت‌نامه
    buttons.append([InlineKeyboardButton(text="❌ انصراف", callback_data="comm_fsm_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_user_role_edit_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for role in UserRole:
        # دکمه برای هر نقش
        buttons.append([InlineKeyboardButton(text=role.value, callback_data=f"set_user_role_{user_id}_{role.name}")])
    
    buttons.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"user_profile_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_user_delete_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✅ بله، حذف شود", callback_data=f"user_delete_confirm_{user_id}")],
        [InlineKeyboardButton(text="❌ خیر، انصراف", callback_data=f"user_profile_{user_id}")]
    ]
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
    
    # ویرایش نام اصلی کالا
    buttons.append([
        InlineKeyboardButton(text="✏️ ویرایش نام اصلی کالا", callback_data=f"comm_edit_name_{commodity_id}")
    ])
    
    # دکمه حذف کل کالا
    buttons.append([
        InlineKeyboardButton(text="❌ حذف کامل این کالا", callback_data=f"comm_delete_{commodity_id}")
    ])
    
    # دکمه بازگشت به لیست اصلی کالاها
    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به لیست کالاها", callback_data="comm_back_to_list")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_alias_delete_confirm_keyboard(commodity_id: int, alias_id: int) -> InlineKeyboardMarkup:
    """دکمه‌های تأیید یا لغو حذف یک نام مستعار."""
    buttons = [
        [InlineKeyboardButton(text=f" بله، این نام مستعار حذف شود", callback_data=f"alias_delete_confirm_yes_{commodity_id}_{alias_id}")],
        [InlineKeyboardButton(text=" خیر، لغو", callback_data="comm_fsm_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_limit_duration_keyboard(user_id: int) -> InlineKeyboardMarkup:
    # Reuse durations but with different callback
    durations = [
        ("1 ساعت", 60),
        ("6 ساعت", 360),
        ("12 ساعت", 720),
        ("1 روز", 1440),
        ("3 روز", 4320),
        ("1 هفته", 10080),
        ("نامحدود", 0)
    ]
    
    keyboard = []
    row = []
    for text, minutes in durations:
        row.append(InlineKeyboardButton(text=text, callback_data=f"user_limit_dur_{user_id}_{minutes}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"user_settings_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_limit_settings_keyboard(user_id: int, max_trades=None, max_commodities=None, max_requests=None) -> InlineKeyboardMarkup:
    """کیبورد تنظیم محدودیت‌ها با دکمه‌های جداگانه برای هر نوع"""
    trades_text = f"📊 مجموع معاملات: {max_trades}" if max_trades else "📊 مجموع معاملات: ---"
    commodities_text = f"📦 مجموع کالا: {max_commodities}" if max_commodities else "📦 مجموع کالا: ---"
    requests_text = f"📨 مجموع لفظ: {max_requests}" if max_requests else "📨 مجموع لفظ: ---"
    
    keyboard = [
        [InlineKeyboardButton(text=trades_text, callback_data=f"limit_set_trades_{user_id}")],
        [InlineKeyboardButton(text=commodities_text, callback_data=f"limit_set_commodities_{user_id}")],
        [InlineKeyboardButton(text=requests_text, callback_data=f"limit_set_requests_{user_id}")],
        [
            InlineKeyboardButton(text="✅ تایید", callback_data=f"limit_confirm_{user_id}"),
            InlineKeyboardButton(text="❌ انصراف", callback_data=f"limit_cancel_{user_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_skip_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ رد کردن (بدون محدودیت)", callback_data=callback_data)]
    ])