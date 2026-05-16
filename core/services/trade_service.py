# core/services/trade_service.py
"""
Trade Service - منطق مشترک معاملات

این ماژول شامل توابع محاسبه لات، اعتبارسنجی و پیشنهاد قیمت است
که هم توسط بات و هم توسط API استفاده می‌شود.
"""
from typing import Any, Dict, Tuple, List, Optional, Union

from core.trading_settings import get_trading_settings

__all__ = [
    "suggest_lot_combination",
    "generate_default_lots",
    "validate_lot_sizes",
    "get_available_trade_amounts",
    "validate_offer_trade_amount",
    "build_lot_unavailable_suggestion_payload",
    "validate_quantity",
    "validate_price",
    "parse_lot_sizes_text",
    "validate_competitive_price",
    "detect_offer_price_warning",
    "PRICE_WARNING_ERROR_CODE",
    "get_quantity_range",
]


# ===== INPUT VALIDATION HELPERS =====

def _ensure_int(value: Union[int, float, str], name: str) -> int:
    """
    اطمینان از اینکه مقدار ورودی یک عدد صحیح است.
    
    Args:
        value: مقدار ورودی
        name: نام پارامتر (برای پیام خطا)
        
    Returns:
        int: مقدار تبدیل شده
        
    Raises:
        TypeError: اگر تبدیل ممکن نباشد
    """
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != int(value):
            raise TypeError(f"{name} باید یک عدد صحیح باشد، نه اعشاری")
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise TypeError(f"{name} باید یک عدد صحیح باشد")
    raise TypeError(f"{name} باید یک عدد صحیح باشد")


def _ensure_int_list(values: List[Union[int, float, str]], name: str) -> List[int]:
    """
    اطمینان از اینکه لیست ورودی شامل اعداد صحیح است.
    
    Args:
        values: لیست مقادیر ورودی
        name: نام پارامتر (برای پیام خطا)
        
    Returns:
        List[int]: لیست تبدیل شده
        
    Raises:
        TypeError: اگر تبدیل ممکن نباشد
    """
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"{name} باید یک لیست باشد")
    return [_ensure_int(v, f"{name}[{i}]") for i, v in enumerate(values)]


# ===== LOT CALCULATION =====

def suggest_lot_combination(
    total: Union[int, float, str],
    user_lots: List[Union[int, float, str]]
) -> Optional[List[int]]:
    """
    پیشنهاد ترکیب بهینه لات‌ها اگر ترکیب کاربر درست نباشد.
    
    الگوریتم:
    1. اگر جمع درست است، همان را برگردان
    2. اگر کمبود داریم، به بزرگترین لات اضافه کن
    3. اگر اضافه داریم، از بزرگترین‌ها کم کن (با رعایت MIN_LOT)
    4. لات‌های کوچکتر از MIN_LOT را حذف کن
    
    Args:
        total: تعداد کل کالا
        user_lots: لیست لات‌های وارد شده توسط کاربر
        
    Returns:
        لیست پیشنهادی یا None اگر امکان اصلاح نباشد
    """
    # اعتبارسنجی ورودی
    try:
        total = _ensure_int(total, "total")
        user_lots = _ensure_int_list(user_lots, "user_lots")
    except TypeError:
        return None
    
    if not user_lots:
        return None
    
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    
    current_sum = sum(user_lots)
    
    # اگر جمع درست است، همان را برگردان
    if current_sum == total:
        return sorted(user_lots, reverse=True)
    
    # مرتب‌سازی نزولی برای کار با بزرگترین‌ها اول
    suggested = sorted(user_lots, reverse=True)
    diff = total - current_sum
    
    if diff > 0:
        # کمبود داریم - به بزرگترین اضافه کن
        suggested[0] += diff
    else:
        # اضافه داریم - از بزرگترین‌ها کم کن
        remaining_excess = -diff
        
        for i in range(len(suggested)):
            # حداکثر مقداری که می‌توانیم کم کنیم (با حفظ MIN_LOT)
            max_reduction = max(0, suggested[i] - MIN_LOT)
            reduction = min(max_reduction, remaining_excess)
            
            suggested[i] -= reduction
            remaining_excess -= reduction
            
            if remaining_excess == 0:
                break
    
    # حذف لات‌های کوچکتر از حداقل
    suggested = [lot for lot in suggested if lot >= MIN_LOT]
    
    # بررسی نهایی: آیا جمع درست شد؟
    if sum(suggested) != total:
        return None
    
    return sorted(suggested, reverse=True)


