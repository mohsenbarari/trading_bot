# گزارش نظارت — Native App Feel V2 (بازاعتبارسنجی)

مخاطب: ایجنت ناظر مستقل. ادعای اجرا را باور نکن مگر با شاهد فایل، `git diff`، یا اجرای تست.

تاریخ: ۲۰۲۶-۰۸-۲۷
شاخهٔ کار: فقط `candidate/webapp-native-app-v2`
حکم این سند: `READY FOR INDEPENDENT NATIVE UI REVIEW`
معنی حکم: ماشین و ماتریس نماینده اجرا شد. بازبینی انسان هنوز انجام نشده. owner-approved یا production-ready نیست.

---

## یافته‌های ناظر که این دور را شروع کرد

`BLOCKED — CORRECTION AND REVALIDATION REQUIRED`

- اتصال گیت کهنه بود؛ `main` جلو رفته بود
- ورود توسعه با hostname/IP خصوصی تشخیص داده می‌شد
- خطای TypeScript candidate-added روی `CustomerRelation | null`
- هشدار Vue: `class` روی `AppConfirmDialog` با root fragment
- flake تست LoginView در suite کامل
- زبان بومی ناقص (هاور، عملیات، پروفایل، متن)
- `.mimosa/` و `SUPERVISOR_HANDOFF.md` آنترک بودند

به گزارش قبلی اعتماد نشد. کد، گیت، گارد، `vue-tsc`، Vitest و مرورگر دوباره سنجیده شد.

---

## اصلاحات این دور

| مورد | نتیجه |
|---|---|
| Login dev shortcut | فقط `VITE_STAGING_DEV_LOGIN=true\|1`؛ پیش‌فرض خاموش؛ localhost و `10.*`/`172.*`/`192.168.*` منفی |
| CustomerRelation null | fail-closed در helperها؛ بدون `!`؛ تست focused |
| AppConfirmDialog | `backdropClass` رسمی؛ workspace با `backdrop-class` |
| Login flake | import ایستا + `vi.hoisted`؛ بدون `resetModules` سراسری؛ بدون افزایش timeout سراسری |
| هاور | chrome تزئینی hover ردیف حذف شد |
| `/operations` | `AppInsetGroup` + `AppListItem` |
| پروفایل | CSS مردهٔ `.profile-action-card` حذف؛ محدودیت‌ها inset؛ شماره/آدرس حذف نشد |
| متن | «در حال دریافت پرونده مشتری» |
| TypeScript UserProfile | `limitations_expire_at_jalali ?? undefined` |

---

## Binding تازه (پس از آخرین merge)

ثبت آغازین ناظر دیگر معتبر نیست. مقادیر واقعی پایان این دور:

| مورد | مقدار |
|---|---|
| شاخه | `candidate/webapp-native-app-v2` |
| HEAD | بعد از کامیت مستندات همین دور ثبت شود |
| مبنای track | `951ca9f0` |
| local main ادغام‌شده | `581396c6791fa9e1fdae9894d3bb56ffbd06f136` |
| origin/main | `255f8f70` — جدّ دور است؛ برای نظارت track از `951ca9f0..HEAD` استفاده کن |
| ahead/behind نسبت به local main | ۲۴ / ۰ در لحظهٔ پیش از کامیت مستندات |
| upstream | ندارد؛ پوش نشده |

`main` چند بار حین کار جلو رفت. هر بار فقط داخل candidate با merge commit (`--no-ff`) ادغام شد. rebase/squash/force نشد.

mergeهای این دور (همه فقط backend/market-data):

1. `e9138367` `feat(market-data): add safe staged history backfill`
2. `1e0ab0f4` `fix(market-data): bound history export temp storage`
3. `16d268c5` `fix(market-data): keep receiver health metrics constant-time`
4. `e37aa09d` `feat(market-data): throttle staging history import`
5. `581396c6` `fix(market-data): tolerate bounded receiver commit load`

تعاش فایلی با UI candidate خالی بود. تعارض سطح محافظت‌شده رخ نداد.

---

## شمارش تست

### گارد UI

`npm run guard:ui` پاس.

هش قهرمان بازار خانه: `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860`
`native-app-messenger-visual-v1`: ۸۵ فایل، ۱۲۸۸۷۸۹ بایت.

### Vitest

دو اجرای کامل متوالی با همان فرمان `npx vitest run --maxWorkers=3`:

| اجرا | فایل | تست | شکست | timeout | مدت |
|---|---|---|---|---|---|
| ۱ | ۱۶۹ | ۱۹۸۰ | ۰ | ۰ | ۱۸۴٫۳۶ث |
| ۲ | ۱۶۹ | ۱۹۸۰ | ۰ | ۰ | ۱۸۹٫۰۵ث |

