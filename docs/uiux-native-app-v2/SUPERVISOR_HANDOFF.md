# گزارش اجرای اصلاح Native App Feel V2

مخاطب: ناظر مستقل. ادعای سبز بودن را فقط با Git، تست و مرورگر بسنج.

تاریخ: ۲۰۲۶-۰۸-۲۷
شاخه: فقط `candidate/webapp-native-app-v2`
حکم جاری: `BLOCKED — NATIVE APP V2 REMAINS IN CORRECTION`

گیت TypeScript این track تفاضلی است: `vue-tsc` کامل می‌تواند EXIT 2 بماند اگر و فقط اگر fingerprint خطاهای `MarketView.vue` دقیقاً برابر مبنای محافظت‌شده باشد و خطای تازه یا غیر Market صفر باشد. ادعا نشود که `vue-tsc` سبز است.

---

## Checkpoint ادامه — ۲۰۲۶-۰۸-۲۷ پس از هدف ۴۸px و settle

| مورد | مقدار مستقل |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD این checkpoint | `5f77a02a1d7d5db694b9bfc45c4a6cdeac3cf0b5` |
| درخت | `b2feb6611a0a85a9571831a2b30e9b81bc794551` |
| merge-base با `main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله | ۴۶ جلو / ۱۳ عقب |
| `main` / `origin/main` | `6a51231ea3d1a842420e520365ba0af614f75095` |
| `git diff --name-only HEAD...main -- frontend/` | خالی |
| تصمیم merge | انجام نشد؛ commitهای جدید main فقط estimator، گیرنده، docs است |
| `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |
| هندسه Chromium | ۲۹۰ سلول `initial/normal` در ۵ عرض، همه سبز با `--workers=1` |
| تصویر پس از settle | ۱۴۵ PNG در `/tmp/native-app-v2-visual-settle`، digest `fb180ca42f4c63cffd08871f7e4004f1097c3835dc719e270dfdb3ce0593c9d1` |

کار باقی برای حکم READY:

1. ماتریس کامل state (loading/error/retry/offline/…) و Firefox/WebKit خانواده‌های حساس
2. گیت‌های نهایی فقط روی HEAD پس از آخرین commit: vitest کامل، vue-tsc تفاضلی، build، guard، E2E کامل
3. اگر تصویر پس از settle هنوز overlay بارگذاری نشان داد، منبع غیر `.ui-loading-state` را اصلاح کن

---

## Checkpoint ادامهٔ اصلاح — ۲۰۲۶-۰۸-۲۷ ۱۶:۳۰

این نقطه برای ازسرگیری پس از compaction است. به گزارش قبلی اعتماد نکن؛ اعداد را از Git دوباره بخوان.

| مورد | مقدار مستقل در شروع این ادامه |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD | `da0dc34a444987e70ddbbf6fc3c3524a6f1f6ab5` |
| درخت | `8c17d7f056069b6922f31f0499a2c1f44d62f0c7` |
| worktree | پاک؛ staged خالی؛ `.mimosa/` غایب |
| merge-base با `main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله با `main` / `origin/main` | ۳۷ جلو / ۸ عقب |
| `main` و `origin/main` | `a44d8fb269af490551cd9bcabea258341e12a65c` |
| `git diff --name-only HEAD...main -- frontend/` | خالی |
| تصمیم merge | انجام نشد؛ هشت commit عقب‌مانده فقط estimator/market-data/docs است |
| `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |
| `git diff main -- MarketView.vue OffersList.vue` | خالی |

کار باقی در همین ادامه، به ترتیب اجباری:

1. fail-close سه spec با `json({})`
2. ماتریس سلول‌به‌سلول ۲۹ مسیر × viewport × state
3. قرارداد ۴۸×۴۸، scroller، main/h1، CTA، overflow
4. soft keyboard و safe-area واقعی
5. reduced-motion و zoom اندازه‌گیری‌شده
6. stateهای hold / retry / offline / stale / long
7. flatten CreateChannel
8. موجودی کامل overlay
9. ممیزی بصری روی بیلد تازه در `/tmp`
10. گیت‌های نهایی فقط روی HEAD پس از آخرین commit

---


---

## Binding پیش از این دور اصلاح

شروع این دور روی HEAD قبلی track بود، نه binding کهنهٔ ناظر `c6ddc5af`.

| مورد | مقدار |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD شروع | `64755d10fc3d3e03149114435a8a51c48ef1634a` |
| درخت شروع | `77208bc57f869b4f0ac420fe5df9fc420cdcb3e9` |
| merge-base با `main` | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| فاصله با local `main` در شروع | ۳۱ جلو / ۸ عقب |
| `main` مشاهده‌شده | `a44d8fb269af490551cd9bcabea258341e12a65c` |
| `origin/main` | `255f8f70589cf3781fbe7f24e1101d8a8f873dcc` |
| worktree شروع | کثیف از اصلاحات همین track؛ `.mimosa/` آنترک |

هشت commit عقب‌ماندهٔ `main` فقط estimator/market-data و سند است. merge-tree تعارض UI یا Market نداشت. برای جلوگیری از drift این track ادغام نشد.

---

## Binding پس از آخرین commit مستندات

مقادیر را پس از commit همین فایل با Git دوباره بخوان. جدول زیر پیش‌نویس لحظهٔ نگارش است و در commit پیام ثبت می‌شود.