def generate_default_lots(quantity: Union[int, float, str]) -> Optional[List[int]]:
    """
    تولید ترکیب پیش‌فرض لات‌ها بر اساس تعداد.
    
    Args:
        quantity: تعداد کل کالا
        
    Returns:
        لیست لات‌های پیشنهادی
    """
    # اعتبارسنجی ورودی
    try:
        quantity = _ensure_int(quantity, "quantity")
    except TypeError:
        return None
    
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    
    # استفاده از تنظیمات به جای hardcoded مقدار 30
    THRESHOLD_3_LOTS = settings.offer_min_quantity * 6  # حدود 30 اگر min=5
    
    if quantity >= THRESHOLD_3_LOTS:
        lot1 = quantity // 3
        lot2 = quantity // 3
        lot3 = quantity - lot1 - lot2
        return sorted([lot1, lot2, lot3], reverse=True)
    elif quantity >= 2 * MIN_LOT:
        lot1 = quantity // 2
        lot2 = quantity - lot1
        return sorted([lot1, lot2], reverse=True)
    else:
        return [quantity]


# ===== VALIDATION =====

def validate_lot_sizes(
    total: Union[int, float, str],
    lot_sizes: List[Union[int, float, str]]
) -> Tuple[bool, str, Optional[List[int]]]:
    """
    اعتبارسنجی ترکیب لات‌ها.
    
    Args:
        total: تعداد کل کالا
        lot_sizes: لیست لات‌های وارد شده
        
    Returns:
        (is_valid, error_message, suggested_lots)
    """
    # اعتبارسنجی ورودی
    try:
        total = _ensure_int(total, "total")
        lot_sizes = _ensure_int_list(lot_sizes, "lot_sizes")
    except TypeError as e:
        return False, f"❌ {str(e)}", None
    
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    MAX_LOTS = settings.lot_max_count
    
    if len(lot_sizes) > MAX_LOTS:
        return False, f"❌ حداکثر {MAX_LOTS} بخش مجاز است.", None
    
    for lot in lot_sizes:
        if lot < MIN_LOT:
            return False, f"❌ هر بخش باید حداقل {MIN_LOT} عدد باشد.", None
    
    lot_sum = sum(lot_sizes)
    
    if lot_sum != total:
        suggested = suggest_lot_combination(total, lot_sizes)
        if suggested:
            return False, f"❌ جمع ترکیب ({lot_sum}) با کل ({total}) برابر نیست.\n\n💡 پیشنهاد: {' '.join(map(str, suggested))}", suggested
        else:
            return False, f"❌ جمع ترکیب ({lot_sum}) با کل ({total}) برابر نیست.", None
    
    return True, "", lot_sizes


def get_available_trade_amounts(
    quantity: Union[int, float, str],
    remaining_quantity: Optional[Union[int, float, str]],
    is_wholesale: bool,
    lot_sizes: Optional[List[Union[int, float, str]]]
) -> List[int]:
    """
    Return the exact amounts that can currently be traded for an offer.

    Wholesale offers are traded as the full remaining quantity. Retail offers
    expose both the full remaining quantity and the still-active owner-defined
    lot sizes so responders can either consume the remainder in one trade or
    pick one of the explicit lots.
    """
    try:
        total = _ensure_int(quantity, "quantity")
        remaining = _ensure_int(remaining_quantity if remaining_quantity is not None else total, "remaining_quantity")
    except TypeError:
        return []

    if remaining <= 0:
        return []

    if is_wholesale or not lot_sizes:
        return [remaining]

    try:
        normalized_lots = _ensure_int_list(lot_sizes, "lot_sizes")
    except TypeError:
        return []

    seen = {remaining}
    available: List[int] = [remaining]
    for lot in normalized_lots:
        if lot <= 0 or lot > remaining or lot in seen:
            continue
        seen.add(lot)
        available.append(lot)

    return sorted(available, reverse=True)


