# core/services/trade_service.py
"""
Trade Service - منطق مشترک معاملات

این ماژول شامل توابع محاسبه لات، اعتبارسنجی و پیشنهاد قیمت است
که هم توسط بات و هم توسط API استفاده می‌شود.
"""
from typing import Tuple, List, Optional, Union

from core.trading_settings import get_trading_settings

__all__ = [
    "suggest_lot_combination",
    "generate_default_lots",
    "validate_lot_sizes",
    "validate_quantity",
    "validate_price",
    "parse_lot_sizes_text",
    "validate_competitive_price",
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

# تحمل قیمت (0.3%)
PRICE_TOLERANCE = 0.003


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
    - فروش: قیمت نباید بیش از 0.3% بالاتر از میانگین باشد
    - خرید: قیمت نباید بیش از 0.3% پایین‌تر از میانگین باشد
    
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
    from sqlalchemy import select, func
    from models.offer import Offer, OfferStatus, OfferType
    
    # تعیین رنج تعداد
    qty_range = get_quantity_range(quantity)
    if qty_range is None:
        # اگر خارج از رنج‌های تعریف شده باشد، بدون اعتبارسنجی تایید می‌شود
        return True, ""
    
    # محدوده رنج
    min_qty, max_qty = QUANTITY_RANGES[qty_range]
    
    # تبدیل نوع به enum
    offer_type_enum = OfferType.SELL if offer_type == "sell" else OfferType.BUY
    
    # کوئری لفظ‌های مشابه فعال
    stmt = select(Offer.price).where(
        Offer.commodity_id == commodity_id,
        Offer.offer_type == offer_type_enum,
        Offer.status == OfferStatus.ACTIVE,
        Offer.quantity >= min_qty,
        Offer.quantity <= max_qty,
        Offer.user_id != user_id  # لفظ‌های خود کاربر حذف
    )
    
    result = await db.execute(stmt)
    prices = [row[0] for row in result.fetchall()]
    
    # اگر کمتر از 3 لفظ مشابه وجود داشته باشد، بدون اعتبارسنجی تایید می‌شود
    if len(prices) < MIN_SIMILAR_OFFERS:
        return True, ""
    
    # محاسبه میانگین قیمت
    avg_price = sum(prices) / len(prices)
    
    # اعتبارسنجی بر اساس نوع لفظ
    if offer_type == "sell":
        # فروش: قیمت نباید بیش از 0.3% بالاتر از میانگین باشد
        max_allowed = avg_price * (1 + PRICE_TOLERANCE)
        if proposed_price > max_allowed:
            return False, "❌ لفظ شما تایید نشد.\nفروشنده‌ای با قیمت پایین‌تر وجود دارد."
    else:
        # خرید: قیمت نباید بیش از 0.3% پایین‌تر از میانگین باشد
        min_allowed = avg_price * (1 - PRICE_TOLERANCE)
        if proposed_price < min_allowed:
            return False, "❌ لفظ شما تایید نشد.\nخریداری با قیمت بالاتر وجود دارد."
    
    return True, ""
