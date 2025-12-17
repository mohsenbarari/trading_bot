# 📋 راهنمای ساختار پروژه

## ساختار فعلی (Legacy)

### bot/handlers/trade.py
فایل اصلی معاملات - **1470+ خط**

#### بخش‌بندی:
```
SECTION 1: UTILITY FUNCTIONS     (خط 40-220)
   - get_trade_type_keyboard()
   - get_lot_type_keyboard()
   - suggest_lot_combination()
   - validate_lot_sizes()
   - get_commodity_keyboard()
   - get_quantity_keyboard()
   - get_confirm_keyboard()
   - format_offer_preview()

SECTION 2: BUTTON FLOW HANDLERS  (خط 220-660)
   - handle_trade_button() - دکمه معامله
   - handle_trade_type() - نوع معامله
   - handle_commodity_page() - صفحه‌بندی کالا
   - handle_commodity_selection() - انتخاب کالا
   - handle_quantity_button() - دکمه تعداد
   - handle_quantity_input() - ورود تعداد
   - handle_lot_type() - نوع لات
   - handle_lot_sizes() - ورود لات‌ها
   - handle_price_input() - ورود قیمت
   - handle_notes() - توضیحات

SECTION 3: PREVIEW & CONFIRM     (خط 660-850)
   - handle_trade_confirm() - تایید و ارسال
   - handle_back_to_type() - برگشت
   - handle_trade_cancel() - انصراف

SECTION 4: OFFER MANAGEMENT      (خط 850-990)
   - handle_expire_offer() - منقضی کردن
   - _expire_rate_tracker - آمار
   - build_lot_buttons() - ساخت دکمه‌ها

SECTION 5: CHANNEL TRADE HANDLERS (خط 990-1190)
   - handle_channel_trade() - معامله از کانال
   - _pending_confirmations - دابل‌کلیک

SECTION 6: TEXT OFFER HANDLER    (خط 1190-1470)
   - _get_offer_suggestion() - پیشنهاد اصلاح
   - has_trade_indicator() - فیلتر متن
   - handle_text_offer() - پردازش لفظ متنی
   - handle_text_offer_confirm() - تایید
   - handle_text_offer_cancel() - انصراف
```

---

## ساختار جدید (Clean Architecture)

```
src/
├── core/                    # بدون وابستگی خارجی
│   ├── entities/            # UserEntity, OfferEntity
│   ├── schemas/             # Pydantic DTOs
│   ├── services/            # UserService, OfferService
│   ├── repositories/        # Interfaces
│   └── exceptions/          # DomainException
│
├── infrastructure/          # پیاده‌سازی
│   └── database/
│       └── repositories/    # SQLAlchemy implementations
│
└── interfaces/              # رابط‌ها
    ├── telegram_bot/        # هندلرهای تلگرام
    └── http_api/            # روترهای FastAPI
```

---

## مراحل مهاجرت (Migration Plan)

### فاز ۱ ✅ انجام شد
- [x] بخش‌بندی فایل با کامنت‌های واضح
- [x] ساختار src/ ایجاد شد
- [x] Entities و Schemas آماده

### فاز ۲ - بعدی
- [ ] انتقال UserService به هندلرها
- [ ] تست و اطمینان از عملکرد

### فاز ۳
- [ ] انتقال OfferService
- [ ] حذف کد تکراری

### فاز ۴
- [ ] شکستن trade.py به 6 فایل جداگانه
- [ ] تست کامل
