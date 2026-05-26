# bot/utils/offer_parser.py
"""پارسر لفظ متنی - تبدیل متن به اطلاعات لفظ"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple, List
from sqlalchemy import select
from models.commodity import Commodity, CommodityAlias
from core.db import AsyncSessionLocal
from core.services.trade_service import validate_price
from core.trading_settings import get_trading_settings


@dataclass
class ParsedOffer:
    """نتیجه پارس شده لفظ"""
    trade_type: str  # "buy" or "sell"
    commodity_id: int
    commodity_name: str
    quantity: int
    price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]]
    notes: Optional[str]


@dataclass 
class ParseError:
    """خطای پارس"""
    message: str


# جدول تبدیل اعداد فارسی/عربی به انگلیسی
PERSIAN_DIGITS = '۰۱۲۳۴۵۶۷۸۹'
ARABIC_DIGITS = '٠١٢٣٤٥٦٧٨٩'
COMMODITY_BOUNDARY_CHARS = r'\u0600-\u06FF\u200C0-9'
BAHAR_QUALIFIERS = {"ربع", "نیم"}
IMPLICIT_DEFAULT_COMMODITY_KEYS = ("امام", "امامی", "سکه امام", "سکه امامی")


def normalize_digits(text: str) -> str:
    """تبدیل اعداد فارسی و عربی به انگلیسی"""
    result = text
    for i, (fa, ar) in enumerate(zip(PERSIAN_DIGITS, ARABIC_DIGITS)):
        result = result.replace(fa, str(i))
        result = result.replace(ar, str(i))
    return result


def validate_characters(text: str) -> Tuple[bool, Optional[str]]:
    """
    بررسی کاراکترهای مجاز در متن لفظ (قبل از :)
    مجاز: حروف فارسی/عربی، اعداد، فاصله، - / , .  و نیم‌فاصله (‌)
    """
    allowed_pattern = r'^[\u0600-\u06FF\u200C\s0-9\-/,.]+$'
    
    if not re.match(allowed_pattern, text):
        for char in text:
            if not re.match(r'[\u0600-\u06FF\u200C\s0-9\-/,.]', char):
                return False, f"کاراکتر غیرمجاز: «{char}»"
        return False, "کاراکتر غیرمجاز در متن"
    
    return True, None


def _normalize_commodity_phrase(text: str) -> str:
    """Normalize commodity text for exact phrase matching."""
    return ' '.join(text.replace('\u200c', ' ').split())


def _commodity_phrase_pattern(name: str) -> re.Pattern:
    normalized = _normalize_commodity_phrase(name)
    escaped_parts = [re.escape(part) for part in normalized.split()]
    phrase = r'\s+'.join(escaped_parts)
    return re.compile(rf'(?<![{COMMODITY_BOUNDARY_CHARS}]){phrase}(?![{COMMODITY_BOUNDARY_CHARS}])')


def _has_bahar_qualifier_conflict(text: str, match: re.Match, candidate_name: str) -> bool:
    candidate = _normalize_commodity_phrase(candidate_name)
    if candidate != "بهار":
        return False

    before = text[:match.start()].strip()
    if not before:
        return False

    previous_word = before.split()[-1]
    return previous_word in BAHAR_QUALIFIERS


def _match_commodity_name(text: str, name_to_commodity: dict) -> Tuple[Optional[int], str]:
    """Match the longest explicit commodity name/alias as a standalone phrase."""
    normalized_text = _normalize_commodity_phrase(text)
    names = sorted(
        (name for name in name_to_commodity.keys() if _normalize_commodity_phrase(name)),
        key=lambda item: len(_normalize_commodity_phrase(item)),
        reverse=True,
    )

    for name in names:
        pattern = _commodity_phrase_pattern(name)
        for match in pattern.finditer(normalized_text):
            if _has_bahar_qualifier_conflict(normalized_text, match, name):
                continue
            return name_to_commodity[name]

    return None, "نامشخص"


def _extract_residual_commodity_text(text: str) -> str:
    """Return the leftover commodity text after stripping offer structure."""
    residual = normalize_digits(text)
    residual = residual.replace("-", " ").replace("/", " ").replace(",", " ").replace(".", " ")
    residual = re.sub(r'\d+', ' ', residual)
    residual = re.sub(r'(?:تا|عدد)', ' ', residual)
    return _normalize_commodity_phrase(residual)


def _resolve_implicit_default_commodity(name_to_commodity: dict) -> Tuple[Optional[int], str]:
    for key in IMPLICIT_DEFAULT_COMMODITY_KEYS:
        commodity = name_to_commodity.get(key)
        if commodity is not None:
            return commodity
    return None, "نامشخص"


def extract_trade_type(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    استخراج نوع معامله (خ/ف/خرید/فروش)
    Returns: (trade_type, error_message)
    """
    import re
    
    # شمارش انواع نشانگرهای خرید
    kh_pattern = r'(?<![آ-ی])خ(?![آ-ی])'
    kharid_pattern = r'خرید'
    
    # شمارش انواع نشانگرهای فروش
    fa_pattern = r'(?<![آ-ی])ف(?![آ-ی])'
    foroush_pattern = r'فروش'
    
    kh_matches = len(re.findall(kh_pattern, text))
    kharid_matches = len(re.findall(kharid_pattern, text))
    fa_matches = len(re.findall(fa_pattern, text))
    foroush_matches = len(re.findall(foroush_pattern, text))
    
    buy_count = kh_matches + kharid_matches
    sell_count = fa_matches + foroush_matches
    
    total = buy_count + sell_count
    
    if total == 0:
        return None, None  # این پیام لفظ نیست
    
    if total > 1:
        if buy_count > 1:
            return None, "❌ چندین نشانگر خرید در لفظ وجود دارد"
        if sell_count > 1:
            return None, "❌ چندین نشانگر فروش در لفظ وجود دارد"
        return None, "❌ هم نشانگر خرید و هم فروش در لفظ وجود دارد"
    
    if buy_count == 1:
        return "buy", None
    return "sell", None


