from __future__ import annotations

from aiogram import types


OFFER_TUTORIAL_STEP = 1
OFFER_TUTORIAL_ACK_CALLBACK = "bot_offer_tutorial_read"

OFFER_TUTORIAL_TEXT = """✅ عضویت شما در کانال معاملات تایید شد.

راهنمای سریع ثبت آفر

فرمت کلی:
خ/ف + کالا + تعداد + فی + بخش‌های خُرد اختیاری + توضیحات اختیاری

مثال:
ف ربع 20تا 49000 5 7 8 : فیش درشت

قواعد مهم:
• خرید: خ یا خرید
• فروش: ف یا فروش
• نام کالا را با نام اصلی یا مستعار بنویسید؛ مثل ربع، نیم، امامی.
• اگر نام کالا را ننویسید، آفر برای کالای پیش‌فرض «امام» ثبت می‌شود.
• تعداد باید با «تا» یا «عدد» بیاید؛ مثل 20تا یا 20 عدد.
• فی باید عدد ۵ یا ۶ رقمی و بدون ویرگول باشد؛ مثل 49000.
• اگر آفر خُرد است، بعد از فی بخش‌ها را بنویسید. جمع بخش‌ها باید برابر تعداد کل باشد.
• برای توضیحات، آخر متن «:» بگذارید و بعد توضیح را بنویسید.

نمونه‌های صحیح:
خ ربع 20تا 49000
ف نیم 10 عدد 125000
ف ربع 20تا 49000 5 7 8
خ 30تا 85000

برای فعال شدن امکانات بات، دکمه زیر را بزنید."""

OFFER_TUTORIAL_BLOCK_MESSAGE = "برای استفاده از امکانات بات، ابتدا راهنمای ثبت آفر را بخوانید و دکمه «خواندم» را بزنید."


def build_offer_tutorial_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="خواندم", callback_data=OFFER_TUTORIAL_ACK_CALLBACK)],
        ],
    )


def user_requires_offer_tutorial(user) -> bool:
    required_step = int(getattr(user, "bot_onboarding_required_step", 0) or 0)
    completed_step = int(getattr(user, "bot_onboarding_completed_step", 0) or 0)
    return required_step >= OFFER_TUTORIAL_STEP and completed_step < OFFER_TUTORIAL_STEP
