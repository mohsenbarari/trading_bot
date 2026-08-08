# Stage 0B-3 — Operations and workspaces evidence

منبع editable اصلی این checkpoint صفحه `02 — Stage 0B-3 Operations & Workspaces` در [فایل Figma پروژه](https://www.figma.com/design/z8jgJxST4O2APzWnlyP9gv) است. Foundations روی صفحه `41:2` و component catalog روی صفحه `46:2` قرار دارند. فایل‌های این پوشه شواهد versioned و harness مشتق‌شده‌اند و کد runtime محصول نیستند.

ترتیب مرجع در صورت اختلاف شواهد:

1. checkpoint الزام‌آور و سیاست خلوتی هدفمند؛
2. nodeهای editable نهایی Figma که با همان قرارداد هم‌راستا هستند؛
3. `FIGMA_SNAPSHOT_MANIFEST.json`؛
4. PNGهای مستقیم Figma در `assets/`؛
5. harness محلی مشتق‌شده برای fit، state و responsive validation.

## خروجی‌های مستقیم Figma

- `assets/figma-operations-role-scenarios.png`
- `assets/figma-customer-workspace-scenarios.png`
- `assets/figma-accountant-workspace-scenarios.png`
- `assets/figma-actions-and-state-atlas.png`
- `assets/figma-responsive-and-desktop-proofs.png`
- `assets/figma-desktop-customer-master-detail-1440x900.png`
- `assets/figma-stage0b3-audit-metrics.json`

این خروجی‌ها sectionهای نهایی Figma را ثبت می‌کنند. source node و checksum هر فایل در manifest نگهداری می‌شود.

## خروجی‌های محلی مشتق‌شده

- `assets/local-evidence/local-operations-role-matrix.png`
- `assets/local-evidence/local-customer-task-flow.png`
- `assets/local-evidence/local-accountant-task-flow.png`
- `assets/local-evidence/local-workspace-state-atlas.png`
- `assets/local-evidence/local-workspace-action-feedback.png`
- `assets/local-evidence/local-workspaces-responsive-sweep.png`
- `assets/local-evidence/local-customer-master-detail-1440x900.png`
- `assets/local-evidence/local-workspaces-validation-metrics.json`

برای بازتولید artifactهای محلی، پس از نصب dependencyهای frontend و Playwright اجرا شود:

```bash
node docs/uiux-stage0b-operations-workspaces/capture-evidence.cjs
```

تولید evidence باید fail-closed باشد: مجموعه دقیق assertionها پیش و پس از capture روی خروجی موقت سنجیده می‌شود، مرورگر پیش از انتشار بسته می‌شود و سپس کل bundle در `assets/local-evidence/` با دو rename سطح پوشه و یک backup کامل جایگزین می‌شود. میان دو rename ممکن است برای لحظه‌ای مسیر live وجود نداشته باشد، اما هرگز مجموعه ناقص یا ترکیب PNG تازه با metrics قدیمی منتشر نمی‌شود؛ اجرای بعدی پیش از بررسی dependencyها backup کامل را بازیابی می‌کند. metrics نیز `runId` و checksum هر capture همان اجرا را در خود نگه می‌دارد.

نتایج رسمی و حدود ادعا در `VALIDATION.md` ثبت شده‌اند. ارزش محتوای هر قاب در `CONTENT_NECESSITY_AUDIT.md` ممیزی می‌شود؛ نبود overflow یا فضای سفید کافی، به‌تنهایی مفیدبودن محتوا را اثبات نمی‌کند.

بازار و پیام‌رسان فقط به‌عنوان مقصد shell حضور دارند. هیچ صفحه، FAB، state یا رفتار داخلی آن‌ها در این artifact بررسی یا بازطراحی نشده است.
