# Stage 0B-2 validation record

وضعیت: در حال اجرا؛ شواهد مستقیم سه section موبایل Figma ثبت شده‌اند، اما screenshot نهایی shell و انتقال width sweep/desktop proof به Figma به‌دلیل سقف Starter باز است.

## شواهد مستقیم Figma

- صفحه مستقل: `12:2`؛
- ۱۰ قاب موبایل طراحی‌شده با مرجع ۳۹۰×۸۴۴؛
- سه screenshot مستقیم و checksum‌شده از nodeهای `13:2`، `14:2` و `15:2`؛
- فونت‌های در دسترس و استفاده‌شده: Vazirmatn با وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰؛
- icon واقعی PWA روی node `15:239`؛
- section قرارداد shell با node `16:2` ساخته شد، اما screenshot بعدی با پیام سقف Starter متوقف شد و تکرار نشد.

بازبینی بصری screenshots موجود نشان داد:

- bottom navigation داخل همه قاب‌ها می‌ماند و محتوای اصلی را نمی‌پوشاند؛
- حالت آرام فضای سفید هدفمند دارد؛
- هشدار محدودیت و غیرفعالی فقط یک خانه بصری دارند؛
- offline، stale و PWA به‌صورت سه state قابل‌تمایز دیده می‌شوند؛
- هیچ شمارنده مسیر/ابزار/رابطه، role chip، greeting یا summary سلامت در قاب‌های محصول وجود ندارد.

دو deviation بصری در همین screenshots وجود دارد و مانع پذیرش نهایی است: subtitle تکراری «حساب جاری» و طراحی تازه داخل slot محافظت‌شده بازار. علاوه بر آن، stateهای inactive/error/offline/stale باید مطابق findingهای checkpoint اصلاح شوند. بنابراین screenshots موجود provenance working draft هستند، نه evidence پذیرش نهایی.

## harness مشتق‌شده

`home-shell-evidence.html` و `capture-evidence.cjs` برای کنترل مکانیکی موارد زیر استفاده می‌شوند:

- width sweep در ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰؛
- fit و نبود overflow افقی/عمودی؛
- safe clearance بالای bottom navigation؛
- حداقل ۴۴×۴۴ برای target عمومی و حداقل ارتفاع ۴۸ برای CTA؛
- بارگذاری واقعی Vazirmatn؛
- proof دسکتاپ ۱۴۴۰×۹۰۰ با همان اقتصاد اطلاعات موبایل؛
- state atlas پوسته و modal امنیتی.

تولید artifactها به‌صورت fail-closed و در staging موقت انجام می‌شود: assertionها پیش از capture پاس می‌شوند و PNGها و metrics فقط پس از موفقیت کل اجرا به مقصد نهایی منتقل می‌شوند؛ بنابراین شکست عادی اجرا evidence تازه را با metrics قدیمی مخلوط نمی‌کند.

اجرای محلی نهایی در ۲۰۲۶-۰۸-۰۸:

- ۱۸/۱۸ assertion پاس، بدون failure یا `pageerror`؛
- پنج عرض CSS دقیق: ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰؛
- ۱۹ product screen بدون overflow افقی یا عمودی؛
- حداقل clearance محتوای خانه آرام تا bottom navigation برابر ۵۰۳px؛
- ۷۳ target قابل‌تعامل با حداقل اندازه ۴۴×۴۴؛
- ۵ CTA با حداقل ارتفاع ۴۸px؛
- ۵۴ برچسب navigation با حداقل اندازه متن ۱۱px و بدون violation؛
- چهار face واقعی Vazirmatn در وزن‌های ۴۰۰، ۵۰۰، ۶۰۰ و ۷۰۰ با status برابر `loaded`؛
- proof مستقل و دقیق دسکتاپ با فایل ۱۴۴۰×۹۰۰؛
- شش قرارداد مسیر: چهار bottom navigation و دو floating shell برای سطوح محافظت‌شده.

