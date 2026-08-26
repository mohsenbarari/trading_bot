# Gate receipt مرحله ۳ Docker foundation

تاریخ اجرا: 2026-08-26

وضعیت gate: **PASS برای Docker/deploy foundation و fixture rehearsal؛ بدون deployment، cutover یا مالکیت live**

مبنای اجرای نهایی: `main@1ca3dbd96d8fc194cf15a36dc8a8704b117e413a`

## 1. مرز ایمنی مرحله

این مرحله فقط image، Compose topology، migration runner، preflight/inventory و runtime مصنوعی را تثبیت کرد. `MARKET_PIPELINE_MODE=live` با exit code `78` fail-closed است. هیچ Telegram session واقعی، source binding، private transport credential، staging/production database یا مدل اصلی در rehearsal استفاده نشد.

Compose محصول، serviceهای host-native و authority فعلی تغییر نکردند. bind-root ساخته‌شده در مرحله ۲ روی سرور وب نیز در این مرحله chown یا deploy نشد؛ اعمال permission واقعی و بالا آوردن stack مجوز مستقل محیط می‌خواهد.

## 2. image و dependency contract

- Dockerfile چندمرحله‌ای مستقل در `deploy/market-data/Dockerfile`؛
- Python base: `python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b`؛
- Dockerfile frontend: `docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e`؛
- PostgreSQL: `postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b`؛
- dependencyهای Stage 3 با نسخه و SHA-256 در `requirements.lock` قفل شده‌اند؛
- runtime user عددی `10001:10001` است و user creation زمان‌دار داخل image ندارد؛
- rootfs مونتاژشده پیش از ورود به runtime با `SOURCE_DATE_EPOCH` زمان commit و ownership قطعی نرمال می‌شود.

اجرای نهایی:

- دو build مستقل و `--no-cache` از SHA یکسان، image ID دقیقاً یکسان ساختند؛
- candidate image ID موقت: `sha256:b3a677c4d827c6607b9afd32c77e001d6618bf955d5759324f57a668c3606d91`؛
- runtime: Python 3.11.16؛ size: 141.130 MiB؛
- build اول/دوم: 17.434/15.966 ثانیه؛
- filesystem secret scan و Docker history secret scan هر دو PASS؛
- imageهای rehearsal بعد از آزمون حذف شدند؛ این ID، registry manifest digest یا release منتشرشده نیست. digest انتشار هر release باید هنگام push/load همان commit روی هر دو میزبان جداگانه pin و verify شود.

## 3. Compose topology

تعریف به سه فایل تقسیم شد:

- `compose.yml`: امنیت، resource/log policy و networkهای مشترک؛
- `compose.web.yml`: هفت service وب/داده شامل database، migration و پنج runtime؛
- `compose.bot.yml`: چهار runtime سرور بات.

تمام serviceهای فعال:

- non-root؛
- root filesystem فقط‌خواندنی؛
- `cap_drop: ALL` و `no-new-privileges`؛
- tmpfs محدود، PID/memory/CPU limit، log rotation و `on-failure:5`؛
- بدون `privileged` و بدون build/runtime secret داخل image یا env.

فقط `estimator-snapshot-receiver` و `market-fact-receiver` port دارند. در fixture روی loopback با port تصادفی اجرا شدند. preflight غیر fixture، wildcard، loopback، public، multicast و reserved IP را رد می‌کند و target port فقط `9443` است. listener داخل container روی همهٔ interfaceهای container است، اما publish میزبان فقط روی private IP دقیق انجام می‌شود.

## 4. volume و single-owner

Web و Bot data-root مستقل دارند. PostgreSQL، capture، session، state، Market Store، model و snapshot همگی bind mount پایدار و خارج container layer هستند. model read-only است و SQLite روی network filesystem یا بین دو میزبان share نمی‌شود.

قفل process روی state وجود دارد، اما authority فقط به آن وابسته نیست:

- هر capture علاوه بر state، روی همان Telegram session mount قفل می‌گیرد؛
- `market-store-adapter` علاوه بر state، روی همان Market Store directory قفل می‌گیرد.

rehearsal با state path متفاوت و resource mount مشترک ثابت کرد مالک دوم capture و writer دوم SQLite هر دو fail-closed می‌شوند. SQLite پس از force-recreate adapter سالم و با همان داده باقی ماند.

## 5. secret contract

- parent directory: `root:root 0700`؛
- secret file: `root:10001 0440`؛
- runtimeها با GID `10001` فقط فایل‌های mount‌شده خود را می‌بینند؛
- PostgreSQL با UID/GID `70:70` اجرا می‌شود و فقط supplemental group `10001` دارد؛
- secret در Compose environment، image history، image filesystem، log یا inventory مقداردهی نمی‌شود؛ inventory فقط نام secret contract را ثبت می‌کند.

علت این انتخاب، رفتار Compose غیر Swarm است که file secret را bind-mount و owner/mode میزبان را حفظ می‌کند. استفاده از `root:root 0600` با container غیر root شکست درست ایجاد می‌کرد؛ world-readable کردن یا root container رد شد.

## 6. migration و storage rehearsal

- PostgreSQL اختصاصی Market Data بالا آمد؛ product DB در دسترس یا touched نبود؛
- migration مستقل 22 جدول ساخت؛
- اجرای دوم migration، `already_current` و no-op بود؛
- database port روی میزبان publish نشد؛
- runtime rollback، down migration اجرا نکرد و schema/state را حفظ کرد.

## 7. fixture transport و persistence

- یک `market_fact_batch/1.0` پذیرفته و durable شد؛
- replay همان batch با duplicate count برابر 1 و بدون درج دوباره ACK شد؛
- `estimator_snapshot/1.0` پذیرفته و atomically جایگزین شد؛
- receiverهای fixture فقط endpointهای صریح `/fixture/...` داشتند و raw payload را log نکردند؛
- rollback به image متمایز همان source با version label قبلی، schema و state را حفظ کرد.

TLS/HMAC live در این fixture فعال نشد؛ primitives آن در Gate مرحله ۱ آزمایش شده و wiring زنده در مراحل transport/cutover بعدی انجام می‌شود.

## 8. inventory، preflight و rollback

`scripts/manage_market_pipeline_stage3.py` خروجی machine-readable برای هر host profile می‌سازد و موارد زیر را fail-closed کنترل می‌کند:

- service inventory دقیق؛
- image revision/user/platform و digest pin در حالت non-fixture؛
- private bind و نبود port اضافی؛
- read-only/non-root/capability/security profile؛
- path owner/mode و ممنوعیت symlink/broad root؛
- secret parent/file owner/mode؛
- نبود plaintext secret environment.

`prepare-paths` dry-run است و برای mutation به هر دو flag `--apply --acknowledge-host-mutation` نیاز دارد. هیچ فرمان حذف data/volume/down migration در مسیر deploy آینده وجود ندارد. rollback فقط image را force-recreate می‌کند و volume/checkpoint/outbox را نگه می‌دارد.

## 9. cleanup و نتیجه gate

پس از اجرای نهایی:

- container باقی‌مانده: صفر؛
- network باقی‌مانده: صفر؛
- rehearsal image باقی‌مانده: صفر؛
- temporary root باقی‌مانده: صفر.

Gateهای image reproducibility، secret scan، Compose isolation، non-root/read-only runtime، persistence، single-writer، migration idempotency، synthetic transport و rollback همگی PASS هستند.

مرحله بعد، Stage 4 است: capture پایدار، durable append، reconciliation، edit/delete/reply metadata و retention سه‌روزه. authority فعلی تا gate live و مجوز cutover دست‌نخورده می‌ماند.
