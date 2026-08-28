# حکم جاری Native App Feel V2

مخاطب: ناظر مستقل. فقط همین بخش authoritative است.
Checkpointهای قدیمی فقط در بخش `HISTORICAL / SUPERSEDED` همین پرونده‌اند و وضعیت جاری نیستند.

تاریخ شواهد: ۲۰۲۶-۰۸-۲۸
شاخه: فقط `candidate/webapp-native-app-v2`

## حکم جاری

`READY FOR MAIN INTEGRATION`

حکم ممنوع صادر نشد: owner-approved و production-ready.

ممیزی مستقل کد، تجربهٔ زنده، شواهد مرورگر و گیت‌های محصول انجام شد. فازهای ۰ تا ۱۱
در محدودهٔ ۲۹ مسیر غیر Market بسته‌اند. Market و interiorهای مالک‌قفل‌شده خارج از این track
بودند و بایت‌به‌بایت یا با گارد تاریخی ثابت ماندند.

چهار خطای `vue-tsc` فقط با فرمان پروژه‌ای `-p tsconfig.app.json` و در Market دیده می‌شوند؛
فرمان عمومی `npx vue-tsc --noEmit` سبز است. هیچ‌کدام از این چهار خطا محصول این شاخه نیست.

Annotation یا early-return داخل pass شمرده نشد.

---

## Binding

| مورد | مقدار |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD شروع مأموریت | `5f9018e105eb72ac9ea534338cd99edac061fda4` |
| درخت شروع | `544a45a3c188a97776afdb330ad6d6feebb8e182` |
| commit harness همین دور | `2a48a53a27378a2865ab7a552239ec665c2ae1a4` |
| درخت harness | `f232d2f48a4ca76aae2d63edea126193bbcf7533` |
| commit محصولی همین دور | `8668a9bd10ce7c57e81fb99b219eeb476a854889` |
| درخت محصولی | `f0edc11c0b2e44890a0ceb32832cdfbbd5e1f9c1` |
| commit ممیزی نهایی محصول و harness | `876e8eb07fa72c4c0272cab9fba1df14b97e0b87` |
| درخت ممیزی نهایی | `70b7968786240fc31d6d510e78e9bd351b3fa49d` |
| merge-base با `origin/main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله با `origin/main` پس از commit محصولی نهایی | ۶۱ جلو / ۲۶ عقب |
| `git diff --name-only $(git merge-base HEAD origin/main) origin/main -- frontend` | خالی |
| تصمیم merge | پس از commit نهایی و بازاجرای گیت یکپارچه مجاز است |
| Figma / Sites | انجام نشد |
| `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |

commit اسناد عمداً پس از commit محصولی قرار می‌گیرد؛ برای ادغام، HEAD و درخت نهایی را
مستقیماً از Git بخوان.

---

## شمار اجرا — نه annotation

جمع هر ردیف با Playwright همان مرورگر یکی است. سلول N/A ماتریس، تست جدا نیست.

| دسته | مرورگر | executed | passed | N/A رسمی | skipped | failed | environmental diagnostics |
|---|---|---:|---:|---:|---:|---:|---:|
| پوشش ۲۹ مسیر / دلیل N/A | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| هندسه `initial/normal` | Chromium | ۲۹۰ | ۲۹۰ | ۰ سلول تست | ۰ | ۰ | ۰ |
| ماتریس state | Chromium | ۲۹۴ | ۲۹۴ | ۴۰۲ سلول توصیف‌گر در ۲ عرض | ۰ | ۰ | ۰ |
| مکمل خانوادهٔ حساس | Chromium | ۰ | ۰ | ۰ | ۴۴ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | Firefox | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | WebKit | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | console ۴۴ |
| صفحه‌کلید / sheet / کپی و پروفایل همتا | Chromium | ۱۴ | ۱۴ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی و پروفایل همتا | Firefox | ۱۴ | ۱۴ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی و پروفایل همتا | WebKit | ۱۴ | ۱۴ | ۰ | ۰ | ۰ | ۰ |
| safe-area:login | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area:login | Firefox | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area:login | WebKit | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area:messenger | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area:messenger | Firefox | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| safe-area:messenger | WebKit | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | Chromium | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | Firefox | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| زوم ۲۰۰٪ اندازه‌گیری‌شده | WebKit | ۷ | ۷ | ۰ | ۰ | ۰ | ۰ |
| overlay inventory | Chromium | ۱ | ۱ | موجودی است نه تعامل | ۰ | ۰ | ۰ |
| overlay inventory | Firefox | ۱ | ۱ | موجودی است نه تعامل | ۰ | ۰ | ۰ |
| overlay inventory | WebKit | ۱ | ۱ | موجودی است نه تعامل | ۰ | ۰ | ۰ |
| overlay interaction | Chromium | ۶ | ۶ | ۰ | ۰ | ۰ | ۰ |
| overlay interaction | Firefox | ۶ | ۶ | ۰ | ۰ | ۰ | ۰ |
| overlay interaction | WebKit | ۶ | ۶ | ۰ | ۰ | ۰ | ۰ |
| fail-closed harness | Chromium | ۱۰ | ۱۰ | ۰ | ۰ | ۰ | ۰ |
| fail-closed harness | Firefox | ۱۰ | ۱۰ | ۰ | ۰ | ۰ | ۰ |
| fail-closed harness | WebKit | ۱۰ | ۱۰ | ۰ | ۰ | ۰ | ۰ |