| مورد | مقدار ثبت‌شده هنگام نگارش |
|---|---|
| HEAD محصولی پیش از docs | `aaec89a7b260b4ef012dafd8e8fc586483f002b4` |
| درخت محصولی | پس از `aaec89a7` |
| upstream | ندارد؛ پوش نشده |

---

## Commitهای این دور اصلاح

از `64755d10` به بعد، بدون amend و بدون بازنویسی تاریخچه:

1. `becb9e19` — بستن سوراخ‌های TypeScript غیر Market بدون تضعیف tsconfig
2. `f760de07` — HelpPopover زنده، seen-list، و تلاش همگرایی سطح عملیات
3. `b9b44e8e` — E2E fail-closed و ماتریس ۲۹ مسیر
4. `6dfb3e4f` — بازگرداندن توکن کاتالوگ `--ui-v2-*` و خارج کردن `main.css` از scope گارد V2
5. `aaec89a7` — بازگرداندن motion نشست تأیید به `--ui-v2-motion-state`
6. همین commit اسناد — ثبت واقعیت blocked

Commitهای پیش از این دور که مانع‌های audit را بسته بودند: `ddf42ccb` overflow، `59e0cadb` landmark، `6ac2254a` inset، `e6318dab` کانال، `64755d10` overlay.

---

## اصلاح یافته‌های audit مستقل

| یافتهٔ `INDEPENDENT_AUDIT.md` | نتیجه |
|---|---|
| AppActionOverflow بدون keyboard | بسته در `ddf42ccb` |
| h1 مدیریت، دو main کانال، نبود main چت/اشتراک | بسته در `59e0cadb` |
| adapter و کارت تودرتو عملیات | کاهش‌یافته؛ هوک `ui-v2-workspace-*` برای تست ماند |
| overlay پیام‌رسان بدون dialog/focus | بسته برای overlayهای زندهٔ شناخته‌شده |
| CreateChannel HelpPopover و main تودرتو | HelpPopover و main تودرتو حذف شد؛ قاب بیرونی هنوز کمی کارت‌مانند است |
| div داخل h1 پروفایل | بسته |
| mock عمومی `json({})` در پذیرش | بسته در harness جدید؛ سه spec قدیمی هنوز `json({})` دارند |
| ROADMAP/inventory زود complete شده بودند | اصلاح شد؛ فاز ۱۱ blocked مانده |

---

## گیت‌های اندازه‌گیری‌شده روی درخت محصولی `aaec89a7`

این اعداد پیش از commit اسناد است. پس از docs باید `vue-tsc`، `guard:ui` و `memory-custodian check` دوباره اجرا شوند.

| گیت | نتیجه |
|---|---|
| `vue-tsc -p tsconfig.app.json --noEmit` | EXIT 2؛ فقط `MarketView.vue` خطوط ۸۲۵، ۹۶۱، ۹۶۲، ۱۲۷۱ |
| `npm run guard:ui` | PASS پس از `6dfb3e4f` |
| Vitest کامل `--maxWorkers=3` پیش از بازگرداندن motion نشست | ۴ شکست در `SessionApprovalModal.test.ts`؛ پس از `aaec89a7` همان فایل ۵/۵ سبز. اجرای کامل پس از docs لازم است |
| بیلد `/tmp/native-app-v2-prod-6dfb3e4f` | EXIT 0؛ ۱۷۰ فایل؛ ۵٬۷۲۷٬۱۰۹ بایت؛ digest `9c1f245693bd280f122dec5f518e5c0941b932acca7fcb6957caf28b77897e2f` |
| E2E Chromium preview | ۱۱ pass، ۲ skip مکمل FF/WK |
| E2E Firefox/WebKit خانوادهٔ حساس | ۴ pass |
| `git diff --check` | پاک روی تغییرات این دور |
| Market `MarketView.vue` | `6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608` |
| Market `OffersList.vue` | `9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c` |
| پیام‌رسان visual | ۸۵ فایل، ۱٬۲۹۵٬۸۵۹ بایت، sha `c440623fb6053b353a080b3c0b7506566d4e345f27ddf166c8ea350fd1d92028` |

Skipهای ماتریس پذیرش فقط مکمل مرورگرند، نه پنهان‌کردن شکست محصول.

---

## برگهٔ تماس موقت

خارج مخزن: `/tmp/native-app-v2-visual`
۶۲ تصویر مسیر + HTML؛ digest `fc569ee17cc988b11290d101651ddb350b87e6d635d1b2dcc5cf4f5fbc08afb9`.

بازبینی دستی: حساب، عملیات، ورود و پروفایل به زبان grouped نزدیک‌اند. کانال هنوز قاب بیرونی کارت‌مانند دارد. ویجت بازار خانه دست‌نخورده دیده شد.

---

## حفاظت

- `main` ویرایش نشد
- پوش، merge، deploy، Figma و Sites انجام نشد
- backend / schema / API تغییر نکرد
- رفتار پیام‌رسان، آلبوم و `expected_action` دست نخورد
- `.mimosa/` آنترک ماند و commit نشد

---

## کار باقی

1. سبز کردن `vue-tsc` بدون ویرایش محصول Market؛ فعلاً ممکن نیست
2. حذف `json({})` از سه spec قدیمی viewport/visual
3. کاهش قاب بیرونی CreateChannel تا زبان inset خالص
4. ممیزی بصری تمام overlay و stateهای کند/آفلاین روی بیلد تازه پس از docs
5. اجرای Vitest کامل روی HEAD نهایی
