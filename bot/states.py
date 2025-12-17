from aiogram.fsm.state import StatesGroup, State

class Registration(StatesGroup):
    awaiting_contact = State()
    awaiting_address = State()  # منتظر دریافت آدرس

class InvitationCreation(StatesGroup):
    awaiting_account_name = State()
    awaiting_mobile_number = State()
    awaiting_role = State() 

class CommodityManagement(StatesGroup):
    # FSM افزودن کالا (بدون تغییر)
    awaiting_add_name = State()
    awaiting_add_aliases = State()
    
    # FSM ویرایش کالا (بدون تغییر)
    awaiting_edit_name = State()
    awaiting_edit_aliases = State()
    
    # FSM حذف کالا (بدون تغییر)
    awaiting_delete_confirmation = State()
    
    
    # === State Group جدید برای مدیریت Alias ===
    awaiting_alias_add_name = State()   # برای افزودن یک alias
    awaiting_alias_edit_name = State()  # برای ویرایش یک alias
    awaiting_alias_delete_confirm = State() # برای تایید حذف یک alias
    # === State جدید برای ویرایش نام اصلی کالا ===
    awaiting_commodity_edit_name = State()

class UserManagement(StatesGroup):
    awaiting_search_query = State()

class UserLimitations(StatesGroup):
    awaiting_limit_value = State()  # منتظر وارد کردن عدد محدودیت

class Trade(StatesGroup):
    """FSM برای ثبت لفظ معاملاتی"""
    awaiting_quantity = State()  # منتظر تعداد کالا
    awaiting_lot_type = State()  # منتظر انتخاب یکجا/خُرد
    awaiting_lot_sizes = State() # منتظر ترکیب بخش‌ها
    awaiting_price = State()     # منتظر قیمت (5 یا 6 رقمی)
    awaiting_notes = State()     # منتظر توضیحات (اختیاری)
    awaiting_text_confirm = State()  # منتظر تایید لفظ متنی


class TradingSettingsEdit(StatesGroup):
    """FSM برای ویرایش تنظیمات سیستم"""
    awaiting_value = State()  # منتظر ورود مقدار جدید

