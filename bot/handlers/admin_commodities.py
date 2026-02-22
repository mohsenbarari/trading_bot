# trading_bot/bot/handlers/admin_commodities.py
import re
import httpx
import logging
import asyncio
import json
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

logger = logging.getLogger(__name__)
router = Router()

COMMODITIES_API_URL = "http://app:8000/api/commodities/"
ALIASES_API_URL = "http://app:8000/api/commodities/aliases/"

# --- توابع کمکی مدیریت پیام ---

def get_auth_headers() -> dict:
    if not settings.dev_api_key: 
        return {"X-DEV-API-KEY": "NOT_SET"}
    return {"X-DEV-API-KEY": settings.dev_api_key}

def get_error_detail(e: httpx.HTTPStatusError) -> str:
    try:
        response_json = e.response.json()
        detail = response_json.get("detail")
        if not detail:
            return e.response.text
        if isinstance(detail, (list, dict)):
            return json.dumps(detail, ensure_ascii=False)
        return str(detail)
    except json.JSONDecodeError:
        return e.response.text

async def safe_delete_message(bot: Bot, chat_id: int, message_id: int, delay: int = 0):
    """پیام را با تأخیر اختیاری حذف می‌کند و خطاها را نادیده می‌گیرد."""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def update_anchor(state: FSMContext, new_message_id: int, bot: Bot, chat_id: int):
    """
    لنگر جدید را ثبت و لنگر قدیمی را با تاخیر ۳۰ ثانیه حذف می‌کند.
    """
    data = await state.get_data()
    old_anchor_id = data.get("anchor_id")
    
    # 1. ثبت لنگر جدید
    await state.update_data(anchor_id=new_message_id)
    
    # 2. حذف لنگر قدیمی با تاخیر ۳۰ ثانیه
    if old_anchor_id and old_anchor_id != new_message_id:
        asyncio.create_task(safe_delete_message(bot, chat_id, old_anchor_id, delay=30))

async def clear_state_retain_anchor(state: FSMContext):
    """استیت را پاک می‌کند اما لنگر (anchor_id) را نگه می‌دارد."""
    data = await state.get_data()
    anchor_id = data.get("anchor_id")
    await state.clear()
    if anchor_id:
        await state.update_data(anchor_id=anchor_id)

async def delete_user_message(message: types.Message):
    """پیام کاربر را بلافاصله حذف می‌کند."""
    try:
        await message.delete()
    except Exception:
        pass

# --- توابع نمایش (Views) ---