جمع Chromium همین سه spec: ۶۲۵ passed + ۴۴ skipped = ۶۶۹.
جمع Firefox خانواده‌های حساس و قراردادهای اندازه‌گیری‌شده: ۸۵ سلول؛ یک اجرای طولانی
پس از ۲۳ صفحهٔ زنده با توقف پردازش مرورگر یک timeout محیطی داشت و همان سلول
`customers:desktop-1440:normal` در اجرای مستقل بلافاصله pass شد. این رخداد به‌عنوان
«اجرای کامل یک‌بارهٔ سبز» معرفی نمی‌شود.
جمع WebKit همان خانواده: ۸۵ passed + ۵۸۴ skipped در یک اجرای کامل.

یکتای مسیر+حالت N/A: ۲۰۱. در دو عرض حساس: ۴۰۲ سلول توصیف‌گر.

کدهای N/A در ۲ عرض حساس:

| naCode | تعداد | دلیل کوتاه |
|---|---:|---|
| `hub-always-populated` | ۷۲ | هاب همیشه ردیف دارد |
| `no-ltr-token` | ۵۲ | شناسهٔ LTR محصولی ندارد |
| `no-stale-resource` | ۵۴ | منبع stale/refresh ندارد؛ پروفایل عمومی همتا عمداً وضعیت عضویت داخلی ندارد |
| `no-unbroken-token` | ۵۲ | شناسهٔ بی‌فاصله ندارد |
| `no-page-load-resource` | ۳۴ | بار صفحه به فهرست شبکه وابسته نیست؛ شامل آفلاین فرم‌های ورود/ثبت/دعوت |
| `no-long-persian-copy` | ۲۸ | متن بلند فارسی محصولی ندارد |
| `no-full-collection` | ۲۴ | فهرست پر متمایز ندارد |
| `not-authorization-gated` | ۲۴ | برای کاربر نشست‌دار ممنوع نیست |
| `no-collection-surface` | ۲۰ | فرم است نه فهرست |
| `local-recovery` | ۱۴ | بازیابی محلی است |
| `share-target-empty-is-normal` | ۱۴ | بدون payload همان حالت عادی است |
| `public-route` | ۱۰ | مسیر عمومی مقصد ورود است |
| `terminal-http-has-no-retry` | ۴ | خطای پایانی ۴۲۲ دکمهٔ تلاش مجدد ندارد |

---

## تفکیک error / retry / offline / 5xx

| حالت ماتریس | معنای واقعی |
|---|---|
| `error` | HTTP پایانی قراردادی **۴۲۲** با `{ detail: 'دریافت فهرست ناموفق بود' }` و بنر خطا |
| `retry` | همان ۴۲۲ چسبان تا کلیک «تلاش مجدد\|تلاش دوباره\|بررسی دوباره» سپس `clearFail` |
| `register` / `invite` retry | N/A `terminal-http-has-no-retry` — ۴۲۲ در قرارداد آن مسیرها پایانی است |
| `offline` فهرست | abort فقط GET/HEAD غیر keepalive؛ mutation غیرمجاز ۴۰۵ و ثبت در `unexpectedMutations` |
| `offline` فرم ورود/ثبت/دعوت | N/A `no-page-load-resource` |
| 5xx / قطع شبکهٔ گذرا | سلول ماتریس نیست. `apiFetch` با `retryNetwork` پیش‌فرض برای 5xx حلقهٔ reconnect دارد. بنر خطا برای 5xx pass اعلام نشد و رفتار تولیدی عوض نشد |