def validate_offer_trade_amount(
    quantity: Union[int, float, str],
    remaining_quantity: Optional[Union[int, float, str]],
    is_wholesale: bool,
    lot_sizes: Optional[List[Union[int, float, str]]],
    requested_amount: Union[int, float, str]
) -> Tuple[bool, str, int, List[int]]:
    """
    Validate that a trade amount is allowed by the offer's current state.

    The caller must run this after locking the offer row. That guarantees a
    second simultaneous request sees the lot removed by the first request and
    cannot consume the same lot again.
    """
    try:
        total = _ensure_int(quantity, "quantity")
        remaining = _ensure_int(remaining_quantity if remaining_quantity is not None else total, "remaining_quantity")
        amount = _ensure_int(requested_amount, "requested_amount")
    except TypeError as exc:
        return False, str(exc), 0, []

    if amount <= 0:
        return False, "تعداد معامله نامعتبر است.", amount, []

    if amount > remaining:
        return False, f"تعداد درخواستی بیشتر از موجودی است. موجودی: {remaining}", amount, []

    available_amounts = get_available_trade_amounts(total, remaining, is_wholesale, lot_sizes)
    if amount not in available_amounts:
        if is_wholesale or not lot_sizes:
            return False, f"تعداد درخواستی بیشتر از موجودی است. موجودی: {remaining}", amount, available_amounts
        return False, "این لات دیگر موجود نیست.", amount, available_amounts

    return True, "", amount, available_amounts


def build_lot_unavailable_suggestion_payload(
    *,
    offer_id: Union[int, float, str],
    requested_amount: Union[int, float, str],
    offer_type: Optional[Union[str, object]],
    commodity_name: Optional[str],
    price: Union[int, float, str],
    remaining_quantity: Union[int, float, str],
    available_amounts: List[Union[int, float, str]],
) -> dict:
    """
    Build a shared UI payload for the "requested retail lot was just taken"
    recovery flow used by both the bot and the web app.
    """
    normalized_offer_id = _ensure_int(offer_id, "offer_id")
    normalized_requested_amount = _ensure_int(requested_amount, "requested_amount")
    normalized_price = _ensure_int(price, "price")
    normalized_remaining = _ensure_int(remaining_quantity, "remaining_quantity")
    normalized_available_amounts = _ensure_int_list(available_amounts, "available_amounts")

    raw_offer_type = getattr(offer_type, "value", offer_type)
    normalized_offer_type = str(raw_offer_type or "").strip().lower()
    if normalized_offer_type not in {"buy", "sell"}:
        normalized_offer_type = ""

    offer_type_label = {"buy": "خرید", "sell": "فروش"}.get(normalized_offer_type, "")
    offer_type_emoji = {"buy": "🟢", "sell": "🔴"}.get(normalized_offer_type, "")
    lots_inline_text = " + ".join(str(amount) for amount in normalized_available_amounts) if normalized_available_amounts else "ندارد"
    lots_button_text = "، ".join(f"{amount} عدد" for amount in normalized_available_amounts) if normalized_available_amounts else "ندارد"
    commodity_label = (commodity_name or "کالا").strip() or "کالا"
    title = "پیشنهاد معامله"
    intro_text = f"لات {normalized_requested_amount} عددی که انتخاب کرده بودید لحظاتی قبل توسط کاربر دیگری انجام شد."
    offer_summary = (
        f"{offer_type_emoji}{offer_type_label} {commodity_label} "
        f"{normalized_remaining} عدد {normalized_price:,}"
    ).strip()
    action_text = (
        "اگر مایل هستید، یکی از دکمه\u200cهای زیر را انتخاب کنید."
        if normalized_available_amounts
        else "این پیشنهاد در حال حاضر دکمه فعالی ندارد."
    )
    message = (
        f"{title}\n\n"
        f"{intro_text}\n\n"
        f"{offer_summary}\n"
        f"🔢 خُرد: {lots_inline_text}\n\n"
        f"{action_text}"
    )

    return {
        "error_code": "TRADE_LOT_UNAVAILABLE",
        "detail": "لات انتخابی شما لحظاتی قبل انجام شد.",
        "title": title,
        "intro_text": intro_text,
        "message": message,
        "offer_id": normalized_offer_id,
        "requested_amount": normalized_requested_amount,
        "offer_type": normalized_offer_type,
        "offer_type_label": offer_type_label,
        "offer_type_emoji": offer_type_emoji,
        "commodity_name": commodity_label,
        "price": normalized_price,
        "remaining_quantity": normalized_remaining,
        "offer_summary": offer_summary,
        "lot_summary": lots_inline_text,
        "available_lots": normalized_available_amounts,
        "available_lots_text": lots_button_text,
    }


