# Telegram delivery queue observability runbook

این runbook فقط برای `synthetic-test` و `staging` است. ابزار عمداً label یا اتصال `production` را نمی‌پذیرد، Telegram gateway را import نمی‌کند و transaction دیتابیس را پیش از نخستین query در حالت `READ ONLY` قرار می‌دهد.

## اجرای مرجع

آدرس PostgreSQL فقط از secret environment زیر خوانده می‌شود و نباید در command line، log یا artifact قرار گیرد:

```text
TELEGRAM_QUEUE_OBSERVABILITY_DATABASE_URL
```

اجرای synthetic روی دیتابیس scratch:

```bash
python scripts/report_telegram_delivery_queue_health.py \
  --environment synthetic-test \
  --expected-database-name telegram_queue_stage3_<name>_test \
  --run-id <synthetic-run-id> \
  --include-shadow \
  --report tmp/telegram-queue-evidence/health.json
```

اجرای staging همین قرارداد را با `--environment staging` و نام دیتابیسی که شامل `staging` و فاقد `prod` است استفاده می‌کند. محیط `production` در parser و validator رد می‌شود و این ابزار مسیر override ندارد.

## قرارداد گزارش و dashboard

گزارش JSON فقط aggregateها و labelهای محدود زیر را دارد:

- depth و oldest-ready-age بر پایه `priority × destination_class`؛
- depth بر پایه action، bot role و method allowlisted؛
- ingress، goodput و زمان تخمینی drain در پنجره نمونه؛
- ambiguity، unresolved، blocked، lease منقضی، freshness deadline ازدست‌رفته و terminal failure؛
- pending provider outcome و سن قدیمی‌ترین مورد؛
- تعداد jobهای دارای `429` و bucketهای `retry_after`؛
- runtime gate بر پایه scope/state، بدون gate key یا reason آزاد؛
- تصمیم `continue`, `warning` یا `stop` و reason codeهای محدود.

گزینه `--publish-metrics` aggregateهای همان snapshot را در registry پروژه با نام‌های زیر منتشر می‌کند:

- `trading_bot_telegram_delivery_queue_ready_depth`
- `trading_bot_telegram_delivery_queue_oldest_ready_age_seconds`
- `trading_bot_telegram_delivery_queue_condition_count`
- `trading_bot_telegram_delivery_queue_goodput_per_second`
- `trading_bot_telegram_delivery_queue_ingress_per_second`
- `trading_bot_telegram_delivery_queue_stop`

هیچ `chat_id`, `user_id`, destination، source، message id، token، متن payload یا dedupe خام label یا output نمی‌شود.

## آستانه‌های اولیه و رفتار تست

| سیگنال | warning | STOP اولیه |
| --- | ---: | ---: |
| ready depth | `>500` | `>5000` |
| oldest ready age | `>10s` | `>30s` |
| ambiguous / unresolved | ندارد | هر مقدار بیشتر از صفر |
| blocked | ندارد | هر مقدار بیشتر از صفر |
| freshness deadline missed | ندارد | هر مقدار بیشتر از صفر |
| terminal failure در پنجره نمونه | ندارد | هر مقدار بیشتر از صفر |
| expired lease | ندارد | `>10` |
| oldest pending provider outcome | ندارد | `>30s` |

این اعداد برای اجرای بدون fault هستند. زیرآزمون fault injection باید threshold مرتبط را فقط در manifest همان زیرآزمون override کند و بعد از بازیابی دوباره threshold اصلی را اعمال کند. کاهش SLO، حذف alert یا تغییر threshold در میانه trace ممنوع است.

`warning` تست را متوقف نمی‌کند ولی باید در time series ثبت شود. `stop` با exit code `2` ورودی جدید را متوقف می‌کند، row یا job را حذف نمی‌کند، drain/reconciliation را طبق runbook ادامه می‌دهد و run را `NO-GO` می‌سازد. exit code `3` یعنی اسکن security artifact شکست خورده و exit code `4/5` به‌ترتیب config یا خطای داخلی است.

## Shadow plan

`--include-shadow` حداکثر ۱۰۰ candidate هر lane را بدون `FOR UPDATE`, lease، dispatch marker یا gateway call مرتب می‌کند. خروجی فقط correlation hash یک‌طرفه دارد. این خروجی شاهد readiness و ترتیب است، نه پیش‌بینی قطعی dispatch؛ gateهای هم‌زمان Redis و raceهای بعد از snapshot فقط از ledger واقعی سنجیده می‌شوند.

Shadow در production توصیه یا پشتیبانی نمی‌شود. هدف آن قبل از تست نهایی، اثبات read-only بودن observer و آشکارکردن starvation/order غیرمنتظره در داده synthetic/staging است.

## Artifact و تحلیل agent

وقتی `--report` داده شود، فایل sibling با پسوند `.security-scan.json` نیز ساخته می‌شود. scanner محتوای فایل، binary و ZIP/TAR/GZIP تو‌در‌تو را fail-closed بررسی و هیچ match حساس را echo نمی‌کند. artifact با status غیر `clean` قابل zip، commit یا تحویل به agent بازبین نیست.

حداقل bundle هر run:

1. health JSON دوره‌ای؛
2. shadow JSON پیش از شروع و در پیک؛
3. acceptance/reconciliation report؛
4. security scan JSON برای هر artifact و archive نهایی؛
5. checksum manifest.

## پاسخ عملیاتی

- `oldest_ready_age_stop` یا رشد depth: ورودی جدید همان run متوقف، lane/gate time series بررسی و drain حفظ می‌شود.
- ambiguity/unresolved/provider-outcome age: ارسال مجدد کور ممنوع؛ reconciler و evidence اپراتوری بررسی می‌شود.
- blocked: scope بات، مقصد یا gateway از runtime gate خوانده و فقط با preflight/resume پایدار باز می‌شود.
- missed freshness: run بلافاصله `NO-GO` است؛ ارسال stale یا تغییر deadline برای نجات run ممنوع است.
- terminal failure: response class و lifecycle feedback بررسی می‌شود؛ حذف row یا بازنویسی نتیجه مجاز نیست.
- security scan failure: artifact قرنطینه و پیش از هر اشتراک‌گذاری بازتولید می‌شود.
