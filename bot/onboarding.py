from __future__ import annotations

from numbers import Integral

from aiogram import types


OFFER_TUTORIAL_STEP = 1
CUSTOMER_TUTORIAL_STEP = 2
BOT_ONBOARDING_REQUIRED_STEP = CUSTOMER_TUTORIAL_STEP

OFFER_TUTORIAL_ACK_CALLBACK = "bot_offer_tutorial_read"
CUSTOMER_TUTORIAL_ACK_CALLBACK = "bot_customer_tutorial_read"

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

برای رفتن به مرحله بعد، دکمه زیر را بزنید."""

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