def validate_quantity(quantity: Union[int, float, str]) -> Tuple[bool, str]:
    """
    اعتبارسنجی تعداد کالا.
    
    Args:
        quantity: تعداد وارد شده
        
    Returns:
        (is_valid, error_message)
    """
    # اعتبارسنجی ورودی
    try:
        quantity = _ensure_int(quantity, "quantity")
    except TypeError as e:
        return False, f"❌ {str(e)}"
    
    settings = get_trading_settings()
    MIN_QTY = settings.offer_min_quantity
    MAX_QTY = settings.offer_max_quantity
    
    if quantity < MIN_QTY:
        return False, f"❌ تعداد باید حداقل {MIN_QTY} باشد."
    
    if quantity > MAX_QTY:
        return False, f"❌ تعداد نمی‌تواند بیشتر از {MAX_QTY} باشد."
    
    return True, ""


def validate_price(price: Union[int, float, str]) -> Tuple[bool, str]:
    """
    اعتبارسنجی قیمت.
    
    Args:
        price: قیمت وارد شده
        
    Returns:
        (is_valid, error_message)
    """
    # اعتبارسنجی ورودی
    try:
        price = _ensure_int(price, "price")
    except TypeError as e:
        return False, f"❌ {str(e)}"
    
    if price <= 0:
        return False, "❌ قیمت باید بزرگ‌تر از صفر باشد."

    if len(str(price)) not in (5, 6):
        return False, "❌ قیمت باید 5 یا 6 رقم باشد (مثال: 75800 یا 758000)"
    
    return True, ""


# ===== TEXT PARSING =====

def parse_lot_sizes_text(text: str) -> Tuple[bool, str, Optional[List[int]]]:
    """
    پارس کردن متن ترکیب لات‌ها (مثلاً "10 15 25").
    
    Args:
        text: متن وارد شده
        
    Returns:
        (is_valid, error_message, lot_sizes)
    """
    settings = get_trading_settings()
    MIN_LOT = settings.lot_min_size
    
    if not isinstance(text, str):
        return False, "❌ ورودی باید متن باشد.", None
    
    text = text.strip()
    if not text:
        return False, "❌ لطفاً ترکیب را وارد کنید.", None
    
    parts = text.split()
    lots: List[int] = []
    
    for part in parts:
        try:
            num = int(part)
            if num <= 0:
                return False, f'❌ "{part}" یک عدد معتبر نیست.', None
            lots.append(num)
        except ValueError:
            return False, f'❌ "{part}" یک عدد معتبر نیست.', None
    
    return True, "", sorted(lots, reverse=True)


# ===== COMPETITIVE PRICE VALIDATION =====

# رنج‌بندی تعداد کالا
QUANTITY_RANGES = {
    "A": (5, 20),    # 5 تا 20 عدد
    "B": (21, 40),   # 21 تا 40 عدد
    "C": (41, 50),   # 41 تا 50 عدد
}