def extract_quantity(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    استخراج تعداد (عدد + تا/عدد)
    Returns: (quantity, error_message)
    """
    pattern = r'(\d+)\s*(?:تا|عدد)'
    matches = re.findall(pattern, text)
    
    if not matches:
        return None, "❌ تعداد کالا یافت نشد. فرمت صحیح: 30تا یا 30 عدد"
    
    if len(matches) > 1:
        return None, "❌ چندین تعداد در لفظ وجود دارد"
    
    return int(matches[0]), None


def extract_price(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    استخراج قیمت (عدد 5 یا 6 رقمی)
    Returns: (price, error_message)
    """
    all_numbers = re.findall(r'\d+', text)
    price_candidates = [n for n in all_numbers if validate_price(n)[0]]
    
    if not price_candidates:
        return None, "❌ قیمت یافت نشد (باید عدد 5 یا 6 رقمی باشد)"
    
    if len(price_candidates) > 1:
        return None, "❌ چندین قیمت در لفظ وجود دارد (فقط یک عدد 5 یا 6 رقمی مجاز است)"
    
    return int(price_candidates[0]), None


def extract_lot_sizes(text: str, quantity: int, price: int) -> Tuple[Optional[List[int]], bool, Optional[str]]:
    """
    استخراج ترکیب خُرد (اعداد 1-2 رقمی غیر از تعداد)
    Returns: (lot_sizes, is_wholesale, error_message)
    """
    ts = get_trading_settings()
    
    all_numbers = re.findall(r'\d+', text)
    quantity_str = str(quantity)
    
    lot_candidates = []
    quantity_found = False
    
    for n in all_numbers:
        if len(n) in [5, 6]:
            continue
        
        if n == quantity_str and not quantity_found:
            quantity_found = True
            continue
        
        if len(n) in [1, 2]:
            lot_candidates.append(int(n))
    
    if not lot_candidates:
        return None, True, None  # یکجا
    
    if len(lot_candidates) > ts.lot_max_count:
        return None, False, f"❌ حداکثر {ts.lot_max_count} بخش مجاز است (تعداد فعلی: {len(lot_candidates)})"
    
    for lot in lot_candidates:
        if lot < ts.lot_min_size:
            return None, False, f"❌ هر بخش باید حداقل {ts.lot_min_size} عدد باشد (بخش نامعتبر: {lot})"
    
    if sum(lot_candidates) != quantity:
        return None, False, f"❌ جمع بخش‌ها ({sum(lot_candidates)}) با تعداد کل ({quantity}) برابر نیست"
    
    return lot_candidates, False, None


async def find_commodity(text: str) -> Tuple[Optional[int], str]:
    """
    پیدا کردن کالا از متن
    Returns: (commodity_id, commodity_name)
    """
    from bot.utils.redis_helpers import get_cached_commodities, set_cached_commodities
    
    # تلاش برای خواندن از cache
    cached = await get_cached_commodities()
    
    if cached:
        # استفاده از cache
        name_to_commodity = {item["name"]: (item["id"], item["name"]) for item in cached}
        for item in cached:
            for alias in item.get("aliases", []):
                # alias ممکن است string یا dict باشد (بسته به منبع cache)
                alias_str = alias["alias"] if isinstance(alias, dict) else alias
                name_to_commodity[alias_str] = (item["id"], item["name"])
        
        commodities_list = cached
    else:
        # خواندن از دیتابیس و cache کردن
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Commodity))
            commodities = result.scalars().all()
            
            result = await session.execute(select(CommodityAlias))
            aliases = result.scalars().all()
            
            # ساخت لیست برای cache
            commodities_list = []
            for c in commodities:
                item = {"id": c.id, "name": c.name, "aliases": []}
                for a in aliases:
                    if a.commodity_id == c.id:
                        item["aliases"].append({"id": a.id, "alias": a.alias, "commodity_id": a.commodity_id})
                commodities_list.append(item)
            
            # ذخیره در cache (5 دقیقه)
            await set_cached_commodities(commodities_list, ttl=300)
            
            # ساخت دیکشنری
            name_to_commodity = {item["name"]: (item["id"], item["name"]) for item in commodities_list}
            for item in commodities_list:
                for alias in item.get("aliases", []):
                    alias_str = alias["alias"] if isinstance(alias, dict) else alias
                    name_to_commodity[alias_str] = (item["id"], item["name"])
    
    # جستجو در متن (اولویت با نام/نام مستعار بلندتر و فقط به صورت عبارت مستقل)
    commodity_id, commodity_name = _match_commodity_name(text, name_to_commodity)
    if commodity_id is not None:
        return commodity_id, commodity_name

    residual_commodity_text = _extract_residual_commodity_text(text)
    if not residual_commodity_text:
        implicit_commodity_id, implicit_commodity_name = _resolve_implicit_default_commodity(name_to_commodity)
        if implicit_commodity_id is not None:
            return implicit_commodity_id, implicit_commodity_name

    return None, "نامشخص"


