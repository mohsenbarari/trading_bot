from aiogram import Router, types, F
from aiogram.filters import Command
from models.user import User
from bot.keyboards import get_main_menu_keyboard, get_mini_app_keyboard
from typing import Optional

router = Router()

# --- نسخه نهایی و اصلاح شده ---
@router.message(Command("panel"))
@router.message(F.text == "پنل کاربری") # به متن جدید گوش می‌دهد
async def show_panel(message: types.Message, user: Optional[User]):
    if not user:
        return
    if user.has_bot_access:
        keyboard = get_main_menu_keyboard(user.role)
        await message.answer(f"سلام {user.full_name}!\nبه پنل کاربری خود خوش آمدید.", reply_markup=keyboard)
    else:
        keyboard = get_mini_app_keyboard()
        await message.answer(
            f"سلام {user.full_name}!\nبرای دسترسی به امکانات، لطفاً از طریق پنل امن وارد شوید.",
            reply_markup=keyboard
        )

@router.callback_query(F.data == "my_profile")
async def show_my_profile(callback_query: types.CallbackQuery, user: Optional[User]):
    if not user:
        await callback_query.answer("خطا: اطلاعات شما یافت نشد.", show_alert=True)
        return
        
    if not user.has_bot_access:
        await callback_query.answer("شما دسترسی لازم برای استفاده از این بخش را ندارید.", show_alert=True)
        return

    await callback_query.answer()
    profile_text = (
        f"👤 **پروفایل شما**\n\n"
        f"🔸 **نام کاربری:** `{user.account_name}`\n"  # <-- فیلد جدید اینجا نمایش داده می‌شود
        f"🔹 **نام تلگرام:** {user.full_name}\n"
        f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
        f"🔹 **سطح دسترسی:** {user.role.value}"
    )
    await callback_query.message.answer(profile_text, parse_mode="Markdown")