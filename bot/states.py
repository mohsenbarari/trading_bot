from aiogram.fsm.state import StatesGroup, State

class Registration(StatesGroup):
    awaiting_contact = State()

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
