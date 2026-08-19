# Validation — WebApp UIUX Unification V3

قرارداد پذیرش این track مستقل است. ماتریس و رسید Stage 8 مرجع تاریخی
می‌مانند و در این فایل شمارش یا overwrite نمی‌شوند.

سناریوی اجرا نشده pass اعلام نمی‌شود. هر N/A دلیل صریح دارد.
این برنامه owner-approved یا production-ready اعلام نمی‌کند.

## گیت‌های اجباری پایانی

| فرمان | نتیجه | یادداشت |
|---|---|---|
| full frontend Vitest | exit 0 | ۱۶۸ فایل، ۱۹۵۱ تست. یک اجرای قبلی `LoginView` OTP به‌خاطر timeout زیر بار flake شد و در اجرای جدا و اجرای کامل بعدی سبز بود |
| `FRONTEND_BUILD_OUT_DIR=/tmp/uiux-unification-v3-dist-phase11 npm run build` | exit 0 | `mini_app_dist` دست نخورد |
| `npx vue-tsc --noEmit` | exit 0 | |
| `npm run guard:ui` | exit 0 | پس از برگرداندن فایل‌های منجمد و حذف نشانگرهای `--ui-v2-*` از CSS جدید |
| `git diff --check` | سبز | |
| آزمون‌های route/scope manifest | سبز | `uiux-unification-v3-inventory-guard.test.mjs` |
| ماتریس مرورگر production محلی | ۱۶۶/۱۶۶ پس از اصلاح زوم CDP | Chromium + Firefox + WebKit |
| accessibility / overflow / CTA / unnamed | در probe موجود | overflow افقی صفر؛ CTA پوشیده صفر در سناریوهای معتبر |
| `memory-custodian check` | OK | از ریشهٔ مخزن |
| merge-tree فقط خواندنی در برابر `origin/main` | بدون تعارض | `git merge-tree --write-tree` → `7a350eb676e39e56902af79afc0783d44872eed0` |

## محیط‌های اجراشده

- مرورگر موبایل و دسکتاپ روی fixture محلی
- شبیه‌سازی PWA (`display-mode: standalone`) برای خانه، حساب، پروفایل
- Telegram Mini App در ماتریس نیست

## پروفایل‌های دسترسی اجراشده

برای هر مسیر فقط `renderProfileId` قرارداد موجود اجرا شد، نه ضرب کامل ۹ نقش.
نقش‌های غیرمجاز برای آن مسیر N/A هستند چون قرارداد مسیر آن‌ها را render نمی‌کند.

## Viewportهای اجراشده

- همهٔ ۳۰ مسیر: ۳۹۰×۸۴۴ و ۱۴۴۰×۹۰۰
- خانوادهٔ خانه / پروفایل / عملیات مشتری / حساب / مدیریت / ورود: ۳۶۰×۷۴۰، ۴۳۰×۹۳۲، ۷۶۸×۱۰۲۴

## تعامل‌های اجراشده

- keyboard Tab روی ورود، پروفایل، عملیات مشتری، حساب
- reduced-motion روی بازار، خانه، پروفایل
- زوم ۲۰۰٪ با CDP `Emulation.setPageScaleFactor=2` روی ۶ مسیر حساس
- زوم CSS `documentElement.style.zoom=2` به‌عنوان روش اشتباه کنار گذاشته شد؛ ۵ شکست آن false positive پروب بود

## سلول‌های N/A

- ۴۲ حالت loading/empty/error که descriptor موجود `applicable=false` دارد، با دلیل همان descriptor
- بقیهٔ ضرب نقش × viewport × state × interaction اجرا نشد و pass نیست
- دلیل: این track ماتریس نماینده روی harness موجود را اجرا می‌کند، نه انفجار دکارتی کامل

## پاک‌سازی CSS

اسکن اکتشافی روی فایل‌های لمس‌شده:

- `public-profile-view` در workspace استفاده می‌شود (`:class`)
- `account-status-dot--*` به‌صورت پویا ساخته می‌شود
- `.logout-btn` / `.today-trades-refresh` فقط در گروه focus-visible قدیمی خانه هستند و حذف نشدند چون مصرف قطعی مرده ثابت نشد

هیچ selector صرفاً به‌خاطر «به نظر بلااستفاده» حذف نشد. قرارداد مسیر و `data-ui-system=v2` عوض نشد.

## مقایسه bundle

- فاز یک: ۱۷۶ فایل، اثر انگشت `9a3e536614403b398f88415facfbd52ffb15277d4e5e2a63ed0e54f05798b2be`، JS دارایی ۳٬۸۰۲٬۷۶۳ بایت
- فاز یازده: ۱۷۳ فایل، اثر انگشت `fb178b038abf792414387aceb0c09dae77bc97a579ecd80aa68792b92882c10d`، JS دارایی ۳٬۸۰۳٬۳۹۰ بایت
- اختلاف JS: ۶۲۷+ بایت