async def show_commodity_list(bot: Bot, chat_id: int, user: User, state: FSMContext):
    """لیست کالاها را نمایش می‌دهد."""
    if user.role != UserRole.SUPER_ADMIN: return
    
    try:
        headers = get_auth_headers()
        async with httpx.AsyncClient() as client:
            response = await client.get(COMMODITIES_API_URL, timeout=10.0, headers=headers)
            response.raise_for_status()
            raw = response.json()
        # نرمال کردن به لیست: گاهی پاسخ dict با کلیدهای عددی است
        if isinstance(raw, dict):
            commodities = list(raw.values()) if raw else []
        elif isinstance(raw, list):
            commodities = raw
        else:
            commodities = []

        text = "📦 **مدیریت کالاها**\n\nلیست کالاهای ثبت شده:"
        buttons = []
        if not commodities:
            text = "📦 **مدیریت کالاها**\n\nهیچ کالایی ثبت نشده است."
        else:
            for comm in commodities:
                if not isinstance(comm, dict):
                    continue
                name = comm.get("name")
                cid = comm.get("id")
                if name is not None and cid is not None:
                    buttons.append([
                        InlineKeyboardButton(text=f"📦 {name}", callback_data=f"comm_manage_aliases_{cid}"),
                    ])
        buttons.append([InlineKeyboardButton(text="➕ افزودن کالای جدید", callback_data="comm_add_new")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # ارسال پیام جدید
        msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")
        
        # این پیام می‌شود لنگر جدید؛ لنگر قبلی ۳۰ ثانیه دیگر پاک می‌شود
        await update_anchor(state, msg.message_id, bot, chat_id)
            
    except Exception as e:
        logger.exception("Error showing list")
        err = await bot.send_message(chat_id, f"❌ خطای سیستمی: {e}")
        asyncio.create_task(safe_delete_message(bot, chat_id, err.message_id, delay=30))


async def show_aliases_list(bot: Bot, chat_id: int, user: User, state: FSMContext, commodity_id: int):
    """لیست نام‌های مستعار را نمایش می‌دهد."""
    if not user: return
    try:
        headers = get_auth_headers()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{COMMODITIES_API_URL}{commodity_id}", headers=headers)
            response.raise_for_status()
        commodity = response.json()
        
        text = f"🔧 مدیریت کالا: **{commodity['name']}**\n\nلیست نام‌های مستعار:"
        if not commodity.get('aliases'):
            text += "\n_(هیچ نام مستعاری ثبت نشده)_"
            
        keyboard = get_aliases_list_keyboard(commodity)
        
        msg = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="Markdown")
        
        # این پیام می‌شود لنگر جدید؛ لنگر قبلی (مثلاً لیست کالاها) ۳۰ ثانیه دیگر پاک می‌شود
        await update_anchor(state, msg.message_id, bot, chat_id)

    except Exception:
        await show_commodity_list(bot, chat_id, user, state)


# === هندلرهای اصلی ===

@router.message(F.text == "📦 مدیریت کالاها")
async def handle_manage_commodities(message: types.Message, user: Optional[User], state: FSMContext):
    if not user: return
    await delete_user_message(message)
    # نمایش لیست جدید (لیست قبلی اگر وجود داشته باشد توسط update_anchor پاک می‌شود)
    await show_commodity_list(message.bot, message.chat.id, user, state)

@router.callback_query(F.data == "comm_back_to_list", StateFilter("*"))
async def handle_back_to_list(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    await clear_state_retain_anchor(state)
    # نکته: پیام فعلی (لیست نام‌های مستعار) را دستی پاک نمی‌کنیم.
    # وقتی show_commodity_list پیام جدید بفرستد، این پیام به عنوان anchor قدیمی ۳۰ ثانیه بعد پاک می‌شود.
    await show_commodity_list(query.bot, query.message.chat.id, user, state)

@router.callback_query(F.data.startswith("comm_manage_aliases_"))
async def handle_manage_aliases(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    commodity_id = int(query.data.split("_")[-1])
    # اینجا هم دستی پاک نمی‌کنیم تا ۳۰ ثانیه بماند
    await show_aliases_list(query.bot, query.message.chat.id, user, state, commodity_id)


# === 3. افزودن نام مستعار ===
@router.callback_query(F.data.startswith("alias_add_"), StateFilter(None))
async def handle_alias_add_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    await state.set_state(CommodityManagement.awaiting_alias_add_name)
    await state.update_data(commodity_id=commodity_id)
    
    # در اینجا چون edit_text استفاده شده، anchor_id تغییر نمی‌کند و پیام باقی می‌ماند (که درست است)
    await query.message.edit_text(
        "➕ **افزودن نام مستعار**\n\nلطفاً نام مستعار جدید را وارد کنید:", 
        reply_markup=get_commodity_fsm_cancel_keyboard(), 
        parse_mode="Markdown"
    )

@router.message(CommodityManagement.awaiting_alias_add_name)
async def handle_alias_add_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    await delete_user_message(message)
    
    new_alias_name = message.text
    data = await state.get_data()
    commodity_id = data.get("commodity_id")
    
    # پیام وضعیت ارسال می‌شود و تبدیل به لنگر جدید می‌شود. فرم قبلی ۳۰ ثانیه دیگر پاک می‌شود.
    status_msg = await message.answer(f"⏳ در حال افزودن **'{new_alias_name}'**...", parse_mode="Markdown")
    await update_anchor(state, status_msg.message_id, message.bot, message.chat.id)
    await clear_state_retain_anchor(state)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{COMMODITIES_API_URL}{commodity_id}/aliases", json={"alias": new_alias_name}, headers=get_auth_headers())
            response.raise_for_status()
        
        await status_msg.edit_text(f"✅ نام مستعار **'{new_alias_name}'** افزوده شد.", parse_mode="Markdown")
        
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        await status_msg.edit_text(f"❌ خطا: {detail}", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطای سیستمی: {e}", parse_mode="Markdown")
    
    # کمی مکث برای خواندن پیام موفقیت
    await asyncio.sleep(1.5)
    # نمایش لیست جدید (پیام موفقیت ۳۰ ثانیه دیگر پاک می‌شود)
    await show_aliases_list(message.bot, message.chat.id, user, state, commodity_id)


# === 4. ویرایش نام مستعار ===
@router.callback_query(F.data.startswith("alias_edit_"), StateFilter(None))
async def handle_alias_edit_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    parts = query.data.split("_"); commodity_id = int(parts[2]); alias_id = int(parts[3])
    
    alias_name = "---"
    try:
        for row in query.message.reply_markup.inline_keyboard:
            if len(row) == 3 and row[1].callback_data == query.data:
                alias_name = row[0].text; break
    except Exception: pass

    await state.set_state(CommodityManagement.awaiting_alias_edit_name)
    await state.update_data(alias_id=alias_id, alias_name=alias_name, commodity_id=commodity_id)
    
    await query.message.edit_text(
        f"✏️ **ویرایش نام مستعار**\n\nنام فعلی: {alias_name}\n\nلطفاً نام جدید را وارد کنید:", 
        reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown"
    )

@router.message(CommodityManagement.awaiting_alias_edit_name)
async def handle_alias_edit_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    await delete_user_message(message)
    
    new_name = message.text
    data = await state.get_data()
    alias_id = data.get("alias_id")
    commodity_id = data.get("commodity_id")
    
    status_msg = await message.answer(f"⏳ در حال ویرایش...", parse_mode="Markdown")
    await update_anchor(state, status_msg.message_id, message.bot, message.chat.id)
    await clear_state_retain_anchor(state)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{ALIASES_API_URL}{alias_id}", json={"alias": new_name}, headers=get_auth_headers())
            response.raise_for_status()
        await status_msg.edit_text(f"✅ ویرایش شد.", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {e}", parse_mode="Markdown")
        
    await asyncio.sleep(1.5)
    await show_aliases_list(message.bot, message.chat.id, user, state, commodity_id)


# === 5. حذف نام مستعار ===
@router.callback_query(F.data.startswith("alias_delete_"), StateFilter(None))
async def handle_alias_delete_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    parts = query.data.split("_"); commodity_id = int(parts[2]); alias_id = int(parts[3])
    
    alias_name = "---"
    try:
        for row in query.message.reply_markup.inline_keyboard:
            if len(row) == 3 and row[2].callback_data == query.data:
                alias_name = row[0].text; break
    except Exception: pass
        
    await state.set_state(CommodityManagement.awaiting_alias_delete_confirm)
    await state.update_data(alias_to_delete_id=alias_id, commodity_id=commodity_id)
    
    await query.message.edit_text(
        f"❌ **حذف نام مستعار**\n\nآیا از حذف **'{alias_name}'** مطمئن هستید؟", 
        reply_markup=get_alias_delete_confirm_keyboard(commodity_id, alias_id), parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("alias_delete_confirm_yes_"), StateFilter(CommodityManagement.awaiting_alias_delete_confirm))
async def handle_alias_delete_yes(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    data = await state.get_data()
    alias_id = data.get("alias_to_delete_id")
    commodity_id = data.get("commodity_id")
    
    status_msg = await query.message.edit_text("⏳ در حال حذف...", reply_markup=None)
    await update_anchor(state, status_msg.message_id, query.bot, query.message.chat.id)
    await clear_state_retain_anchor(state)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{ALIASES_API_URL}{alias_id}", headers=get_auth_headers())
            response.raise_for_status()
        await status_msg.edit_text("✅ حذف شد.")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {e}")
        
    await asyncio.sleep(1.5)
    await show_aliases_list(query.bot, query.message.chat.id, user, state, commodity_id)


# === 6. ویرایش نام اصلی کالا ===
@router.callback_query(F.data.startswith("comm_edit_name_"), StateFilter(None))
async def handle_commodity_edit_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    
    await state.set_state(CommodityManagement.awaiting_commodity_edit_name)
    await state.update_data(commodity_id=commodity_id)
    
    await query.message.edit_text(
        f"✏️ **ویرایش نام کالا**\n\nلطفاً نام جدید کالا را وارد کنید:",
        reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown"
    )

@router.message(CommodityManagement.awaiting_commodity_edit_name)
async def handle_commodity_edit_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    await delete_user_message(message)
    
    new_name = message.text
    data = await state.get_data()
    commodity_id = data.get("commodity_id")
    
    status_msg = await message.answer(f"⏳ در حال تغییر نام...", parse_mode="Markdown")
    await update_anchor(state, status_msg.message_id, message.bot, message.chat.id)
    await clear_state_retain_anchor(state)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{COMMODITIES_API_URL}{commodity_id}", json={"name": new_name}, headers=get_auth_headers())
            response.raise_for_status()
        await status_msg.edit_text(f"✅ نام کالا تغییر یافت.", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {e}", parse_mode="Markdown")
        
    await asyncio.sleep(1.5)
    await show_commodity_list(message.bot, message.chat.id, user, state)


# === 7. افزودن کالا ===
@router.callback_query(F.data == "comm_add_new", StateFilter(None))
async def handle_add_start(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    await state.set_state(CommodityManagement.awaiting_add_name)
    
    await query.message.edit_text(
        "➕ **افزودن کالای جدید**\n\nلطفاً **نام اصلی** کالا را وارد کنید:", 
        reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown"
    )

@router.message(CommodityManagement.awaiting_add_name)
async def handle_add_name(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    await delete_user_message(message)
    
    name = message.text.strip()
    
    # بررسی تکراری بودن نام
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(COMMODITIES_API_URL, headers=get_auth_headers())
            response.raise_for_status()
            raw = response.json()
            if isinstance(raw, dict):
                all_commodities = list(raw.values()) if raw else []
            elif isinstance(raw, list):
                all_commodities = raw
            else:
                all_commodities = []
            if any(isinstance(c, dict) and c.get("name") == name for c in all_commodities):
                error_msg = await message.answer(
                    f"❌ کالایی با نام **'{name}'** قبلاً ثبت شده است.\nلطفاً یک نام **جدید** وارد کنید:",
                    reply_markup=get_commodity_fsm_cancel_keyboard(),
                    parse_mode="Markdown"
                )
                await update_anchor(state, error_msg.message_id, message.bot, message.chat.id)
                return
    except Exception:
        pass 
    
    await state.update_data(name=name)
    await state.set_state(CommodityManagement.awaiting_add_aliases)
    
    msg = await message.answer(
        f"نام کالا: **{name}**\n\nحالا **نام‌های مستعار** را وارد کنید (جدا با `،` یا `-`). اگر ندارد، **ندارد** را ارسال کنید:",
        reply_markup=get_commodity_fsm_cancel_keyboard(), parse_mode="Markdown"
    )
    await update_anchor(state, msg.message_id, message.bot, message.chat.id)

@router.message(CommodityManagement.awaiting_add_aliases)
async def handle_add_aliases_and_create(message: types.Message, state: FSMContext, user: Optional[User]):
    if not user: return
    await delete_user_message(message)
    
    data = await state.get_data()
    commodity_name = data.get("name")
    
    status_msg = await message.answer(f"⏳ در حال ثبت کالا...", parse_mode="Markdown")
    await update_anchor(state, status_msg.message_id, message.bot, message.chat.id)
    await clear_state_retain_anchor(state)
    
    aliases_text = message.text.strip()
    final_aliases = [commodity_name.strip()]
    if aliases_text.lower() != "ندارد":
        additional_aliases = [alias.strip() for alias in re.split('[،-]', aliases_text) if alias.strip()]
        for alias in additional_aliases:
            if alias not in final_aliases:
                final_aliases.append(alias)
    
    payload = {
        "commodity_data": {"name": commodity_name},
        "aliases": final_aliases
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(COMMODITIES_API_URL, json=payload, headers=get_auth_headers())
            response.raise_for_status()
        await status_msg.edit_text(f"✅ کالا **'{commodity_name}'** ثبت شد.", parse_mode="Markdown")
    except httpx.HTTPStatusError as e:
        detail = get_error_detail(e)
        await status_msg.edit_text(f"❌ خطا: {detail}", parse_mode="Markdown")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {e}", parse_mode="Markdown")
    
    await asyncio.sleep(1.5)
    await show_commodity_list(message.bot, message.chat.id, user, state)


# === 8. حذف کل کالا ===
@router.callback_query(F.data.startswith("comm_delete_"), StateFilter(None))
async def handle_delete_confirm(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user or user.role != UserRole.SUPER_ADMIN: return
    commodity_id = int(query.data.split("_")[-1])
    
    await state.set_state(CommodityManagement.awaiting_delete_confirmation)
    await state.update_data(commodity_to_delete_id=commodity_id)
    
    await query.message.edit_text(
        f"🗑 **حذف کالا**\n\n⚠️ آیا از حذف کامل این کالا و تمام نام‌های مستعار آن مطمئن هستید؟", 
        reply_markup=get_commodity_delete_confirm_keyboard(commodity_id), parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("comm_delete_confirm_yes_"), StateFilter(CommodityManagement.awaiting_delete_confirmation))
async def handle_delete_yes(query: types.CallbackQuery, user: Optional[User], state: FSMContext):
    if not user: return
    data = await state.get_data()
    commodity_id = data.get("commodity_to_delete_id")
    
    status_msg = await query.message.edit_text("⏳ در حال حذف کالا...", reply_markup=None)
    await update_anchor(state, status_msg.message_id, query.bot, query.message.chat.id)
    await clear_state_retain_anchor(state)
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{COMMODITIES_API_URL}{commodity_id}", headers=get_auth_headers())
            response.raise_for_status()
        await status_msg.edit_text("✅ کالا حذف شد.")
    except Exception as e:
        await status_msg.edit_text(f"❌ خطا: {e}")
        
    await asyncio.sleep(1.5)
    await show_commodity_list(query.bot, query.message.chat.id, user, state)


# === لغو عملیات ===
@router.callback_query(F.data == "comm_fsm_cancel", StateFilter("*"))
async def handle_cancel_fsm(query: types.CallbackQuery, state: FSMContext, user: Optional[User]):
    if not user: return
    data = await state.get_data()
    commodity_id = data.get("commodity_id")
    await clear_state_retain_anchor(state)
    
    # پیام فعلی را با تاخیر ۳۰ ثانیه پاک کن (توسط update_anchor در توابع بعدی مدیریت می‌شود)
    # اینجا مستقیماً تابع نمایش را صدا می‌زنیم که پیام جدید می‌سازد و قبلی را پاک می‌کند.
    
    if commodity_id:
        await show_aliases_list(query.bot, query.message.chat.id, user, state, commodity_id)
    else:
        await show_commodity_list(query.bot, query.message.chat.id, user, state)