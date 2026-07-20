# TOPQ-ADR-01 — ظرفیت کانال، SLO و expiry

- وضعیت: Accepted؛ بند تعداد بات با `TOPQ-ADR-07` اصلاح شده است
- تاریخ: `2026-07-16`
- owner: Product + Backend + Operations
- چالش‌ها: `TOPQ-C21`, `TOPQ-N01`, `TOPQ-N02`

## تصمیم

- معماری یک کانال و یک پیام مستقل برای هر آفر حفظ می‌شود. execution چند credential فقط طبق `TOPQ-ADR-07` مجاز است: primary ناشر و editor صرفاً ویرایشگر همان کانال.
- پذیرش هدف `3 valid offer/s` و expiry فعلی دو دقیقه‌ای تغییر نمی‌کند.
- pending بودن publication عمر آفر را تمدید و admission را محدود نمی‌کند.
- deadline انتشار `expires_at - 5s` است. آفر terminal یا عبورکرده از deadline بدون ارسال stale به `SUPERSEDED/DISABLED` می‌رود.
- گیت production نیازمند publication صددرصد آفرهای واجد انتشار پیش از deadline، `p95<=10s`، `p99<=30s` و صفر miss صرفاً ناشی از backlog است.
- شکست هر معیار در Stage 4 نتیجه `NO-GO` دارد؛ SLO یا semantics خودکار ضعیف نمی‌شود.

## گزینه‌های ردشده

- تمدید expiry تا پس از publication: تازگی قیمت و semantics بازار را عوض می‌کند.
- backpressure یا رد آفر معتبر برای جبران کانال: هدف پذیرش را نقض می‌کند.
- batching، کانال دوم، publisher دوم یا paid broadcast خارج از scope قطعی محصول است؛ editor دومِ بدون حق انتشار فقط طبق `TOPQ-ADR-07` مجاز است.
- انتشار دیرهنگام آفر terminal: از نظر کسب‌وکار نادرست است.

## داده، migration و sync

`accepted_at`, `expires_at`, `publication_deadline_at` و علت `DISABLED/SUPERSEDED` باید قابل audit باشند. زمان مرجع دامنه از home server می‌آید؛ worker foreign مجاز به تغییر expiry یا status Offer نیست. migration فقط فیلدهای delivery را به‌صورت افزایشی اضافه می‌کند.

## failure mode و کنترل

- backlog رو به رشد: alert و `NO-GO`، نه حذف رکورد.
- 429 یا outage: retry طبق ADR-04 تا deadline؛ پس از آن no-op terminal.
- clock skew: deadline authoritative همراه intent sync می‌شود و با ساعت foreign بازنویسی نمی‌شود.

## تست و observability

- دور مرجع `1800` معتبر + `400` نامعتبر در ده دقیقه و burstهای `8..12/s`.
- سنجه‌ها: accepted rate، publication ratio، p50/p95/p99، oldest job، backlog، drain و backlog-caused disabled.
- acceptance: تمام معیارهای بالا و zero stale publication.

## feature flag و rollback

صف با flag پیش‌فرض خاموش اجرا می‌شود. rollback هیچ expiry را تغییر نمی‌دهد؛ claim متوقف، in-flight/ambiguous reconcile و ownership مطابق ADR-05 اتمیک به مسیر قبلی برمی‌گردد.

## الحاقیه ممیزی `2026-07-18`

بار مرجع فقط `3 publication/s` نیست. پس از warm-up انقضای دو‌دقیقه‌ای، نزدیک `3 terminal edit/s` و editهای partial نیز به همان مقصد کانال اضافه می‌شوند؛ demand ترکیبی برآوردی حدود `6.18 operation/s` است. interval پیش‌فرض `1.05s` و candidate `0.25s` روی کاغذ به‌ترتیب حدود `0.95` و `4 operation/s` ظرفیت می‌دهند. این محاسبه سقف واقعی Telegram را اثبات نمی‌کند، اما یک precondition اجباری برای Stage 4 است: calibration باید ظرفیت امن مقصد را قبل از دور `1800+400` ثابت کند؛ شکست، `NO-GO` و تصمیم صریح محصول است. بات ویرایشگر ظرفیت همان chat را خودکار افزایش نمی‌دهد.
