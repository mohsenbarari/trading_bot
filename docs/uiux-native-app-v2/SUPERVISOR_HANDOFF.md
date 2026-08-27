# حکم جاری Native App Feel V2

مخاطب: ناظر مستقل. فقط همین بخش authoritative است.
Checkpointهای قدیمی فقط در بخش `HISTORICAL / SUPERSEDED` همین پرونده‌اند و وضعیت جاری نیستند.

تاریخ شواهد: ۲۰۲۶-۰۸-۲۷
شاخه: فقط `candidate/webapp-native-app-v2`

## حکم جاری

`READY FOR INDEPENDENT INTEGRATION REVIEW`

حکم ممنوع صادر نشد: owner-approved و production-ready.

`vue-tsc` سبز اعلام نمی‌شود. اجرای کامل EXIT 2 است و فقط چهار خطای منجمد Market مجاز است.

Annotation یا early-return داخل pass شمرده نشد.

---

## Binding پس از commit اصلاح harness

| مورد | مقدار |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD harness | `9248a3f730aebe392b4c2e2ced3903c7394d57ce` |
| درخت harness | `ab07c96e797ae5bb86d5a8dd85fd2c3b3aad8d6b` |
| commit محصولی همین دور | `cc19f5d0` |
| merge-base با `origin/main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله با `origin/main` هنگام نگارش | ۵۶ جلو / ۲۱ عقب |
| `git diff --name-only $(git merge-base HEAD origin/main) origin/main -- frontend` | خالی |
| تصمیم merge | انجام نشد |
| پوش / deploy / Figma / Sites | انجام نشد |
| `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |

پس از commit همین پرونده، HEAD و درخت را دوباره از Git بخوان.

---

## شمار اجرا — نه annotation

| دسته | مرورگر | executed | passed | N/A رسمی | skipped | failed | harness-deferred |
|---|---|---:|---:|---:|---:|---:|---:|
| هندسه `initial/normal` | Chromium | ۲۹۰ | ۲۹۰ | ۰ سلول تست | ۰ | ۰ | ۰ |
| ماتریس state | Chromium | ۳۰۶ | ۳۰۶ | ۳۹۰ سلول توصیف‌گر در ۲ عرض | ۰ | ۰ | ۰ |
| مکمل خانوادهٔ حساس | Chromium | ۰ | ۰ | ۰ | ۴۴ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | Firefox | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | WebKit | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی عملیات | Chromium | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی عملیات | Firefox | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی عملیات | WebKit | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
| safe-area اندازه‌گیری‌شده | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area اندازه‌گیری‌شده | Firefox | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area اندازه‌گیری‌شده | WebKit | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | Chromium | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | Firefox | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | WebKit | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| overlayهای applicable اجراشده | Chromium | ۷ | ۷ | موجودی Market جدا | ۰ | ۰ | ۰ |
| overlayهای applicable اجراشده | Firefox | ۷ | ۷ | موجودی Market جدا | ۰ | ۰ | ۰ |
| overlayهای applicable اجراشده | WebKit | ۷ | ۷ | موجودی Market جدا | ۰ | ۰ | ۰ |
| fail-closed harness | Chromium | ۹ | ۹ | ۰ | ۰ | ۰ | ۰ |
| پوشش ۲۹ مسیر / دلیل N/A | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |

جمع Chromium همین سه spec: ۶۳۳ passed + ۴۴ skipped مکمل Firefox/WebKit.
جمع Firefox/WebKit خانواده‌های حساس و قراردادهای اندازه‌گیری‌شده: ۷۲ + ۷۲ passed؛ skipped زوم صفر چون `css-zoom` واقعاً اعمال و اندازه‌گیری شد.

N/A ماتریس state سلول تست جدا نیست؛ فقط سلول applicable اجرا شد. یکتای مسیر+حالت N/A: ۱۹۵. در دو عرض حساس: ۳۹۰ سلول توصیف‌گر.

کدهای N/A:

| naCode | تعداد سلول در ۲ عرض حساس | دلیل کوتاه |
|---|---:|---|
| `hub-always-populated` | ۷۲ | هاب همیشه ردیف دارد |
| `no-ltr-token` | ۵۲ | شناسهٔ LTR محصولی ندارد |
| `no-stale-resource` | ۵۲ | منبع stale/refresh ندارد |
| `no-unbroken-token` | ۵۲ | شناسهٔ بی‌فاصله ندارد |
| `no-long-persian-copy` | ۲۸ | متن بلند فارسی محصولی ندارد |
| `no-page-load-resource` | ۲۸ | بار صفحه به فهرست شبکه وابسته نیست |
| `no-full-collection` | ۲۴ | فهرست پر متمایز ندارد |
| `not-authorization-gated` | ۲۴ | برای کاربر نشست‌دار ممنوع نیست |
| `no-collection-surface` | ۲۰ | فرم است نه فهرست |
| `local-recovery` | ۱۴ | بازیابی محلی است |
| `share-target-empty-is-normal` | ۱۴ | بدون payload همان حالت عادی است |
| `public-route` | ۱۰ | مسیر عمومی مقصد ورود است |

