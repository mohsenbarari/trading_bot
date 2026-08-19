# Validation — WebApp UIUX Unification V3

قرارداد پذیرش این track مستقل است. ماتریس و رسید Stage 8 مرجع تاریخی
می‌مانند و در این فایل شمارش یا overwrite نمی‌شوند.

سناریوی اجرا نشده pass اعلام نمی‌شود. هر N/A دلیل صریح دارد.
این برنامه owner-approved یا production-ready اعلام نمی‌کند.

## وضعیت شواهد پس از بازبینی مستقل

اجرای ۱۶۶/۱۶۶ قبلی برای پذیرش state قابل استفاده نیست: harness پس از release و
settle تصویر می‌گرفت و visibility حالت درخواستی را الزام نمی‌کرد. آن artifact حذف
یا بازنویسی نمی‌شود، اما superseded/non-promotable است. اجرای اصلاحی اولیه هفت
ادعای loading نادرست را fail-closed کرد؛ پس از اصلاح selector، انتظار mount و
identity recovery، برش سخت‌گیرانهٔ state با ۴۸/۴۸ پاس شد. اجرای کامل اصلاح‌شده
روی commit تمیز ۱۶۷/۱۶۷ پاس شد و receipt مستقل آن در
`FINAL_INTEGRATION_RECEIPT.json` ثبت شده است.

## گیت‌های اجباری پایانی

| فرمان | نتیجه | یادداشت |
|---|---|---|
| full frontend Vitest | exit 0 | ۱۶۹ فایل، ۱۹۵۹ تست؛ اجرای serial بدون timeout |
| production build با خروجی ایزوله | exit 0 | ۲۲۰۵ module، ۱۷۳ فایل؛ `mini_app_dist` دست نخورد |
| `npx vue-tsc --noEmit` | exit 0 | |
| `npm run guard:ui` | exit 0 | پس از برگرداندن فایل‌های منجمد و حذف نشانگرهای `--ui-v2-*` از CSS جدید |
| `git diff --check` | سبز | |
| آزمون‌های route/scope manifest | سبز | `uiux-unification-v3-inventory-guard.test.mjs` |
| ماتریس مرورگر production محلی | ۱۶۷/۱۶۷ | clean-bound؛ ۴۸ state قابل‌اجرا با evidence مستقیم، ۴۲ N/A دلیل‌دار، Firefox و WebKit هرکدام ۱۲/۱۲ |
| accessibility / overflow / CTA / unnamed | در probe موجود | overflow افقی صفر؛ CTA پوشیده صفر در سناریوهای معتبر |
| `memory-custodian check` | OK | از ریشهٔ مخزن |
| همگام‌سازی با `origin/main` | انجام شد | `2f8dd6e0` در شاخهٔ کاندید merge شد؛ main تغییر نکرد |

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

- keyboard روی خانه، ورود، پروفایل، عملیات مشتری و حساب؛ منوی هویت خانه با ArrowDown/Escape و focus restoration
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
- اجرای نهایی پس از همگام‌سازی main: ۱۷۳ فایل، اثر انگشت `b98708c8d303dc30759c091cd1a9d741cadb42a258b5f81d0e90d4926a0bb8dc`، JS دارایی ۳٬۸۶۶٬۵۹۳ بایت
- اختلاف با فاز یک معیار مستقیم regression نیست، چون سه commit مستقل main نیز پیش از اجرای نهایی وارد شاخه شدند
