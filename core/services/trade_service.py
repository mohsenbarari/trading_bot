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