# حداقل تعداد لفظ مشابه برای اعتبارسنجی
MIN_SIMILAR_OFFERS = 3

# تحمل قیمت (0.4%)
PRICE_TOLERANCE = 0.004

PRICE_WARNING_ERROR_CODE = "OFFER_PRICE_WARNING"
SELL_PRICE_WARNING_TYPE = "sell_below_lowest_active"
BUY_PRICE_WARNING_TYPE = "buy_above_highest_active"


def get_quantity_range(quantity: int) -> Optional[str]:
    """
    تعیین رنج تعداد کالا.
    
    Args:
        quantity: تعداد کالا
        
    Returns:
        کد رنج (A, B, C) یا None اگر خارج از محدوده باشد
    """
    for range_code, (min_qty, max_qty) in QUANTITY_RANGES.items():
        if min_qty <= quantity <= max_qty:
            return range_code
    return None


def _get_quantity_bounds(quantity: int) -> Optional[Tuple[int, int]]:
    qty_range = get_quantity_range(quantity)
    if qty_range is None:
        return None
    return QUANTITY_RANGES[qty_range]


async def _get_comparable_active_prices(
    db,
    offer_type: str,
    commodity_id: int,
    quantity: int,
    user_id: Optional[int],
) -> List[int]:
    from sqlalchemy import select
    from models.offer import Offer, OfferStatus, OfferType

    quantity_bounds = _get_quantity_bounds(quantity)
    if quantity_bounds is None:
        return []

    min_qty, max_qty = quantity_bounds
    offer_type_enum = OfferType.SELL if offer_type == "sell" else OfferType.BUY

    stmt = select(Offer.price).where(
        Offer.commodity_id == commodity_id,
        Offer.offer_type == offer_type_enum,
        Offer.status == OfferStatus.ACTIVE,
        Offer.quantity >= min_qty,
        Offer.quantity <= max_qty,
        Offer.exclude_from_competitive_price.is_(False),
    )
    if user_id is not None:
        stmt = stmt.where(Offer.user_id != user_id)

    result = await db.execute(stmt)
    return [row[0] for row in result.fetchall()]


def _build_price_warning_payload(
    *,
    warning_type: str,
    offer_type: str,
    proposed_price: int,
    reference_price: int,
    comparable_count: int,
) -> Dict[str, Any]:
    if reference_price <= 0:
        difference_percent = 0.0
    else:
        difference_percent = round(abs(proposed_price - reference_price) / reference_price * 100, 2)

    if warning_type == SELL_PRICE_WARNING_TYPE:
        title = "هشدار قیمت فروش"
        reference_label = "پایین\u200cترین قیمت فروش فعال"
        detail = (
            "قیمت فروش شما از پایین\u200cترین فروش فعال مشابه پایین\u200cتر است. "
            "اگر قیمت یا حتی کالای لفظ را اشتباه وارد کرده\u200cاید، قبل از انتشار اصلاحش کنید."
        )
    else:
        title = "هشدار قیمت خرید"
        reference_label = "بالاترین قیمت خرید فعال"
        detail = (
            "قیمت خرید شما از بالاترین خرید فعال مشابه بالاتر است. "
            "اگر قیمت یا حتی کالای لفظ را اشتباه وارد کرده\u200cاید، قبل از انتشار اصلاحش کنید."
        )

    message = (
        f"⚠️ {title}\n\n"
        f"{detail}\n\n"
        f"{reference_label}: {reference_price:,}\n"
        f"قیمت شما: {proposed_price:,}\n"
        f"اختلاف: {difference_percent}%\n\n"
        "در صورت تایید دوباره، لفظ منتشر می\u200cشود اما در محاسبه نرخ منصفانه لحاظ نخواهد شد."
    )

    return {
        "error_code": PRICE_WARNING_ERROR_CODE,
        "warning_type": warning_type,
        "offer_type": offer_type,
        "title": title,
        "detail": detail,
        "message": message,
        "reference_label": reference_label,
        "reference_price": reference_price,
        "proposed_price": proposed_price,
        "difference_amount": abs(proposed_price - reference_price),
        "difference_percent": difference_percent,
        "comparable_count": comparable_count,
    }