**۴۲۵:** برای این فهرست‌ها و احراز قراردادی نیست. در Register، ۴۲۵ retryable است. fixture دیگر ۴۲۵ نیست.

---

## تشخیص محیطی — تعداد دقیق

| مرورگر | نوع | متن یا مسیر | تعداد |
|---|---|---|---:|
| Chromium | `environmentalConsole` | — | ۰ |
| Chromium | `environmentalPageErrors` | — | ۰ |
| Chromium | `environmentalRequestFailed` | — | ۰ |
| Firefox | هر سه نوع | — | ۰ |
| WebKit | `environmentalConsole` | `Viewport argument key "interactive-widget" not recognized and ignored.` | ۴۴ |
| WebKit | `environmentalPageErrors` | — | ۰ |
| WebKit | `environmentalRequestFailed` | — | ۰ |

Abort فقط وقتی environmental است که ناوبری واقعی و کنترل‌شده باشد، یا GET/HEAD keepalive دقیق (`/api/auth/me`، `/api/sessions/verify`، `/api/auth/refresh`). GET معمولی abortشده خطا است. شکست dynamic import / chunk پیش‌فرض تست را قرمز می‌کند.

سوکت در harness با `page.routeWebSocket(/\/api\/realtime\/ws(?:\?|$)/)` mock شد؛ endpoint دقیق `/api/realtime/ws`. در Chromium و Firefox و WebKit خطای سوکت محصولی دیده نشد؛ allowlist عمومی برای متن حاوی `/api/realtime/ws` وجود ندارد.

---

## fail-closed آفلاین

۱۰ تست، نه ۹.

اجازهٔ کلی `/api/*` حذف شد. allowlist آفلاین فقط GET/HEAD همان `offlineGetPaths` توصیف‌گر است.

اثبات mutation پنهان: تست `offline classifies mutations before abort and records unexpected writes` برای POST/PATCH/DELETE غیرمجاز پاسخ ۴۰۵، ثبت در `unexpectedMutations`، و شکست `expectCleanDiagnostics` را ثابت کرد. GET مورد انتظار توصیف‌گر مجاز است؛ GET نامرتبط و شکست asset مجاز نیست.

---

## گیت‌های غیر مرورگر

| گیت | نتیجه |
|---|---|
| `git diff --check` | پاک |
| Vitest متمرکز | ProfilePresentation ۳، CreateChannel ۱۰، AdminView ۴۰، Stage4 ۳۳، PublicProfile ۵۸ — همه passed |
| Vitest کامل `--maxWorkers=1` | ۱۶۹ پرونده / ۱۹۸۸ تست passed |
| ChatView جدا `--maxWorkers=1` | ۱۰۹ passed |
| `vue-tsc -p tsconfig.app.json --noEmit` | EXIT 2؛ فقط `MarketView.vue:825 TS2345`، `961 TS18048`، `962 TS18048`، `1271 TS2322` |
| بیلد موقت نهایی | `/tmp/native-app-v2-final-dist-v3`؛ ۱۷۰ فایل؛ ۵٬۷۲۵٬۲۴۱ بایت؛ digest `7589b2c2edcb215a8ebeeb75f7af2bcd7393373a191403bbe8210918f84f256e` |
| `npm run guard:ui` | PASS |
| `memory-custodian check` | پس از commit اسناد |

تشخیص اجباری روی سلول‌های executed: unknown API = 0، unexpected mutation = 0، external request = 0، unexpected console = 0، unexpected request failure = 0، page error = 0.

---

## گارد منجمد