async def parse_offer_text(text: str) -> Tuple[Optional[ParsedOffer], Optional[ParseError]]:
    """
    پارس کامل متن لفظ
    Returns: (ParsedOffer, ParseError)
    """
    # جدا کردن توضیحات
    notes = None
    offer_text = text
    
    if ':' in text:
        parts = text.split(':', 1)
        offer_text = parts[0].strip()
        notes = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        
        if notes and len(notes) > 200:
            return None, ParseError("❌ توضیحات نباید بیش از 200 کاراکتر باشد")
    
    # نرمال‌سازی اعداد
    offer_text = normalize_digits(offer_text)
    
    # اول چک کن آیا خ/ف دارد - اگر نداشت، این پیام لفظ نیست
    trade_type, error = extract_trade_type(offer_text)
    if trade_type is None and error is None:
        return None, None  # این پیام لفظ نیست (مثل دکمه‌های کیبورد)
    
    # فقط اگر خ/ف داشت، بررسی کاراکترهای مجاز کن
    valid, char_error = validate_characters(offer_text)
    if not valid:
        return None, ParseError(char_error)
    
    # اگر خطای خ/ف بود، برگردان
    if error:
        return None, ParseError(error)
    
    # حذف نشانگرهای معامله از متن
    import re
    clean_text = re.sub(r'(?<![آ-ی])خ(?![آ-ی])', ' ', offer_text)
    clean_text = re.sub(r'(?<![آ-ی])ف(?![آ-ی])', ' ', clean_text)
    clean_text = clean_text.replace('خرید', ' ').replace('فروش', ' ')
    clean_text = ' '.join(clean_text.split())  # حذف فاصله‌های اضافی
    
    # استخراج تعداد
    quantity, error = extract_quantity(clean_text)
    if error:
        return None, ParseError(error)
    
    # اعتبارسنجی تعداد
    ts = get_trading_settings()
    if quantity < ts.offer_min_quantity:
        return None, ParseError(f"❌ حداقل تعداد باید {ts.offer_min_quantity} باشد")
    if quantity > ts.offer_max_quantity:
        return None, ParseError(f"❌ حداکثر تعداد می‌تواند {ts.offer_max_quantity} باشد")
    
    # استخراج قیمت
    price, error = extract_price(clean_text)
    if error:
        return None, ParseError(error)
    
    # استخراج ترکیب خُرد
    lot_sizes, is_wholesale, error = extract_lot_sizes(clean_text, quantity, price)
    if error:
        return None, ParseError(error)
    
    # پیدا کردن کالا
    commodity_id, commodity_name = await find_commodity(clean_text)
    if commodity_id is None:
        return None, ParseError("❌ کالا یافت نشد")
    
    return ParsedOffer(
        trade_type=trade_type,
        commodity_id=commodity_id,
        commodity_name=commodity_name,
        quantity=quantity,
        price=price,
        is_wholesale=is_wholesale,
        lot_sizes=lot_sizes,
        notes=notes
    ), None
