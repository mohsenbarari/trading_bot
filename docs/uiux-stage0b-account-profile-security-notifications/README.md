# Stage 0B-5 — Account, profile, security and notifications evidence

وضعیت: **شواهد فنی تکمیل و خروجی بصری در ۲۰۲۶-۰۸-۰۸ به‌صورت صریح توسط مالک محصول تأیید شده است.** این checkpoint بسته است، اما هیچ مجوزی برای تغییر runtime صادر نشده است.

منبع editable و canonical صفحه `04 — Stage 0B-5 Account, Profile, Security & Notifications` با شناسه `117:2` در [فایل رسمی Figma](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv?node-id=117-2) است. freeze نهایی در `2026-08-08T17:10:58.500Z`، audit schema 2 در `2026-08-08T17:11:05.475Z` و capture مستقیم در `2026-08-08T17:13:12.738Z` ثبت شد. Foundations روی `41:2` و component catalog روی `46:2` قرار دارند.

این پوشه بسته evidence versioned و harness مشتق‌شده Stage است و کد runtime محصول نیست.

## ترتیب مرجع

1. checkpoint الزام‌آور Stage 0B-5 و سیاست خلوتی هدفمند؛
2. nodeهای editable نهایی Figma؛
3. `FIGMA_SNAPSHOT_MANIFEST.json`؛
4. PNGها و metrics مستقیم Figma؛
5. پیش‌نمایش خصوصی Sites؛
6. harness و PNGهای محلی مشتق‌شده.

Sites و harness برای مرور، fit، state و responsive validation هستند؛ هیچ‌کدام منبع canonical طراحی یا اثبات رفتار runtime نیستند.

## خروجی‌های مستقیم Figma — ثبت‌شده

- `assets/figma-account-profile-scenarios.png`
- `assets/figma-security-storage-scenarios.png`
- `assets/figma-notification-center-scenarios.png`
- `assets/figma-state-route-visibility-push-matrix.png`
- `assets/figma-responsive-and-desktop-proofs.png`
- `assets/figma-desktop-security-sessions-1440x900.png`
- `assets/figma-stage0b5-audit-metrics.json`

شش screenshot مستقیم از sectionهای `117:4` تا `117:8` و desktop root `143:668` ثبت شده‌اند. metrics مستقیم با SHA-256 برابر `351f6afafb0e2d3b1a08e908dcd88cb72d9d2fd4fed8110c3fb22c12c6658d94`، نتیجه `27 / 27`، صفر blocker و `142` target را ثبت می‌کند. شناسه، timestamp، ابعاد و checksum هر خروجی در manifest آمده است.

## خروجی‌های محلی مشتق‌شده — ثبت‌شده

- `assets/local-evidence/local-account-profile-scenarios.png`
- `assets/local-evidence/local-profile-visibility-matrix.png`
- `assets/local-evidence/local-security-storage-scenarios.png`
- `assets/local-evidence/local-notification-center-scenarios.png`
- `assets/local-evidence/local-state-route-push-atlas.png`
- `assets/local-evidence/local-account-notifications-responsive-sweep.png`
- `assets/local-evidence/local-desktop-security-sessions-1440x900.png`
- `assets/local-evidence/local-account-profile-security-notifications-validation-metrics.json`

اجرای نهایی harness:

- run ID: `2839230-1786210464518`؛
- زمان تولید: `2026-08-08T17:34:30.693Z`؛
- `27 / 27` assertion، صفر failure و صفر page error؛
- هفت capture، پنج عرض دقیق `360 / 375 / 390 / 414 / 430 × 844` و desktop دقیق `1440×900`؛
- `155` target با کمینه `44×44px` و CTA با کمینه ارتفاع `48px`؛
- pre/post canonical DOM یکسان با SHA-256 برابر `c5693eb79e0405cd7946a7d3ebeedd6b9a8fac3b7fe3699454aeac4c82eae831`؛
- metrics SHA-256 برابر `293524253132064c0056022132325e213f8122fc43c0bd8a3a9601a7f222ca91`؛
- HTML برابر `83e4b8a12d04eba3ca547aa31b63ac28598b5192be3606580838c29b0450e77e` و capture script برابر `bfba40a15fee2edf4ee924703a02ba9b5de860ce8d92c1458526fbcdd7c222f3`.

برای بازتولید بسته محلی اجرا شود:

```bash
node docs/uiux-stage0b-account-profile-security-notifications/capture-evidence.cjs
```

تولید fail-closed است: exact file set و ترتیب assertionها، pre/post remeasurement، checksumها و canonical DOM پیش از جایگزینی اتمیک کل پوشه بررسی می‌شوند.

## پیش‌نمایش Sites — منتشرشده و خصوصی

