# حکم جاری Native App Feel V2

مخاطب: ناظر مستقل. فقط همین بخش authoritative است.
Checkpointهای قدیمی فقط در بخش `HISTORICAL / SUPERSEDED` همین پرونده‌اند و وضعیت جاری نیستند.

تاریخ شواهد: ۲۰۲۶-۰۸-۲۷
شاخه: فقط `candidate/webapp-native-app-v2`

## حکم جاری

`READY FOR INDEPENDENT INTEGRATION REVIEW`

حکم ممنوع صادر نشد: owner-approved و production-ready.

`vue-tsc` سبز اعلام نمی‌شود. اجرای کامل EXIT 2 است و فقط چهار خطای منجمد Market مجاز است.

فازهای ۱ تا ۱۰ همچنان partial هستند. این حکم پذیرش harness و شواهد همین دور است، نه پایان track.

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
| merge-base با `origin/main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله با `origin/main` هنگام نگارش | ۵۹ جلو / ۲۵ عقب |
| `git diff --name-only $(git merge-base HEAD origin/main) origin/main -- frontend` | خالی |
| تصمیم merge | انجام نشد |
| پوش / deploy / Figma / Sites | انجام نشد |
| `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |

پس از commit همین پرونده، HEAD و درخت را دوباره از Git بخوان.

---

## شمار اجرا — نه annotation

جمع هر ردیف با Playwright همان مرورگر یکی است. سلول N/A ماتریس، تست جدا نیست.

| دسته | مرورگر | executed | passed | N/A رسمی | skipped | failed | environmental diagnostics |
|---|---|---:|---:|---:|---:|---:|---:|
| پوشش ۲۹ مسیر / دلیل N/A | Chromium | ۱ | ۱ | ۰ | ۰ | ۰ | ۰ |
| هندسه `initial/normal` | Chromium | ۲۹۰ | ۲۹۰ | ۰ سلول تست | ۰ | ۰ | ۰ |
| ماتریس state | Chromium | ۲۹۶ | ۲۹۶ | ۴۰۰ سلول توصیف‌گر در ۲ عرض | ۰ | ۰ | ۰ |
| مکمل خانوادهٔ حساس | Chromium | ۰ | ۰ | ۰ | ۴۴ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | Firefox | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | ۰ |
| خانوادهٔ حساس `normal` | WebKit | ۴۴ | ۴۴ | ۰ | ۰ | ۰ | console ۴۴ |
| صفحه‌کلید / sheet / کپی عملیات | Chromium | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی عملیات | Firefox | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
| صفحه‌کلید / sheet / کپی عملیات | WebKit | ۱۳ | ۱۳ | ۰ | ۰ | ۰ | ۰ |
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

جمع Chromium همین سه spec: ۶۲۶ passed + ۴۴ skipped = ۶۷۰.
جمع Firefox خانواده‌های حساس و قراردادهای اندازه‌گیری‌شده: ۸۴ passed.
جمع WebKit همان خانواده: ۸۴ passed + ۵۸۶ skipped سلول Chromium-only.

یکتای مسیر+حالت N/A: ۲۰۰. در دو عرض حساس: ۴۰۰ سلول توصیف‌گر.

کدهای N/A در ۲ عرض حساس:

| naCode | تعداد | دلیل کوتاه |
|---|---:|---|
| `hub-always-populated` | ۷۲ | هاب همیشه ردیف دارد |
| `no-ltr-token` | ۵۲ | شناسهٔ LTR محصولی ندارد |
| `no-stale-resource` | ۵۲ | منبع stale/refresh ندارد |
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
| Vitest کامل `--maxWorkers=1` | ۱۶۹ پرونده / ۱۹۸۶ تست passed |
| ChatView جدا `--maxWorkers=1` | ۱۰۹ passed |
| `vue-tsc -p tsconfig.app.json --noEmit` | EXIT 2؛ فقط `MarketView.vue:825 TS2345`، `961 TS18048`، `962 TS18048`، `1271 TS2322` |
| بیلد موقت | `/tmp/native-app-v2-prod-build-honesty`؛ ۱۷۰ فایل؛ ۵٬۷۲۵٬۲۵۵ بایت؛ digest `83bb4fe09e0ab71a80bbc6612512ec995348c6520462822d5c2c17672a316f0c` |
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

مجموعهٔ تازه پس از `waitForVisualStability`: `/tmp/native-app-v2-visual-honesty`
۱۴۵ PNG؛ ۲۹ مسیر × ۵ عرض؛ ۴٬۳۸۹٬۷۹۲ بایت؛ digest `8af07491da8e994404fbf948d34c9c6a654641921497b14a531c1a743ba87797`.
PNG داخل Git نیست.

ایرادهای بصری پیدا و اصلاح‌شده:

- نام حساب چندخطی و last-visit با `position:absolute` به مرز هدر و کارت بعدی برخورد می‌کرد
- سلسله‌مراتب هدر پروفایل نامتعادل بود
- CreateChannel کارت تو در کارت و سطح دوم / gutter دوبل داشت

---

## باقی‌ماندهٔ کوچک

- چهار خطای منجمد `vue-tsc` در Market؛ خارج از این track
- فازهای ۱ تا ۱۰ partial؛ کاتالوگ V2 هنوز `--ui-v2-*` دارد
- قاعدهٔ مردهٔ `.channel-admin-header-btn` با رنگ `#334155` در CSS کانال هنوز در فایل است و در قالب استفاده نمی‌شود
- Vitest موازی ممکن است ChatView را flake کند؛ با یک worker سبز است
- مسیر fixture پروفایل عمومی همان کاربر جاری است؛ نمای کاربر دیگر همان `ProfileIdentityHeader` را دارد
- ۴۴ پیام محیطی WebKit فقط کلید viewport ناشناخته است

---

## HISTORICAL / SUPERSEDED

متن‌های زیر وضعیت جاری نیستند.

- Checkpointهای `4c5e8136` / `5f77a02a` / `da0dc34a` / `aaec89a7` / `5f9018e1` و حکم READY قبلی **منسوخ**اند.
- fail-closed = ۹ **منسوخ** است؛ شمار جاری ۱۰ است.
- overlay interaction = ۷ در حالی که inventory داخل آن بود **منسوخ** است؛ inventory جدا است.
- state executed ۳۰۶ و N/A ۳۹۰ **منسوخ** است؛ شمار جاری ۲۹۶ executed و ۴۰۰ N/A در ۲ عرض است.
- digest تصویر `1bd0a213…` **منسوخ** است.
- environmental شمردن تمام abortهای GET/HEAD، dynamic import، و هر متن حاوی `/api/realtime/ws` **منسوخ** است.
- fixture ۴۲۵ به‌عنوان خطای فهرست **منسوخ** است.
- ادعای ناقص بودن inset کانال **منسوخ** است؛ کارت تو در کارت برداشته شد.
- `vue-tsc` هیچ‌گاه در این track سبز اعلام نشود.