| سطح | مقدار |
|---|---|
| Stage 4 baseline AdminMessages | `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a` بازنویسی نشد |
| visual AdminMessages | `aeb33b605830cedd6cdd6e93b70cd973252d5e0ab27b555f2a21ebd62a8dfb38` از منبع نهایی |
| Stage 4 baseline TradingSettings | `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa` بازنویسی نشد |
| visual TradingSettings | `1d7831ebedd3e09f6ce8a21aedb8b9d7b35fc1135a5d27fa101cf25d2eedaf5f` از منبع نهایی |
| messenger visual | ۸۵ فایل، ۱٬۲۹۲٬۰۵۹ بایت، pathSet `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58`، sha `466798f7bd57a3b0874fc559677af7d21685981e5e08c2fd518e3aff5d298b1f` |
| MarketView / OffersList | byte-for-byte منجمد |

---

## تصویر

مجموعهٔ قبلی `/tmp/native-app-v2-visual-correction` digest `1bd0a21399c69a6d363e870f59e5248f8f6023cd57e90acb5057b24b57857199` **منسوخ** است؛ در چند شات normal، محتوا و loader نیمه‌شفاف با هم دیده می‌شد.

مجموعهٔ نهایی پس از دو دور `waitForVisualStability` و کنترل scroll اولیه:
`/tmp/native-app-v2-final-visual-v3-20260828`، ۱۴۵ PNG؛ ۲۹ مسیر × ۵ عرض؛
۴٬۲۰۹٬۲۵۶ بایت؛ digest `ae27c762190cc6a3b9545e6e5dcf9f0be227e60c115063a84b57a5259141f5f3`.
PNG داخل Git نیست.

ایرادهای بصری پیدا و اصلاح‌شده:

- نام حساب چندخطی و last-visit با `position:absolute` به مرز هدر و کارت بعدی برخورد می‌کرد
- سلسله‌مراتب هدر پروفایل نامتعادل بود
- CreateChannel کارت تو در کارت و سطح دوم / gutter دوبل داشت
- outcome دعوت با فوکوس برنامه‌ای یک focus ring تمام‌صفحه می‌کشید؛ specificity و
  `box-shadow` در قرارداد دقیق همان عنصر اصلاح و روی هر پنج عرض بازتصویربرداری شد

---

## استثناهای آگاهانه، نه مانع track

- چهار خطای فرمان پروژه‌ای `vue-tsc` در Market؛ خارج از این track و پیش‌موجود
- hookهای تاریخی `ui-v2-*` که تست‌ها و گاردها به آن‌ها متصل‌اند؛ زبان بصری دوم نیستند
- CSS منجمد کانال حتی در بخش‌های بدون مصرف تغییر نکرد، چون receipt تاریخی دقیقاً همان فایل را قفل می‌کند
- Vitest موازی ممکن است ChatView را flake کند؛ با یک worker سبز است
- fixture پروفایل عمومی اکنون viewer و target را مستقل می‌سازد و رؤیت تلفن/نشانی همتا را می‌آزماید
- ۴۴ پیام محیطی WebKit فقط کلید viewport ناشناخته است

---

## HISTORICAL / SUPERSEDED

متن‌های زیر وضعیت جاری نیستند.

- Checkpointهای `4c5e8136` / `5f77a02a` / `da0dc34a` / `aaec89a7` / `5f9018e1` و حکم READY قبلی **منسوخ**اند.
- fail-closed = ۹ **منسوخ** است؛ شمار جاری ۱۰ است.
- overlay interaction = ۷ در حالی که inventory داخل آن بود **منسوخ** است؛ inventory جدا است.
- state executed ۳۰۶/۲۹۶ و N/A ۳۹۰/۴۰۰ **منسوخ** است؛ شمار جاری ۲۹۴ executed و ۴۰۲ N/A در ۲ عرض است.
- digest تصویر `1bd0a213…` **منسوخ** است.
- environmental شمردن تمام abortهای GET/HEAD، dynamic import، و هر متن حاوی `/api/realtime/ws` **منسوخ** است.
- fixture ۴۲۵ به‌عنوان خطای فهرست **منسوخ** است.
- ادعای ناقص بودن inset کانال **منسوخ** است؛ کارت تو در کارت برداشته شد.
- سبز اعلام‌کردن `vue-tsc -p tsconfig.app.json` تا رفع چهار خطای خارج از scope Market ممنوع است؛ فرمان عمومی بدون project سبز است.