خروجی‌های مشتق‌شده:

- `assets/local-home-shell-responsive-sweep.png`
- `assets/local-home-shell-quiet-390.png`
- `assets/local-home-shell-route-matrix.png`
- `assets/local-home-shell-state-atlas.png`
- `assets/local-home-shell-security-session-modal.png`
- `assets/local-home-shell-adaptive-desktop.png`
- `assets/local-home-shell-desktop-1440x900.png`
- `assets/local-home-shell-validation-metrics.json`

### رفع blockerهای draft در harness مشتق‌شده

- تمام slotهای بازار به placeholder هاشورخورده و صریحاً غیرهنجاری تبدیل شده‌اند؛ هیچ CTA، badge وضعیت یا کنترل قابل‌تعامل بازار در آن‌ها تعریف نشده است.
- عبارت `حساب جاری` یا badge معادل آن در قاب‌های محصول وجود ندارد.
- حالت حساب غیرفعال market pseudo-card ندارد و یک CTA صریح `پیگیری در حساب` دارد.
- loading و error هیچ مقصد permission-dependent بازار/عملیات ندارند و loading فقط skeleton خنثی است.
- متن error علت فنی یا شبکه را فرض نمی‌کند.
- offline و stale هرکدام فقط یک connection signal دارند؛ badge یا CTA غیرفعال تکراری ندارند.
- متن وعده‌دهنده retry در آینده وجود ندارد؛ `تلاش دوباره` فقط اقدام قابل‌اجرای همان لحظه در error است.
- headerهای استاندارد یک کنترل اعلان دارند، modal نشست زمان و countdown لازم را نگه می‌دارد و کارت PWA همان آیکون واقعی `frontend/public/pwa-192x192.png` را بارگذاری می‌کند.

این قیود در assertionهای `protected-market-placeholder-only` و `draft-state-blockers-resolved` داخل JSON نیز ماشینی کنترل می‌شوند.

## baseline runtime

این تست‌ها رفتار فعلی را ثبت می‌کنند و پیاده‌سازی طرح تازه را اثبات نمی‌کنند:

- `frontend/src/App.test.ts`
- `frontend/src/components/AppAuthenticatedShell.test.ts`
- `frontend/src/components/BottomNav.test.ts`
- `frontend/src/router/index.test.ts`
- `frontend/src/views/DashboardView.test.ts`
- `frontend/src/components/PWAInstallOverlay.test.ts`
- `frontend/src/utils/pwaInstall.test.ts`

اجرا در ۲۰۲۶-۰۸-۰۸:

```text
Test Files  7 passed (7)
Tests       39 passed (39)
Duration    9.37s
```

دستور:

```bash
npm run test:unit:run -- \
  src/App.test.ts \
  src/components/AppAuthenticatedShell.test.ts \
  src/components/BottomNav.test.ts \
  src/router/index.test.ts \
  src/views/DashboardView.test.ts \
  src/components/PWAInstallOverlay.test.ts \
  src/utils/pwaInstall.test.ts
```

اجرای نخست فقط به‌علت نبود `node_modules` در worktree ایزوله به `vitest: not found` رسید. بدون نصب شبکه‌ای، `node_modules` موجود checkout اصلی به worktree symlink شد و اجرای واقعی بالا با ۳۹/۳۹ تست پاس شد. symlink یک artifact محلی ignored است و در commit وارد نمی‌شود.

## حدود ادعا

شواهد static Figma و HTML نمی‌توانند router واقعی، cache/offline runtime، نصب PWA مرورگر، WebView تلگرام، screen reader، focus management، zoom/font scaling یا modal security behavior را اثبات کنند. این موارد در Stageهای ۱، ۳، ۴، ۷ و ۸ validation اجرایی دارند.

تا وقتی width sweep و desktop proof روی صفحه Figma ثبت و screenshot/metadata نهایی گرفته نشود، checkpoint از نظر فنی بسته نیست.
