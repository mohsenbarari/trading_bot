# bot/handlers/admin_commodities.py (نسخه نهایی با اصلاح ویرایش نام کالا)
import httpx
import re
import logging
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from models.user import User
from core.enums import UserRole
from core.config import settings
from bot.states import CommodityManagement
from bot.keyboards import (
    get_commodity_fsm_cancel_keyboard, 
    get_commodity_delete_confirm_keyboard,
    get_aliases_list_keyboard,
    get_alias_delete_confirm_keyboard
)
from aiogram.filters import StateFilter
from aiogram.exceptions import TelegramBadRequest
import json

logger = logging.getLogger(__name__)
router = Router()

COMMODITIES_API_URL = "http://app:8000/api/commodities/"
ALIASES_API_URL = "http://app:8000/api/commodities/aliases/"

def get_auth_headers() -> dict:
    if not settings.dev_api_key: return {"X-Dev-Key": "NOT_SET"}
    return {"X-Dev-Key": settings.dev_api_key}

def get_error_detail(e: httpx.HTTPStatusError) -> str:
    """جزئیات خطا را به صورت ایمن از پاسخ HTTP استخراج می‌کند."""
    try:
        detail = e.response.json().get("detail", e.response.text)
    except json.JSONDecodeError:
        detail = e.response.text
    return detail

async def safe_delete_message(bot: Bot, chat_id: int, message_id: int):
    """تلاش می‌کند پیامی را حذف کند و خطاهای احتمالی را نادیده می‌گیرد."""
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, Exception) as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")

