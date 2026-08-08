# Stage 0B-2 — Home and authenticated shell evidence

منبع editable اصلی این checkpoint صفحه `01 — Stage 0B-2 Home & Shell` در [فایل Figma پروژه](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv) است. فایل‌های این پوشه شواهد versioned و harness مشتق‌شده‌اند و کد runtime محصول نیستند. تا وقتی Stage باز است، قرارداد مکتوب checkpoint بر deviation شناخته‌شده working draft Figma مقدم است؛ پس از هم‌راستاسازی، Figma دوباره مرجع بصری مستقیم خواهد بود.

ترتیب مرجع در اختلاف شواهد:

1. قرارداد الزام‌آور checkpoint و سیاست خلوتی؛
2. nodeهای Figma که با همان قرارداد هم‌راستا و سپس توسط مالک مصوب شده‌اند؛
3. `FIGMA_SNAPSHOT_MANIFEST.json`؛
4. PNGهای مستقیم Figma در `assets/`؛
5. harness محلی مشتق‌شده برای width/fit/desktop validation.

خروجی‌های مستقیم موجود از Figma:

- `assets/figma-core-mobile-scenarios.png`
- `assets/figma-role-recovery-scenarios.png`
- `assets/figma-connectivity-pwa-scenarios.png`

پس از ساخته‌شدن قرارداد shell، سقف فراخوانی MCP پلن Starter فعال شد. nodeهای shell با شناسه‌های ثبت‌شده در manifest ساخته شده‌اند، اما screenshot مستقیم آن section و افزودن width sweep/desktop proof به Figma هنوز باز است. فراخوانی شکست‌خورده تکرار نشد.

PNGهای مستقیم موجود working draft هستند: داخل slot بازار هنوز ظاهر/CTA غیرمجاز ترسیم شده و subtitle «حساب جاری» باقی مانده است. این دو مورد همراه اصلاح stateهای ثبت‌شده در checkpoint باید در نخستین فرصت write بعدی Figma برطرف شوند؛ PNGها برای حفظ provenance نگه‌داری می‌شوند و approval visual نیستند.

برای بازتولید شواهد محلی مشتق‌شده، پس از نصب dependencyهای frontend و Playwright اجرا شود:

```bash
node docs/uiux-stage0b-home-shell/capture-evidence.cjs
```

حدود ادعا و نتایج دقیق در `VALIDATION.md` ثبت می‌شود. قضاوت ارزش محتوا با `CONTENT_NECESSITY_AUDIT.md` انجام می‌شود؛ fit و نبود overflow به‌تنهایی خلوتی یا مفیدبودن محتوا را اثبات نمی‌کنند.

بازار و پیام‌رسان فقط در سطح مقصد navigation و slot قفل‌شده ورود بازار حضور دارند. هیچ صفحه یا رفتار داخلی آن‌ها در این artifact بررسی نشده است.
