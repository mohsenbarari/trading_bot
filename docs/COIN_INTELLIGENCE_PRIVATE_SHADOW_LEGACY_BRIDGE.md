# پل موقت Shadow → مدل Legacy

این مسیر یک compatibility bridge یک‌طرفه و موقت است. Product authority در تمام مدت
`LEGACY` می‌ماند. سوییچ `PRIVATE_PRIMARY`، صف تلگرام، parser و الگوریتم مدل
تغییر نمی‌کنند.

## معماری

```
Shadow Market Store (فقط‌خواندنی)
        │
        ├─ PRIVATE_GOLD_CHANNEL
        ├─ PRIVATE_GOLD_PAPER_MINUTE
        ├─ GROUP_1
        └─ GROUP_2
        ▼
Legacy Market Store ──► coin-rate-engine-v8 ──► coin-rates.json
        │                                         │
        │                                         └── snapshot relay موجود
        ▼
conversation_events.sqlite3 ──► داشبورد estimator
```

داده از Legacy به Shadow برنمی‌گردد.

## منبع و مقصد

| نقش | منبع | مقصد |
|---|---|---|
| آبشده خصوصی و دقیقه کاغذی | Shadow `market_observations` | Legacy Market Store |
| گروه‌ها برای snapshot | همان Shadow | Legacy Market Store |
| گروه‌ها برای داشبورد | `project_group_market_to_estimator.py` | `conversation_events.sqlite3` |

## قواعد تک‌نویسنده

- قفل Market Store: `private-gold-live/staging/.market-store-writer.lock`
- قفل conversation: `estimator-live/.conversation-writer.lock`
- قفل خود bridge فقط برای جلوگیری از دو اجرای هم‌زمان bridge است
- دو قفل مقصد هرگز با هم گرفته نمی‌شوند
- Shadow فقط `mode=ro` باز می‌شود

## Allowlist فیلد

فقط فیلدهای مدل‌پذیر کپی می‌شوند: `event_key`، زمان رویداد و دسترسی، ابعاد بازار،
قیمت canonical تومان، کیفیت، revision، parser و hash مدل. متن خام، هویت،
شناسه پیام تلگرام و archive پژوهشی منتقل نمی‌شوند. واحد قیمت دوباره تبدیل
نمی‌شود.

`occurred_at`/`event_time_utc` و `available_at_utc` عیناً حفظ می‌شوند.

اجرای اول تمام ردیف‌های داغ را با cutoff هم‌تراز می‌کند. اجراهای بعدی فقط
پنجرهٔ افزایشی (آخرین watermark منبع به‌علاوهٔ ۱۲۰ ثانیه همپوشانی) را می‌خوانند
تا lag زیر ۶۰ ثانیه بماند. حذف/بازنشستگی فقط برای ردیف‌هایی است که در همان
پنجره باید دیده می‌شدند ولی دیگر در منبع نیستند.

## عملیات systemd

واحدها:

- `coin-private-shadow-legacy-estimator-bridge.service`
- `coin-private-shadow-legacy-estimator-bridge.timer`

نصب release-bound:

```bash
PRIVATE_SHADOW_LEGACY_BRIDGE_CONFIRM=install-private-shadow-legacy-estimator-bridge \
PRIVATE_SHADOW_LEGACY_BRIDGE_RELEASE_SHA=<exact-sha> \
PRIVATE_SHADOW_LEGACY_BRIDGE_ACTIVATE_TIMER=0 \
scripts/install_private_shadow_legacy_estimator_bridge.sh
```

Timer حدود ۱۵ ثانیه پس از پایان اجرای قبلی مسلح می‌شود. نصب اولیه timer را
روشن نمی‌کند.

## مشاهده‌پذیری

Heartbeat بدون payload:

`/srv/trading-bot/production-data/coin-intelligence/private-shadow-legacy-bridge/health.json`

شامل schema، SHA دقیق، شمارش projected/updated/unchanged/removed، watermark،
lag و دلیل شکست است.

همین اجرای release-bound پس از projection موفق گروه‌ها، وضعیت استاندارد
`COIN_GROUP_PROJECTION` را نیز به‌صورت اتمیک در فایل زیر به‌روز می‌کند:

`estimator-live/conversation/group-event-health.json`

جزئیات این probe فقط زمان واقعی جدیدترین رویداد canonical و جدیدترین رویداد
واجد شرایط هر گروه و شمارش‌های کنترل کیفیت را دارد. زمان رویداد از ساعت اجرای
bridge ساخته نمی‌شود. اگر projection شکست بخورد probe نیز `FAILED` می‌شود؛ در
نتیجه داشبورد دیگر heartbeat گیرندهٔ Legacy خاموش را به‌عنوان دریافت زنده نشان
نمی‌دهد. در زمان اجرای بعدی، آخرین جزئیات موفق تا commit کامل projection حفظ
می‌شود تا کارت‌های صفحه میان دو چرخه موقتاً خالی نشوند.

## پشتیبان و بازگردانی

قبل از اولین نوشتن تولید، از هر دو مقصد با SQLite online backup API نسخه گرفته
می‌شود. Restore فقط وقتی مجاز است که فساد یا projection برگشت‌ناپذیر ثابت شود.

ابزار: `scripts/backup_private_shadow_legacy_estimator_inputs.py`

یک نسخهٔ verified و فقط‌root روی wa-fi نگه داشته می‌شود. Receipt فقط digest،
شمارش جدول و وضعیت `quick_check` دارد؛ متن خام یا secret ندارد.

اجرای افزایشی watermark هر منبع را جدا نگه می‌دارد تا منبع کندتر پشت منبع
سریع‌تر نماند. Ledger جدا از مقصد است و payload ندارد.

## Rollback

1. stop/disable timer bridge
2. صبر تا oneshot تمام شود
3. در صورت نیاز timerهای قدیمی را دوباره enable کن
4. ردیف‌های projected را با ledger غیرفعال کن؛ کل DB را عوض نکن
5. Shadow بدون تغییر ادامه می‌دهد
6. Product mode همان `LEGACY` می‌ماند

```bash
python3 scripts/rollback_private_shadow_legacy_estimator_bridge.py \
  --legacy-market-store <legacy-store> --ledger <ledger>
```

## بازنشستگی پس از cutover PRIVATE_PRIMARY

پس از `PRIMARY_COMMITTED` و سوییچ رسمی Product، این bridge را stop/disable کن،
ledger را نگه دار، و collector/state قدیمی را حذف نکن تا rollback Product-only
ممکن بماند.

## تفاوت رویداد parseشده، fact واجد شرایط، و ورودی مدل

- رویداد parseشده: خروجی parser/archive؛ ممکن است رد یا قرنطینه شود
- fact واجد شرایط: `quality_state=ELIGIBLE` در Market Store
- ورودی مدل: subset همان factها که از گیت تأخیر، شرطی بودن، پیوند معامله و
  outlier عبور کرده‌اند و `realtime_eligible` هستند

## Catch-up تاریخی

مرز `2026-08-25T09:33:00Z` برای backfill رسمی capture است، نه برای ساخت رویداد
مصنوعی. داده باید مسیر capture → parse → archive → ACK → Market Store → bridge
را طی کند.