# === 1. جریان اصلی مدیریت کالاها (بدون تغییر) ===
async def show_commodity_list(target_message: types.Message, user: User, edit: bool = False):
    if user.role != UserRole.SUPER_ADMIN: return
    logger.info("Showing commodity list...")
    try:
        headers = get_auth_headers()
        async with httpx.AsyncClient() as client:
            response = await client.get(COMMODITIES_API_URL, timeout=10.0, headers=headers)
            response.raise_for_status()
            commodities = response.json()
        text = "لیست کالاهای ثبت شده:\n\n"
        buttons = []
        if not commodities:
            text = "هیچ کالایی ثبت نشده است."
        else:
            for comm in commodities:
                buttons.append([
                    InlineKeyboardButton(text=f"📦 {comm['name']}", callback_data=f"comm_manage_aliases_{comm['id']}"),
                ])
        buttons.append([InlineKeyboardButton(text="➕ افزودن کالای جدید", callback_data="comm_add_new")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        if edit:
            await target_message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await target_message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    except httpx.RequestError as e: logger.error(f"Network error showing list: {e}"); await target_message.answer(f"❌ خطای شبکه. لطفاً اتصال اینترنت خود را بررسی کنید.")
    except httpx.HTTPStatusError as e: 
        detail = get_error_detail(e)
        logger.error(f"API error showing list: {detail}"); await target_message.answer(f"❌ {detail}\n\nعملیات بارگیری لیست ناموفق بود.", parse_mode="Markdown")
    except Exception as e: logger.exception("Unexpected error showing list"); await target_message.answer(f"❌ خطای پیش‌بینی نشده: {e}")

@router.message(F.text == "📦 مدیریت کالاها")
async def handle_manage_commodities(message: types.Message, user: Optional[User]):
    if not user: return
    await show_commodity_list(message, user)

@router.callback_query(F.data == "comm_back_to_list", StateFilter("*"))
async def handle_back_to_list(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    await state.clear() 
    await show_commodity_list(query.message, user, edit=True)
    await query.answer()

# === 2. نمایش و مدیریت نام‌های مستعار (بدون تغییر) ===
async def show_aliases_list(query: types.CallbackQuery, user: User, commodity_id: int, edit: bool = True):
    if not user: return
    try:
        headers = get_auth_headers()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{COMMODITIES_API_URL}{commodity_id}", headers=headers)
            response.raise_for_status()
        commodity = response.json()
        aliases = commodity.get('aliases', [])
        text = f"مدیریت نام‌های مستعار برای: **{commodity['name']}**\n\n"
        if not aliases: text += "<i>هیچ نام مستعاری ثبت نشده است.</i>"
        else: text += "لیست نام‌های مستعار:"
        keyboard = get_aliases_list_keyboard(commodity)
        if edit:
            await query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await query.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error showing aliases list")
        await query.message.edit_text(f"❌ خطا در دریافت اطلاعات کالا: {e}")
    await query.answer()

@router.callback_query(F.data.startswith("comm_manage_aliases_"))
async def handle_manage_aliases(query: types.CallbackQuery, user: Optional[User]):
    if not user: return
    commodity_id = int(query.data.split("_")[-1])
    await show_aliases_list(query, user, commodity_id, edit=True)

# === 3. افزودن نام مستعار (Alias ADD) (بدون تغییر) ===
@router.callback_query(F.data.startswith("alias_add_"), StateFilter(None))
async def handle_alias_add_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    await state.set_state(CommodityManagement.awaiting_alias_add_name)
    await state.update_data(commodity_id=commodity_id, list_message_id=query.message.message_id)
    await query.message.edit_text("--- افزودن نام مستعار ---\n\nلطفاً **نام مستعار (alias)** جدید را وارد کنید:", reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown")
    await query.answer()

@router.message(CommodityManagement.awaiting_alias_add_name)
async def handle_alias_add_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    new_alias_name = message.text
    data = await state.get_data()
    commodity_id = data.get("commodity_id")
    list_message_id = data.get("list_message_id")
    await state.clear()
    await message.delete()
    if list_message_id: await safe_delete_message(message.bot, message.chat.id, list_message_id)
    status_msg = await message.answer(f"در حال افزودن نام مستعار **'{new_alias_name}'**...", parse_mode="Markdown")
    headers = get_auth_headers()
    payload = {"alias": new_alias_name}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{COMMODITIES_API_URL}{commodity_id}/aliases", json=payload, headers=headers)
            response.raise_for_status()
        await status_msg.edit_text(f"✅ نام مستعار **'{new_alias_name}'** با موفقیت افزوده شد.", parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        await status_msg.edit_text(f"❌ {detail}\n\nعملیات ثبت نشد. لطفاً دوباره امتحان کنید.", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطای پیش‌بینی نشده: {e}\n\nعملیات انجام نشد.")
    await show_commodity_list(message, user)

# === 4. ویرایش نام مستعار (Alias EDIT) (بدون تغییر) ===
@router.callback_query(F.data.startswith("alias_edit_"), StateFilter(None))
async def handle_alias_edit_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    parts = query.data.split("_"); commodity_id = int(parts[2]); alias_id = int(parts[3])
    alias_name = f"(ID: {alias_id})" 
    try:
        for row in query.message.reply_markup.inline_keyboard:
            if len(row) == 3 and row[1].callback_data == query.data:
                alias_name = row[0].text; break
    except Exception as e: logger.warning(f"Could not parse alias name from keyboard for edit: {e}")
    await state.set_state(CommodityManagement.awaiting_alias_edit_name)
    await state.update_data(alias_id=alias_id, alias_name=alias_name, commodity_id=commodity_id, list_message_id=query.message.message_id) 
    await query.message.edit_text(f"--- ویرایش نام مستعار ---\n\nنام فعلی: **{alias_name}**\n\nلطفاً **نام جدید** را وارد کنید:", reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown")
    await query.answer()

@router.message(CommodityManagement.awaiting_alias_edit_name)
async def handle_alias_edit_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    new_alias_name = message.text
    data = await state.get_data()
    alias_id = data.get("alias_id")
    old_alias_name = data.get("alias_name", f"(ID: {alias_id})")
    list_message_id = data.get("list_message_id")
    await state.clear()
    await message.delete()
    if list_message_id: await safe_delete_message(message.bot, message.chat.id, list_message_id)
    status_msg = await message.answer(f"در حال ویرایش **'{old_alias_name}'** به **'{new_alias_name}'**...", parse_mode="Markdown")
    headers = get_auth_headers()
    payload = {"alias": new_alias_name}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{ALIASES_API_URL}{alias_id}", json=payload, headers=headers)
            response.raise_for_status()
        await status_msg.edit_text(f"✅ نام مستعار **'{old_alias_name}'** با موفقیت به **'{new_alias_name}'** ویرایش شد.", parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        await status_msg.edit_text(f"❌ {detail}\n\nعملیات ثبت نشد. لطفاً دوباره امتحان کنید.", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطای پیش‌بینی نشده: {e}\n\nعملیات انجام نشد.")
    await show_commodity_list(message, user)

# === 5. حذف نام مستعار (Alias DELETE) (بدون تغییر) ===
@router.callback_query(F.data.startswith("alias_delete_"), StateFilter(None))
async def handle_alias_delete_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    parts = query.data.split("_"); commodity_id = int(parts[2]); alias_id = int(parts[3])
    alias_name = f"(ID: {alias_id})"
    try:
        for row in query.message.reply_markup.inline_keyboard:
            if len(row) == 3 and row[2].callback_data == query.data:
                alias_name = row[0].text; break
    except Exception as e:
        logger.warning(f"Could not parse alias name from keyboard for delete: {e}")
    await state.set_state(CommodityManagement.awaiting_alias_delete_confirm)
    await state.update_data(alias_to_delete_id=alias_id, alias_to_delete_name=alias_name, commodity_id=commodity_id, list_message_id=query.message.message_id)
    await query.message.edit_text(f"--- حذف نام مستعار ---\n\n⚠️ آیا از حذف نام مستعار **'{alias_name}'** مطمئن هستید؟", reply_markup=get_alias_delete_confirm_keyboard(commodity_id, alias_id), parse_mode="Markdown")
    await query.answer()

@router.callback_query(F.data.startswith("alias_delete_confirm_yes_"), StateFilter(CommodityManagement.awaiting_alias_delete_confirm))
async def handle_alias_delete_yes(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    data = await state.get_data()
    alias_id = data.get("alias_to_delete_id")
    alias_name = data.get("alias_to_delete_name", f"(ID: {alias_id})")
    commodity_id = data.get("commodity_id")
    await state.clear()
    if alias_id is None or commodity_id is None:
        await query.answer("خطا: ID نام مستعار یا کالا یافت نشد.", show_alert=True)
        await safe_delete_message(query.bot, query.message.chat.id, query.message.message_id); await show_commodity_list(query.message, user)
        return
    parts = query.data.split("_"); button_commodity_id = int(parts[4]); button_alias_id = int(parts[5])
    if button_alias_id != alias_id or button_commodity_id != commodity_id:
        await query.answer("خطا: عدم تطابق ID.", show_alert=True)
        await safe_delete_message(query.bot, query.message.chat.id, query.message.message_id); await show_commodity_list(query.message, user)
        return
    await query.answer(f"در حال حذف **'{alias_name}'**...", parse_mode="Markdown")
    headers = get_auth_headers()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{ALIASES_API_URL}{alias_id}", headers=headers)
            response.raise_for_status()
        await query.message.answer(f"✅ نام مستعار **'{alias_name}'** با موفقیت حذف شد.", parse_mode="Markdown")
        await safe_delete_message(query.bot, query.message.chat.id, query.message.message_id)
        await show_aliases_list(query, user, commodity_id, edit=False)
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        await query.message.edit_text(f"❌ {detail}\n\nعملیات حذف ناموفق بود.", parse_mode="Markdown")
    except Exception as e:
        await query.message.edit_text(f"❌ خطای پیش‌بینی نشده: {e}")

# === 7. ویرایش نام اصلی کالا (اصلاح شد) ===

@router.callback_query(F.data.startswith("comm_edit_name_"), StateFilter(None))
async def handle_commodity_edit_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    """FSM را برای ویرایش نام اصلی کالا آغاز می‌کند."""
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    
    # === شروع اصلاح: واکشی نام کالا از API ===
    await query.answer("در حال دریافت اطلاعات کالا...")
    headers = get_auth_headers()
    commodity_name = f"(ID: {commodity_id})" # Fallback
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{COMMODITIES_API_URL}{commodity_id}", headers=headers)
            response.raise_for_status()
        commodity = response.json()
        commodity_name = commodity.get('name', commodity_id)
    except Exception as e:
        logger.error(f"Could not fetch commodity name for edit: {e}")
        await query.message.answer(f"خطا در دریافت اطلاعات کالا: {e}")
        return
    # === پایان اصلاح ===
            
    await state.set_state(CommodityManagement.awaiting_commodity_edit_name)
    await state.update_data(
        commodity_id=commodity_id,
        commodity_name=commodity_name,
        list_message_id=query.message.message_id
    )
    
    await query.message.edit_text(
        f"--- ویرایش نام کالا ---\n\n"
        f"نام فعلی کالا: **{commodity_name}**\n\n" # <-- نام صحیح نمایش داده می‌شود
        "لطفاً **نام جدید** را وارد کنید:",
        reply_markup=get_commodity_fsm_cancel_keyboard(),
        parse_mode="Markdown"
    )

@router.message(CommodityManagement.awaiting_commodity_edit_name)
async def handle_commodity_edit_name(message: types.Message, state: FSMContext, user: Optional[User]):
    """نام جدید کالا را دریافت و API را فراخوانی می‌کند."""
    if not user: return
    new_commodity_name = message.text
    
    data = await state.get_data()
    commodity_id = data.get("commodity_id")
    old_commodity_name = data.get("commodity_name", f"(ID: {commodity_id})")
    list_message_id = data.get("list_message_id")
    
    await state.clear()
    
    await message.delete()
    if list_message_id:
        await safe_delete_message(message.bot, message.chat.id, list_message_id)

    status_msg = await message.answer(f"در حال ویرایش نام کالا از **'{old_commodity_name}'** به **'{new_commodity_name}'**...", parse_mode="Markdown")
    
    headers = get_auth_headers()
    payload = {"name": new_commodity_name}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{COMMODITIES_API_URL}{commodity_id}", json=payload, headers=headers)
            response.raise_for_status()
        
        await status_msg.edit_text(f"✅ نام کالا با موفقیت به **'{new_commodity_name}'** تغییر یافت.", parse_mode="Markdown")
        await show_commodity_list(message, user) # بازگشت به لیست اصلی

    except httpx.HTTPStatusError as e:
        # === اصلاح شد: بازگشت به لیست در صورت خطا ===
        detail = get_error_detail(e)
        await status_msg.edit_text(f"❌ {detail}\n\nعملیات ویرایش ناموفق بود.", parse_mode="Markdown")
        await show_commodity_list(message, user) # بازگشت به لیست اصلی
    except Exception as e:
        # === اصلاح شد: بازگشت به لیست در صورت خطا ===
        await status_msg.edit_text(f"❌ خطای پیش‌بینی نشده: {e}")
        await show_commodity_list(message, user) # بازگشت به لیست اصلی


# === 8. FSM های قدیمی (افزودن/حذف کل کالا) ===
@router.callback_query(F.data == "comm_fsm_cancel", StateFilter("*"))
@router.message(F.text == "لغو", StateFilter("*"))
async def handle_cancel_fsm(event: types.Message | types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user: return
    current_state = await state.get_state()
    target_message = event.message if isinstance(event, types.CallbackQuery) else event
    data = await state.get_data()
    await state.clear()
    logger.info(f"Cancel FSM requested. Current state: {current_state}")
    
    edit_message_existed = False
    commodity_id_to_return = None
    
    if isinstance(event, types.CallbackQuery):
        await event.answer("لغو شد")
        
        # === State جدید را به لیست اضافه کنید ===
        if current_state in (
            CommodityManagement.awaiting_alias_add_name.state, 
            CommodityManagement.awaiting_alias_edit_name.state,
            CommodityManagement.awaiting_alias_delete_confirm.state,
            CommodityManagement.awaiting_commodity_edit_name.state # <-- اضافه شد
        ):
            commodity_id_to_return = data.get("commodity_id")
        
        try: 
            await event.message.edit_text("عملیات لغو شد. در حال بازگشت...")
            edit_message_existed = True
        except TelegramBadRequest: 
             try: await event.message.delete()
             except TelegramBadRequest: pass
    
    elif isinstance(event, types.Message):
        await event.delete()
        prompt_message_id = data.get("prompt_message_id")
        if prompt_message_id:
            await safe_delete_message(event.bot, event.chat.id, prompt_message_id)
            
    if not edit_message_existed:
         await target_message.answer("عملیات لغو شد.")

    if commodity_id_to_return and isinstance(event, types.CallbackQuery):
        await show_aliases_list(event, user, commodity_id_to_return, edit=True)
    else:
        await show_commodity_list(target_message, user, edit=edit_message_existed)

# --- افزودن کالا (بدون تغییر) ---
@router.callback_query(F.data == "comm_add_new", StateFilter(None))
async def handle_add_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    logger.info("Starting add commodity flow")
    await state.set_state(CommodityManagement.awaiting_add_name)
    await state.update_data(prompt_message_id=query.message.message_id) 
    await query.message.edit_text("--- افزودن کالای جدید ---\n\nلطفاً **نام اصلی** کالا را وارد کنید:", reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown")
    await query.answer()

@router.message(CommodityManagement.awaiting_add_name)
async def handle_add_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    logger.info(f"Received commodity name: {message.text}")
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")
    await message.delete()
    if prompt_message_id: await safe_delete_message(message.bot, message.chat.id, prompt_message_id)
    await state.update_data(name=message.text)
    await state.set_state(CommodityManagement.awaiting_add_aliases)
    new_prompt = await message.answer(f"نام کالا: **{message.text}**\n\nحالا **نام‌های مستعار (alias)** را وارد کنید (جدا با `،` یا `-`). اگر ندارد، **ندارد** را ارسال کنید:", reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown")
    await state.update_data(prompt_message_id=new_prompt.message_id)

@router.message(CommodityManagement.awaiting_add_aliases)
async def handle_add_aliases_and_create(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    logger.info(f"Received commodity aliases: {message.text}. Creating...")
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")
    commodity_name = data.get("name")
    await message.delete()
    if prompt_message_id:
        await safe_delete_message(message.bot, message.chat.id, prompt_message_id)
    await state.clear()
    status_msg = await message.answer(f"در حال ثبت کالا **'{commodity_name}'**...", parse_mode="Markdown")
    aliases_text = message.text.strip()
    final_aliases = [commodity_name.strip()]
    if aliases_text.lower() != "ندارد":
        additional_aliases = [alias.strip() for alias in re.split('[،-]', aliases_text) if alias.strip()]
        for alias in additional_aliases:
            if alias not in final_aliases:
                final_aliases.append(alias)
    alias_list = final_aliases
    payload = {"name": commodity_name, "aliases": alias_list}
    headers = get_auth_headers()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(COMMODITIES_API_URL, json=payload, headers=headers)
            response.raise_for_status()
        logger.info(f"Commodity '{commodity_name}' created successfully.")
        await status_msg.edit_text(f"✅ کالا **'{commodity_name}'** با موفقیت ثبت شد.", parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        logger.error(f"API error creating commodity: {detail}"); await status_msg.edit_text(f"❌ {detail}\n\nعملیات ثبت نشد. لطفاً دوباره امتحان کنید.", parse_mode="Markdown")
    except Exception as e: 
        logger.exception("Unexpected error creating commodity"); await status_msg.edit_text(f"❌ خطای پیش‌بینی نشده: {e}\n\nعملیات انجام نشد.")
    await show_commodity_list(message, user)

# --- 9. حذف کل کالا (بدون تغییر) ---
@router.callback_query(F.data.startswith("comm_delete_"), StateFilter(None))
async def handle_delete_confirm(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    logger.info(f"Delete requested for ENTIRE commodity ID: {commodity_id}. Setting state.")
    await query.answer("در حال دریافت اطلاعات کالا...")
    headers = get_auth_headers()
    commodity_name = f"(ID: {commodity_id})"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{COMMODITIES_API_URL}{commodity_id}", headers=headers)
            response.raise_for_status()
        commodity = response.json()
        commodity_name = commodity.get('name', commodity_id)
    except Exception as e:
        logger.error(f"Could not fetch commodity name for delete confirmation: {e}")
        await query.message.answer(f"خطا در دریافت اطلاعات کالا: {e}")
        return
    await state.set_state(CommodityManagement.awaiting_delete_confirmation)
    await state.update_data(commodity_to_delete_id=commodity_id, commodity_to_delete_name=commodity_name, list_message_id=query.message.message_id)
    logger.info(f"State is now set to: {await state.get_state()}")
    await query.message.edit_text(f"--- حذف کالا ---\n\n⚠️ **هشدار:** آیا از حذف کامل کالا **'{commodity_name}'** مطمئن هستید؟", reply_markup=get_commodity_delete_confirm_keyboard(commodity_id), parse_mode="Markdown")

@router.callback_query(F.data.startswith("comm_delete_confirm_yes_"), StateFilter(CommodityManagement.awaiting_delete_confirmation))
async def handle_delete_yes(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    logger.info(f"Handling delete confirmation for ENTIRE commodity. Callback data: {query.data}")
    data = await state.get_data()
    commodity_id = data.get("commodity_to_delete_id")
    commodity_name = data.get("commodity_to_delete_name", f"(ID: {commodity_id})")
    await state.clear()
    logger.info("State cleared.")
    await safe_delete_message(query.bot, query.message.chat.id, query.message.message_id)
    if commodity_id is None:
        logger.error("Commodity ID not found in state during delete confirmation.")
        await query.message.answer("خطا: ID کالا برای حذف یافت نشد.")
        await show_commodity_list(query.message, user)
        return
    button_commodity_id = int(query.data.split("_")[-1])
    if button_commodity_id != commodity_id:
        logger.warning(f"Button ID ({button_commodity_id}) mismatch with state ID ({commodity_id}). Aborting.")
        await query.message.answer("خطا: عدم تطابق ID. لطفاً دوباره امتحان کنید.")
        await show_commodity_list(query.message, user)
        return
    await query.answer(f"در حال حذف کالا **'{commodity_name}'**...", show_alert=False, parse_mode="Markdown")
    headers = get_auth_headers()
    try:
        logger.info(f"Calling DELETE API for commodity ID: {commodity_id}")
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{COMMODITIES_API_URL}{commodity_id}", headers=headers)
            response.raise_for_status()
        logger.info(f"Commodity ID {commodity_id} deleted successfully via API.")
        await query.message.answer(f"✅ کالا **'{commodity_name}'** با موفقیت حذف شد.", parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        logger.error(f"API error deleting commodity: {detail}")
        await query.message.answer(f"❌ {detail}\n\nعملیات حذف ناموفق بود.", parse_mode="Markdown")
    except TelegramBadRequest as e:
        logger.warning(f"Telegram error during delete process: {e}")
        await query.message.answer(f"❌ خطای تلگرام در حین پردازش حذف: {e}")
    except Exception as e:
        logger.exception("Unexpected error during delete process")
        await query.message.answer(f"❌ خطای پیش‌بینی نشده: {e}\n\nعملیات انجام نشد.")
    await show_commodity_list(query.message, user)