`--maxWorkers=3` افزایش timeout نیست. علت: تست بازار `OffersList` در isolation حدود ۳٫۶ثانیه است و زیر بار موازی پیش‌فرض یک‌بار در ۱۰ثانیه timeout شد. محصول Market دست نخورد.

هشدار `extraneous non-props attribute` مربوط به dialog: صفر.

سایر stderr: مسیرهای خطای تعمدی (PublicProfile history، AttachmentMenu camera، ChatView). شکست تست نیستند.

### vue-tsc

فرمان یکسان: `vue-tsc -p tsconfig.app.json --noEmit`
main از shadow خارج worktree با همان `node_modules` candidate (بدون symlink داخل main).

پس از نرمال‌سازی مسیر و حذف شماره خط: ۶۴ تشخیص در هر طرف.
خطای محصولی candidate-added: صفر.
اختلاف باقی: `DashboardView.test.ts` برای import همان `.mjs` گارد Stage 3؛ candidate `TS7016` و main `TS2307`. ارثی است، نه اصلاح Market.

### بیلد

`FRONTEND_BUILD_OUT_DIR=/tmp/native-app-v2-supervisor-dist npm run build`
EXIT 0. `mini_app_dist` دست نخورد.
حجم ۵٫۹M / ۱۷۱ فایل.
digest درخت: `2cb4b2f304935e3205dee62fa870ebbd228c38761c5ddbc4915c0345c1f9e44b`

### مرورگر (همان بیلد)

`CI=1` + preview روی ۴۱۷۳.

| نتیجه | تعداد | توضیح |
|---|---|---|
| pass | ۴۲ | remaining-route + ماتریس ۲۹ مسیر Chromium + خانواده‌های حساس هر سه مرورگر + ورود |
| skip | ۶ | پایین |
| fail | ۰ | |

ماتریس نماینده است؛ Cartesian کامل نقش×عرض×حالت نیست.

Skipها:

| شناسه | تعداد | علت |
|---|---|---|
| BR-MATRIX-001 | ۴ | ماتریس ۲۹ مسیر فقط Chromium؛ Firefox/WebKit خانوادهٔ حساس را می‌پوشانند |
| CDP zoom | ۲ | زوم ۲۰۰٪ فقط Chromium |

Skip داخل تست پاس‌شده (نه `test.skip`):

| شناسه | مسیر | علت |
|---|---|---|
| NAV-MAIN-001 | `/chat` `/share-receive` `/admin/channels` | landmark تکی بدون drift هش visual پیام‌رسان ممکن نیست |
| EXT-001 | همه | `telegram.org/js/telegram-web-app.js` وابستگی از پیش موجود Mini App است |

Warning پیش‌نمایش: ۶ بار `vite ws proxy` / `EPIPE`. قطع websocket پیش‌نمایش است، نه درخواست محصولی جدید.

---

## حفاظت

- `MarketView.vue` و `OffersList.vue` نسبت به local main اختلاف ندارند
- confirm تقویم: `TradingSettings.vue:300` همان `آیا از حذف این استثنای تقویمی مطمئن هستید؟`
- HelpPopover بازار در AdminMessages: ۳؛ کانال: ۲؛ PublicProfile: ۰
- دو `.copy-btn` در CreateInvitation
- `album_id` + `album_index` زنده‌اند؛ rollout legacy-default
- `<script setup>` در `ChatView.vue` نسبت به `951ca9f0` بایت‌به‌بایت یکسان است
- Session V2 Escape-dismiss ممنوع مانده
- `main` و worktree آن ویرایش نشد

---

## `.mimosa`

متعلق به پروژه نیست. hook موقت Codex با قرارداد `mimosa-stop-continuation/v1`.

- ساخته‌شده: ۲۰۲۶-۰۸-۲۷ حدود ۰۶:۵۵ UTC
- پایان دور: حدود ۱۰۰۰۴ فایل، ۲۱۳M (در طول نشست رشد کرد)
- در تاریخچهٔ گیت نیست
- stage/commit/archive نشد
- در زمان نشست حذف نشد

---

## محدودیت باقی

- هوک‌های قفل تست `ui-v2-*`
- landmark تکی immersive پیام‌رسان
- HelpPopover بازار و کانال
- دو دکمهٔ copy دعوت
- ماشهٔ حساب خانه هنوز disclosure سفارشی است
- `vue-tsc` ارثی main صفر نیست
- ماتریس مرورگر Cartesian کامل نیست
- `.mimosa` محلی است و پاک‌سازی‌اش کار محصول نیست

---

## حکم ممنوع برای ناظر بعدی

ننویس: owner-approved، production-ready، آمادهٔ پوش/ادغام/استقرار.

اقدام بعدی ناظر: بازبینی مستقل ظاهر بومی غیر بازار روی همین candidate؛ بدون پوش.