پیش‌نمایش مشتق‌شده با عنوان `Trading Bot UI/UX — Stage 0B-5` در [URL خصوصی مالک](https://trading-bot-uiux-stage0b5.mohsenbarari235.chatgpt.site) منتشر شده است:

- project: `appgprj_6a776942e35c819198a0dcab372ac65e`؛
- slug: `trading-bot-uiux-stage0b5`؛
- source commit: `9a710611d52ca24c5cd300fc010f464fb1ad33c3`؛
- version `1`: `appgprj_6a776942e35c819198a0dcab372ac65e~appgver_d0bbd46aed2481918e6dd16377916706`؛
- deployment موفق: `appgdep_6a776aae0604819185ff740c57054fac` روی `site---6a776942e35c819198a0dcab372ac65e`؛ وضعیت موفق در `2026-08-08T17:43:19.978890Z` ثبت و connector reread نهایی در `2026-08-08T17:43:58.035651Z` تأیید شد؛
- archive محلی: `391385` بایت با SHA-256 برابر `22d41b9fd89c7543c6be518fc7f23304daab84dd2390e936126bdd0a55f2f731`؛
- connector-normalized content: `890880` بایت و `27` فایل با SHA-256 برابر `058f397ec23d099c0ddcaf84e3f1a54ed1bcce86dc241cc43624b50d0bfc70a2`.

دسترسی پیش و پس از deploy به‌صورت `custom` و owner-only راستی‌آزمایی شد: یک کاربر مجاز، صفر گروه و صفر بازدیدکننده خارجی. probe ناشناس در `2026-08-08T17:43:56Z` با `HTTP 401`، `Cache-Control: no-store`، `Referrer-Policy: no-referrer` و عنوان `Sign in required` متوقف شد. bypass token درخواست نشد و محتوای signed-in live واکشی نشد؛ بنابراین drift review فنی فقط `passed_artifact_and_source_bound` است. مالک محصول سپس در ۲۰۲۶-۰۸-۰۸ خروجی بصری را به‌صورت صریح تأیید کرد.

build با Next.js `16.3.0`، audit سطح high با صفر vulnerability، Worker/ASSETS، سه probe محلی با پاسخ `200`، چهار فونت Vazirmatn، sensitive scan و query پانزده‌دقیقه‌ای با صفر Worker error پاس شدند. هیچ source map، env/key یا log وارد بسته نشد.

## قرارداد bounded این بسته

- ۱۰ root موبایل دقیق `390×844`؛
- پنج proof responsive و یک proof امنیت/نشست دقیق `1440×900` بدون افزودن حقیقت تازه؛
- ۱۵ recovery group، ۱۴ nested substate و ۹ Push state؛
- مقصد فعال `حساب` و همان ترتیب/label/SVG پوسته مصوب `0B-2`؛
- profile visibility matrix با phone masked و address hidden برای viewer عادی؛
- پایان همه نشست‌های دیگر با حفظ نشست جاری؛
- cache فقط محلی و browser/device-specific؛
- مرکز اعلان بدون total/category count ساختگی و بدون delete/clear؛
- Push فقط در state قابل اقدام و بدون disable/test خیالی؛
- ۶۵ variable، ۹ text style، ۲ effect، ۱۲ component set و ۵۴ variant؛
- صفر instance detached، صفر interior بازار/پیام‌رسان و فقط داده synthetic؛
- صفر تغییر runtime تا تأیید صریح `0B-6`.

دو carry-forward غیرمسدودکننده Figma صادقانه ثبت شده‌اند: avatar initials در component inherited متن‌استایل محلی exact ندارد، و variantهای Operations-active قدیمی Bottom Navigation بدهی focus/layout/style پیش از 0B-5 دارند. متن همچنان Vazirmatn و rootها و variantهای Account-active این Stage تمام گیت‌های fit، contrast و interaction را پاس کرده‌اند.

## baseline runtime

baseline مرتبط روی ۱۳ فایل به‌صورت سریالی با `--maxWorkers=1 --no-file-parallelism` اجرا شد: `13 / 13` فایل و `128 / 128` تست با exit code صفر و Vitest duration برابر `38.54s` پاس شدند. هشدار stale بودن Browserslist/caniuse-lite و logهای failure mock‌شده NetworkError، clear-cache و browser notification خروجی موردانتظار تست‌اند و failure نیستند.

این baseline رفتار موجود را ثبت می‌کند و پیاده‌سازی طراحی تازه را اثبات نمی‌کند.

## حدود ادعا و گیت جاری

شواهد static و مشتق‌شده permission، API mutation، redirect، session revocation، realtime recovery، Push delivery، cross-server/cross-channel sync، clipboard، focus management، screen reader، keyboard یا failure race واقعی را اثبات نمی‌کنند.

شواهد فنی Stage 0B-5 کامل و checkpoint با تأیید بصری صریح مالک در ۲۰۲۶-۰۸-۰۸ بسته است. `0B-6` فقط در سطح قرارداد طراحی در حال انجام و runtime implementation همچنان unauthorized است.
