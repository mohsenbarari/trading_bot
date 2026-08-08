# Stage 0B-2 — Home and authenticated shell evidence

منبع editable اصلی این checkpoint صفحه `01 — Stage 0B-2 Home & Shell` در [فایل Figma پروژه](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv) است. فایل‌های این پوشه شواهد versioned و harness مشتق‌شده‌اند و کد runtime محصول نیستند. نسخه نهایی Figma با قرارداد checkpoint و سیاست خلوتی هم‌راستا شده و برای تصمیم مالک محصول آماده است.

ترتیب مرجع در اختلاف شواهد:

1. قرارداد الزام‌آور checkpoint و سیاست خلوتی؛
2. nodeهای نهایی Figma که با همان قرارداد هم‌راستا هستند؛
3. `FIGMA_SNAPSHOT_MANIFEST.json`؛
4. PNGهای مستقیم Figma در `assets/`؛
5. harness محلی مشتق‌شده برای width/fit/desktop validation.

خروجی‌های مستقیم موجود از Figma:

- `assets/figma-core-mobile-scenarios.png`
- `assets/figma-role-recovery-scenarios.png`
- `assets/figma-connectivity-pwa-scenarios.png`
- `assets/figma-authenticated-shell-contracts.png`
- `assets/figma-responsive-and-desktop-proofs.png`
- `assets/figma-desktop-1440x900.png`

این PNGها خروجی مستقیم نسخه نهایی Figma هستند. width sweep دقیق برای ۳۶۰، ۳۷۵، ۳۹۰، ۴۱۴ و ۴۳۰ و proof دسکتاپ ۱۴۴۰×۹۰۰ در همان صفحه ثبت شده‌اند. manifest شناسه nodeها، checksumها و نتیجه audit ساختاری را نگه می‌دارد.

برای بازتولید شواهد محلی مشتق‌شده، پس از نصب dependencyهای frontend و Playwright اجرا شود:

```bash
node docs/uiux-stage0b-home-shell/capture-evidence.cjs
```

حدود ادعا و نتایج دقیق در `VALIDATION.md` ثبت می‌شود. قضاوت ارزش محتوا با `CONTENT_NECESSITY_AUDIT.md` انجام می‌شود؛ fit و نبود overflow به‌تنهایی خلوتی یا مفیدبودن محتوا را اثبات نمی‌کنند.

بازار و پیام‌رسان فقط در سطح مقصد navigation و slot قفل‌شده ورود بازار حضور دارند. هیچ صفحه یا رفتار داخلی آن‌ها در این artifact بررسی نشده است.