---

## گیت‌های غیر مرورگر

| گیت | نتیجه |
|---|---|
| `git diff --check` | پاک |
| Vitest متمرکز فایل‌های تغییرکرده | ۹۰ passed |
| Vitest کامل `--maxWorkers=1` | ۱۶۹ پرونده / ۱۹۸۶ تست passed |
| `vue-tsc -p tsconfig.app.json --noEmit` | EXIT 2؛ فقط `MarketView.vue:825 TS2345`، `961 TS18048`، `962 TS18048`، `1271 TS2322` |
| بیلد موقت | `/tmp/native-app-v2-prod-build-correction`؛ ۱۷۰ فایل؛ digest `ad80fed2ad7b957abe6189e01a10626742f2581ef16b6fd8227e665028f6187e` |
| `npm run guard:ui` | PASS |
| `memory-custodian check` | OK پس از به‌روزرسانی memory |

تشخیص اجباری روی سلول‌های executed: unknown API = 0، unexpected mutation = 0، external request = 0، unexpected console = 0، unexpected request failure = 0، page error = 0.

شکست‌های محیطی جدا شمارش می‌شوند: سوکت `/api/realtime/ws`، `WebSocket Error: Event` در Chromium، `WebSocket Error: JSHandle@object` در Firefox، abort نوع GET/HEAD هنگام ناوبری، و abort keepalive روی `/api/sessions/verify` و `/api/auth/me`. mutation غیر keepalive اگر abort شود تست را قرمز می‌کند.

---

## گارد منجمد

| سطح | مقدار |
|---|---|
| Stage 4 baseline AdminMessages | `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a` بازنویسی نشد |
| visual AdminMessages | `aeb33b605830cedd6cdd6e93b70cd973252d5e0ab27b555f2a21ebd62a8dfb38` از منبع نهایی |
| Stage 4 baseline TradingSettings | `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa` بازنویسی نشد |
| visual TradingSettings | `1d7831ebedd3e09f6ce8a21aedb8b9d7b35fc1135a5d27fa101cf25d2eedaf5f` از منبع نهایی |
| messenger visual | ۸۵ فایل، ۱٬۲۹۱٬۴۲۵ بایت، pathSet `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58`، sha `c953c15f5fc9d8ecd838d6f83a4af4e137f7af4e73f29f411a3e3e194f0db598` |
| MarketView / OffersList | byte-for-byte منجمد |

---

## رفتار شبکه و ثبت‌نام

`retryNetwork: false` از مسیر زندهٔ `fetchChatConversations`، بارگذاری کانال، سه GET پیام مدیریت، و helper تنظیمات حذف شد.
خطای Market در پیام مدیریت دیگر تاریخچهٔ چت را خراب نمی‌کند و یک شکست نتیجهٔ موفق خواهر را دور نمی‌اندازد.

`WebRegister` اتصال تلگرام را فقط در `step === 4` نشان می‌دهد. `stepForContext` فقط ۱|۲|۳ برمی‌گرداند؛ مرحلهٔ ۴ فقط پس از تکمیل با `can_connect_telegram`. تست واحد مراحل ۱ تا ۴ را ثابت کرد. این fall-through است نه تغییر workflow.

---

## تصویر

خارج مخزن: `/tmp/native-app-v2-visual-correction`
۱۴۵ PNG از بیلد نهایی؛ digest `1bd0a21399c69a6d363e870f59e5248f8f6023cd57e90acb5057b24b57857199`.
PNG داخل Git نیست.

---

## باقی‌ماندهٔ کوچک

- چهار خطای منجمد `vue-tsc` در Market؛ خارج از این track
- سه spec قدیمی غیر این harness ممکن است هنوز `json({})` داشته باشند؛ داخل ماتریس Native App V2 نیستند
- قاب CreateChannel هنوز کاملاً inset خالص نیست؛ flatten انجام شده
- Vitest موازی (`--maxWorkers=3`) دو تست ChatView را timeout/race کرد؛ همان پرونده با worker=1 و اجرای کامل تک‌کارگر سبز است

---

## HISTORICAL / SUPERSEDED

متن‌های زیر وضعیت جاری نیستند. پروندهٔ جدا برای تاریخچه در این پوشه gitignore است؛ بنابراین بایگانی فشرده همین‌جاست.

- Checkpointهای `4c5e8136` / `5f77a02a` / `da0dc34a` / `aaec89a7` و حکم READY قبلی **منسوخ**اند.
- ادعای جاری بودن `json({})` در harness Native App V2 **منسوخ** است؛ سه spec این track دیگر `json({})` ندارند.
- ادعای «کار باقی: fail-close و ماتریس و Vitest کامل» به‌عنوان وضعیت امروز **منسوخ** است.
- شمار state ۴۶۲/۴۶۲ بدون N/A جدا **منسوخ** است؛ شمار جاری executed/N/A در جدول بالا است.
- `vue-tsc` هیچ‌گاه در این track سبز اعلام نشود.
