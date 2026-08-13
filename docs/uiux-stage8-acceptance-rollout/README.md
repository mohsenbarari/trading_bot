# Stage 8 — پیش‌نویس رهگیری پذیرش و عرضهٔ تیمی

بستهٔ mutable برای رهگیری پذیرش و مدل عرضه است. این پوشه نه freeze محصول است، نه
گواه اجرای ماتریس کامل، و نه مجوز production.

## وضعیت

- branch: `condidate/webapp-ui-ux-redesign-v2`
- access-policy snapshot: `8eccdd2177ea5e2b21710b3a8863eace40092c35`
- component canonicalization snapshot: `7588d9c20b995244197d8de09392dd6a5f61b195`
- historical bounded visual-recovery source: `4415b7431a6b67965d24c44f6f9f0e59e48ed422` (subsequent local P1 validation pending)
- authority: `stage8CompleteAuthority=false`
- matrix status: `partial-browser-slice-executed-full-acceptance-pending`
- expected-access coverage: ۳۰ مسیر × ۹ پروفایل دقیق = ۲۷۰ outcome صریح
- full matrix execution: صفر سلول؛ viewport/state/interaction/environment هنوز فقط requirement هستند
- partial synthetic evidence: یک slice دسترسی/shell با ۴۸/۴۸ scenario cell و یک slice محدود directory/profile ثبت شده‌اند؛ هیچ‌کدام به full matrix افزوده نمی‌شوند
- owner aesthetic acceptance: انجام نشده
- merge: انجام نشده
- production/staging/Sites: انجام نشده

## اصلاح مدل دسترسی

نسخهٔ قبلی `customer`، `accountant` و `group-lead` را کنار نقش‌ها قرار می‌داد، درحالی‌که
guard فعلی علاوه بر نقش از `account_status`، `is_customer` و `is_accountant` استفاده
می‌کند. نسخهٔ ۳ برای هر مسیر و هر access profile نتیجهٔ صریح router/guard دارد و به source
واقعی آن متصل است. چهار deep-link مجازِ router برای مدیر میانی (`/admin/channels`،
`/admin/commodities`، `/admin/messages` و `/admin/system`) جداگانه به outcome کامپوننتی
`AdminView → /admin` متصل شده‌اند؛ این redirect رکورد router یا forbidden recovery نیست.

نقش‌های پایدارشده در مدل `UserRole` عبارت‌اند از `تماشا`، `عادی`، `پلیس`، `مدیر میانی`
و `مدیر ارشد`. مشتری، حسابدار و مالک/سرگروه context رابطه‌ای هستند، نه نقش جدید.

این outcomeها انتظار ایستای normal-case هستند؛ authorization داخل component یا API،
هویت شیء پارامتری، حالت inactive/unavailable و پذیرش بصری باید جداگانه اجرا و evidence-bound
شوند. هیچ cross-product ساختگی یا ادعای ۵۱٬۹۶۸ سلول در این بسته وجود ندارد.

## شواهد محدود 8A

[STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json) تنها count، hash و
source-revisionهای redacted دو اجرای local/synthetic را نگه می‌دارد؛ screenshot، diagnostic،
trace یا مسیر محلی در repository ذخیره نشده است.

- slice دسترسی/shell در `390×844`: شش profile × هشت scenario، ۴۸/۴۸ cell و ۵۰ assertion؛
  این اجرا full matrix نیست.
- slice بازیابی directory/profile در source تاریخی `4415b743`: مسیرهای `/profile`، `/users/:id`،
  `/admin/users` و `/admin/users/:id` در viewportهای محدود بررسی شده‌اند؛ این اجرا full
  role×route acceptance نیست و validation تغییر محلی P1 پس از آن pending است.

مرجع Figma زنده و قابل‌ویرایش در file `z8jgJxST4O2APzWnlyP9gv`، page `486:1455`، section
`508:95` و frame `508:96` (`390×844`) ثبت شده است. audit محدود آن ۲۷ text با Vazirmatn،
۷ instance متصل UIUX، ۴۹ node token-bound، و صفر phone/email/URL/query ناایمن گزارش کرده است.
این مرجع نه screenshot/hash-freeze است، نه evidence اجرای runtime، و نه پذیرش نهایی؛ تا validation
P1، منبع تاریخی `4415b743` را نشان می‌دهد و ادعایی دربارهٔ working tree جاری ندارد.

## محتوا

- [ACCEPTANCE_MATRIX.json](ACCEPTANCE_MATRIX.json)
- [STAGE8A_EXECUTION_RECEIPTS.json](STAGE8A_EXECUTION_RECEIPTS.json)
- [VISUAL_FREEZE_PROTECTED_SURFACES.json](VISUAL_FREEZE_PROTECTED_SURFACES.json)
- [ROLLOUT_PLAN.md](ROLLOUT_PLAN.md)
- [VALIDATION.md](VALIDATION.md)
