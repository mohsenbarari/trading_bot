# Stage 8 — پیش‌نویس رهگیری پذیرش و عرضهٔ تیمی

بستهٔ mutable برای رهگیری پذیرش و مدل عرضه است. این پوشه نه freeze محصول است، نه
گواه اجرای ماتریس کامل، و نه مجوز production.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- access-policy snapshot: `8eccdd2177ea5e2b21710b3a8863eace40092c35`
- correction status: این snapshot فقط sourceهای تغییرنکردهٔ router/guard/role را bind می‌کند؛ اصلاح shared-dependency در `82cb016e` ثبت شده و اصلاح ماتریس در commit مستندات همین بسته قرار می‌گیرد، اما هیچ‌کدام binding پذیرش کامل نیستند
- authority: `stage8CompleteAuthority=false`
- matrix status: `traceability-draft-expected-access-not-executed-acceptance`
- expected-access coverage: ۳۰ مسیر × ۹ پروفایل دقیق = ۲۷۰ outcome صریح
- full matrix execution: صفر سلول؛ viewport/state/interaction/environment هنوز فقط requirement هستند
- owner aesthetic acceptance: انجام نشده
- merge: انجام نشده
- production/staging/Sites: انجام نشده

## اصلاح مدل دسترسی

نسخهٔ قبلی `customer`، `accountant` و `group-lead` را کنار نقش‌ها قرار می‌داد، درحالی‌که
guard فعلی علاوه بر نقش از `account_status`، `is_customer` و `is_accountant` استفاده
می‌کند. نسخهٔ ۲ برای هر مسیر و هر access profile نتیجهٔ صریح `render`، redirect به login،
redirect canonical یا forbidden recovery دارد و به source واقعی router/guard متصل است.

نقش‌های پایدارشده در مدل `UserRole` عبارت‌اند از `تماشا`، `عادی`، `پلیس`، `مدیر میانی`
و `مدیر ارشد`. مشتری، حسابدار و مالک/سرگروه context رابطه‌ای هستند، نه نقش جدید.

این outcomeها انتظار ایستای normal-case هستند؛ authorization داخل component یا API،
هویت شیء پارامتری، حالت inactive/unavailable و پذیرش بصری باید جداگانه اجرا و evidence-bound
شوند. هیچ cross-product ساختگی یا ادعای ۵۱٬۹۶۸ سلول در این بسته وجود ندارد.

## محتوا

- [ACCEPTANCE_MATRIX.json](ACCEPTANCE_MATRIX.json)
- [VISUAL_FREEZE_PROTECTED_SURFACES.json](VISUAL_FREEZE_PROTECTED_SURFACES.json)
- [ROLLOUT_PLAN.md](ROLLOUT_PLAN.md)
- [VALIDATION.md](VALIDATION.md)