async def detect_offer_price_warning(
    db,
    offer_type: str,
    commodity_id: int,
    quantity: int,
    proposed_price: int,
    user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    prices = await _get_comparable_active_prices(
        db=db,
        offer_type=offer_type,
        commodity_id=commodity_id,
        quantity=quantity,
        user_id=user_id,
    )
    if not prices:
        return None

    if offer_type == "sell":
        lowest_price = min(prices)
        if proposed_price < lowest_price:
            return _build_price_warning_payload(
                warning_type=SELL_PRICE_WARNING_TYPE,
                offer_type=offer_type,
                proposed_price=proposed_price,
                reference_price=lowest_price,
                comparable_count=len(prices),
            )
        return None

    highest_price = max(prices)
    if proposed_price > highest_price:
        return _build_price_warning_payload(
            warning_type=BUY_PRICE_WARNING_TYPE,
            offer_type=offer_type,
            proposed_price=proposed_price,
            reference_price=highest_price,
            comparable_count=len(prices),
        )
    return None


async def validate_competitive_price(
    db,  # AsyncSession
    offer_type: str,
    commodity_id: int,
    quantity: int,
    proposed_price: int,
    user_id: int
) -> Tuple[bool, str]:
    """
    اعتبارسنجی قیمت رقابتی.
    
    قیمت پیشنهادی را با میانگین قیمت لفظ‌های مشابه فعال مقایسه می‌کند.
    
    قوانین:
    - اگر کمتر از 3 لفظ مشابه وجود داشته باشد: تایید می‌شود
    - فروش: قیمت نباید بیش از 0.4% بالاتر از میانگین باشد
    - خرید: قیمت نباید بیش از 0.4% پایین‌تر از میانگین باشد
    
    Args:
        db: AsyncSession دیتابیس
        offer_type: "buy" یا "sell"
        commodity_id: شناسه کالا
        quantity: تعداد کالا
        proposed_price: قیمت پیشنهادی
        user_id: شناسه کاربر (برای حذف از مقایسه)
        
    Returns:
        (True, "") اگر معتبر باشد
        (False, "پیام خطا") اگر نامعتبر باشد
    """
    quantity_bounds = _get_quantity_bounds(quantity)
    if quantity_bounds is None:
        # اگر خارج از رنج‌های تعریف شده باشد، بدون اعتبارسنجی تایید می‌شود
        return True, ""
    prices = await _get_comparable_active_prices(
        db=db,
        offer_type=offer_type,
        commodity_id=commodity_id,
        quantity=quantity,
        user_id=user_id,
    )
    
    # اگر کمتر از 3 لفظ مشابه وجود داشته باشد، بدون اعتبارسنجی تایید می‌شود
    if len(prices) < MIN_SIMILAR_OFFERS:
        return True, ""
    
    price_sum = sum(prices)
    price_count = len(prices)
    tolerance_per_thousand = int(round(PRICE_TOLERANCE * 1000))
    
    # اعتبارسنجی بر اساس نوع لفظ
    if offer_type == "sell":
        # فروش: قیمت نباید بیش از 0.4% بالاتر از میانگین باشد.
        if proposed_price * price_count * 1000 > price_sum * (1000 + tolerance_per_thousand):
            return False, "❌ لفظ شما تایید نشد.\nفروشنده‌ای با قیمت پایین‌تر وجود دارد."
    else:
        # خرید: قیمت نباید بیش از 0.4% پایین‌تر از میانگین باشد.
        if proposed_price * price_count * 1000 < price_sum * (1000 - tolerance_per_thousand):
            return False, "❌ لفظ شما تایید نشد.\nخریداری با قیمت بالاتر وجود دارد."
    
    return True, ""
