# TOPQ-ADR-06 — UX، RACI، ایمنی تست و go-live

- وضعیت: Accepted
- تاریخ: `2026-07-16`
- owner: Product + Operations + Security
- چالش‌ها: `TOPQ-C03`, `TOPQ-C48`, `TOPQ-C53`, `TOPQ-C54`, `TOPQ-C61`, `TOPQ-N03..N18`

## تصمیم UX

- شکست Telegram پس از commit پیام غیرعادی صف/retry به کاربر نشان نمی‌دهد.
- Bot همان preview را با یک `editMessageText` به success + «لفظ شما» و دکمه انقضا تبدیل می‌کند؛ `sendMessage` خصوصی دوم حذف می‌شود. WebApp snapshot رفتار `main` است.
- پیام و anchor authenticated تا حد امکان حذف نمی‌شوند؛ context keyboard در success/error/cancel/timeout به منوی persistent بازمی‌گردد و `/start` برای بازیابی لازم نیست. inline keyboard stale همچنان حذف می‌شود.

## عملیات و RACI

- Product مالک semantics/SLO/go-no-go، Backend مالک schema/worker، Operations مالک alert/credential/run و Security مالک allowlist/privacy است.
- alert بحرانی حداکثر یک دقیقه تحویل و ده دقیقه acknowledge می‌شود. نام افراد در manifest همان run الزامی است.
- pause/resume فقط break-glass و stop/circuit-break مسیر عادی است.
- support با public offer id به admin view state/age/error دسترسی دارد؛ queue internals به کاربر افشا نمی‌شود.

## ایمنی staging و داده

- `max_active_offers=10` فقط Iran staging، sync/readback foreign و default/production برابر ۴ است.
- fixture production فقط sampler read-only، non-PII و artifact پاک‌سازی‌شده است؛ runner staging اتصال production ندارد.
- pool هشتادحسابی و جریان کامل E2E فقط Test DC است. main-DC staging برای کالیبراسیون از primary/editor/channel و مقصدهای allowlisted با workload replay استفاده می‌کند؛ هر دو token از production متمایز و در artifact redacted هستند.
- artifactها AI-readable، redacted و checksumدارند؛ cleanup پس از export فقط برای `run_id` و خارج measurement است.
- terminal jobها ۳۰ روز hot، payload/error خام redacted هفت روز، aggregate غیرهویتی ۱۸۰ روز و unresolved تا سی روز پس از resolution نگهداری می‌شوند.

## go-live و stop condition

حداقل ده دور ده‌دقیقه‌ای و یک endurance شصت‌دقیقه‌ای staging بدون critical breach لازم است. پیش از آن smoke cross-bot edit، readback مجوز editor و benchmark A/B طبق `TOPQ-ADR-07` باید پاس شوند. سپس shadow planner حداکثر ۲۴ ساعت بدون send اجرا می‌شود. duplicate، stale publish، مقصد/credential اشتباه، fallback ناخواسته، job گم‌شده، sync mismatch یا عبور SLO stop فوری است. production deploy فقط با دستور صریح جداگانه مجاز است.

## گزینه‌های ردشده

- نمایش queue/error به کاربر، حذف anchor، استفاده کاربر production در تست، تغییر credential زنده برای fault injection، cleanup با SQL بین peerها و canary با بار مصنوعی production.

## داده، sync، failure mode و feature flag

این ADR schema کسب‌وکار را تغییر نمی‌دهد؛ metadata مربوط به run، retention و audit در migration افزایشی ADR-02 نگهداری می‌شود. تنظیم `max_active_offers=10` فقط از authority Iran staging sync می‌شود. fingerprint نامعتبر محیط، نبود owner، secret leak، observer gap یا cleanup ناقص اجرای زنده را fail-closed متوقف می‌کند. queue، shadow planner و keyboard changes همگی flag مستقل و پیش‌فرض خاموش دارند.

## تست، observability و rollback

snapshot Bot/WebApp، keyboard/anchor، preflight محیط، secret scan، cleanup readback، support drill، pause/resume و canary matrix تست می‌شوند. rollback مطابق ADR-05 است و هیچ داده یا پیام production در staging لمس نمی‌شود.

## الحاقیه ممیزی `2026-07-18`

- policy retention باید worker batch/idempotent، legal-hold rule، metric و تست واقعی داشته باشد؛ وجود `payload_redacted_at` بدون writer کافی نیست.
- گزارش عملیات باید backlog/oldest age، ambiguous/unresolved، blocked bot/gateway/destination، outcome inbox lag، reconciliation و stop threshold را machine-readable و AI-readable ارائه کند.
- log به‌جای dedupe/idempotency خام از job id یا correlation hash استفاده می‌کند و bundle نهایی با secret/PII scanner generic بررسی می‌شود.
- label محیط تست باید صریحاً `test` باشد و test harness هر network/provider call واقعی را fail کند.
- policy relink برای هر خانواده پیام، به‌ویژه trade/admin/account، پیش از Stage 4 ثبت و تست می‌شود.

## الحاقیه resume بات و gateway `2026-07-19`

- resume مستقیم Redis مجاز نیست. فرمان break-glass فقط روی foreign، با scope صریح، request-id یکتا، actor الزامی و عبارت تأیید exact اجرا می‌شود.
- saga ابتدا درخواست را در PostgreSQL journal می‌کند، full channel permission preflight همان credentialهای فعال را می‌گیرد، jobهای blocked را یک‌بار آزاد می‌کند، سپس Redis را پاک و در پایان gate را active می‌کند.
- قطع میان DB و Redis در حالت `database_applied` باقی می‌ماند؛ اجرای همان request-id شناسه jobهای ثبت‌شده را reuse می‌کند و درخواست متفاوت conflict می‌گیرد. 429 پیش‌پرواز deadline پایدار می‌سازد و retry پیش از آن fail-closed است.
- actor فقط به‌صورت SHA-256، خطا فقط با class/reason کنترل‌شده و خروجی CLI بدون token، payload، chat id یا متن provider ثبت می‌شود.
