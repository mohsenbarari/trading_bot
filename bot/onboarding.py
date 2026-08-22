from __future__ import annotations

from numbers import Integral

from aiogram import types


OFFER_TUTORIAL_STEP = 1
CUSTOMER_TUTORIAL_STEP = 2
BOT_ONBOARDING_REQUIRED_STEP = CUSTOMER_TUTORIAL_STEP

OFFER_TUTORIAL_ACK_CALLBACK = "bot_offer_tutorial_read"
CUSTOMER_TUTORIAL_ACK_CALLBACK = "bot_customer_tutorial_read"

OFFER_TUTORIAL_TEXT = """✅ دسترسی شما فعال شد.

راهنمای سریع ثبت آفر

الگو:
نوع معامله + کالا (اختیاری) + تعداد + قیمت + بخش‌بندی اختیاری + توضیحات

• نقدی: «خ» برای خرید و «ف» برای فروش
• فردایی: «خ ف» برای خرید و «ف ف» برای فروش
• تعداد: «20تا» یا «20 عدد»
• قیمت: کامل یا بدون سه صفر آخر؛ «197» یعنی «197000»
• بدون نام کالا، بات گزینهٔ مناسب را برای تأیید نشان می‌دهد.
• برای تاریخ پایین «پ» و برای پک «پک» را بنویسید. پک همیشه ۱۰۰ عدد و یکجا است.
• اندازهٔ بخش‌ها را بعد از قیمت بنویسید؛ جمع آن‌ها باید برابر تعداد باشد.
• توضیحات را بعد از «:» بنویسید.

نمونه‌ها:
خ ربع 20تا 51500
ف ف نیم 10تا 125000 : شب حساب
خ پک 100600

برای ادامه «خواندم» را بزنید."""

CUSTOMER_TUTORIAL_TEXT = """راهنمای سریع مشتریان

شما می‌توانید کاربران دیگر را به عنوان مشتری و پشت‌دست خود به بات و وب اپ اضافه کنید.

در این پروژه، مشتریان دو سطح دارند:

سطح ۱
• امکان استفاده از بات تلگرام را دارد.
• امکان استفاده از وب اپ را دارد.
• نرخ کمیسیون توافقی است و در بات یا وب اپ برای این سطح، نرخ کمیسیون تعیین نمی‌شود.

سطح ۲
• فقط امکان استفاده از وب اپ را دارد.
• به بات تلگرام دسترسی ندارد.
• قابلیت تعیین نرخ کمیسیون دارد و قیمت‌ها را بر اساس نرخ کمیسیون تعیین‌شده توسط شما می‌بیند.

مشتریان شما برای کاربران دیگر قابل مشاهده نیستند و تمام معاملات مشتریان شما از کانال شما عبور می‌کند.

اگر شما مشتری ندارید یا فعلاً با این بخش کار نمی‌کنید، این راهنما فقط برای آشنایی با نقش مشتریان است.

برای شروع استفاده از بات، دکمه زیر را بزنید."""

BOT_ONBOARDING_BLOCK_MESSAGE = "برای استفاده از امکانات بات، ابتدا راهنما را بخوانید و دکمه «خواندم» را بزنید."
OFFER_TUTORIAL_BLOCK_MESSAGE = BOT_ONBOARDING_BLOCK_MESSAGE


def build_offer_tutorial_keyboard() -> types.InlineKeyboardMarkup:
    return build_onboarding_keyboard(OFFER_TUTORIAL_STEP)


def build_customer_tutorial_keyboard() -> types.InlineKeyboardMarkup:
    return build_onboarding_keyboard(CUSTOMER_TUTORIAL_STEP)


def build_onboarding_keyboard(step: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="خواندم", callback_data=onboarding_callback_for_step(step))],
        ],
    )


def _step_value(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip() or "0")
        except ValueError:
            return 0
    return 0


def onboarding_callback_for_step(step: int) -> str:
    if step >= CUSTOMER_TUTORIAL_STEP:
        return CUSTOMER_TUTORIAL_ACK_CALLBACK
    return OFFER_TUTORIAL_ACK_CALLBACK


def onboarding_text_for_step(step: int) -> str:
    if step >= CUSTOMER_TUTORIAL_STEP:
        return CUSTOMER_TUTORIAL_TEXT
    return OFFER_TUTORIAL_TEXT


def pending_onboarding_step(user) -> int | None:
    required_step = _step_value(getattr(user, "bot_onboarding_required_step", 0))
    completed_step = _step_value(getattr(user, "bot_onboarding_completed_step", 0))
    if required_step < OFFER_TUTORIAL_STEP or completed_step >= required_step:
        return None
    next_step = max(completed_step + 1, OFFER_TUTORIAL_STEP)
    return min(next_step, BOT_ONBOARDING_REQUIRED_STEP)


def user_requires_bot_onboarding(user) -> bool:
    return pending_onboarding_step(user) is not None


def user_requires_offer_tutorial(user) -> bool:
    return user_requires_bot_onboarding(user)


def is_allowed_onboarding_callback(user, callback_data: object) -> bool:
    pending_step = pending_onboarding_step(user)
    return pending_step is not None and callback_data == onboarding_callback_for_step(pending_step)
