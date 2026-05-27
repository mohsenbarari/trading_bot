
# Runbook یکپارچه‌سازی Timezone و بازگشت Sync دو سرور

این runbook برای روزی است که ارتباط سرور ایران دوباره برقرار شود و بخواهیم هر دو سرور را با policy مشترک زمانی بالا بیاوریم، بدون اینکه بعد از deploy لازم باشد command دستی جداگانه‌ای برای sync worker یا recovery اجرا شود.

## Policy نهایی

- timezone هر دو host: `UTC`
- timezone سرویس‌های runtime و دیتابیس: `UTC`
- timezone منطق بازار و نمایش به کاربر: `Asia/Tehran`

در این repo این policy به دو بخش پیاده شده است:

1. `scripts/ensure_host_timezone.sh`
Host را فقط وقتی timezone اشتباه باشد اصلاح می‌کند.

2. `docker-compose.yml` و `docker-compose.iran.yml`
روی سرویس‌های runtime `TZ=UTC` و روی Postgres به‌صورت explicit `timezone=UTC` و `log_timezone=UTC` enforce شده است.

## Deploy behavior

- `deploy.sh foreign` قبل از deploy روی host آلمان timezone را چک می‌کند.
- `deploy.sh iran` قبل از deploy روی host ایران timezone را از طریق `ssh` چک می‌کند.
- `deploy.sh all` بعد از deploy هر دو سرور، `sync_worker` را روی هر دو سمت بالا می‌آورد و `scripts/recover_cross_server_sync.sh` را به‌صورت خودکار اجرا می‌کند.
- اگر timezone از قبل درست باشد، script هیچ تغییری اعمال نمی‌کند.

## Preflight روز reconnect

اگر ترجیح بدهی قبل از اولین full deploy یک بار وضعیت hostها را دستی ببینی:

روی هر دو سرور:

```bash
timedatectl status
```

اما برای اجرای واقعی لازم نیست command جداگانه‌ای برای timezone یا sync recovery بزنی؛ `make up` همه‌ی این مراحل را خودش انجام می‌دهد.

## ترتیب اجرا بعد از بازگشت اینترنت ایران

1. اگر هنوز در حالت single-server development mode هستی، آن mode موقت را خاتمه بده.
2. روی آلمان آخرین کد را pull کن.
3. full deploy را اجرا کن:

```bash
cd /root/trading-bot/trading_bot
make up
```

همین command باید این مراحل را خودکار انجام دهد:

1. build frontend
2. deploy ایران
3. deploy آلمان
4. check/fix host timezone روی هر دو سرور در صورت نیاز
5. start `sync_worker` روی هر دو سرور
6. replay `change_log` در هر دو جهت تا convergence

اگر این فاز خودکار به هر دلیل interrupt شد، فقط آن‌وقت `make sync-recover` به‌عنوان fallback دستی باقی می‌ماند.

## Validation checklist

### Host timezone

روی هر دو سرور:

```bash
timedatectl show --property=Timezone --value
date
date -u
```

خروجی مورد انتظار:

- timezone host: `UTC`
- `date` و `date -u` از نظر timezone label هم‌راستا با UTC باشند.

### Container timezone

روی آلمان:

```bash
docker compose exec app date
docker compose exec bot date
docker compose exec db date
docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'SHOW TIMEZONE;'
```

روی ایران:

```bash
docker compose -f docker-compose.iran.yml exec app date
docker compose -f docker-compose.iran.yml exec db date
docker compose -f docker-compose.iran.yml exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'SHOW TIMEZONE;'
```

خروجی مورد انتظار:

- containerها روی `UTC`
- `SHOW TIMEZONE;` در Postgres برابر `UTC`

### Application behavior

1. یک آفر یا معامله جدید بساز.
2. بررسی کن رکورد در هر دو سرور sync می‌شود.
3. UI و bot باید زمان را همچنان به‌صورت ایران/تهران نمایش بدهند.
4. logs و ordering بین دو سرور باید دیگر اختلاف timezone نداشته باشد.

## Rollback

اگر لازم شد host timezone را برگردانی:

```bash
sudo bash /root/trading-bot/trading_bot/scripts/ensure_host_timezone.sh Asia/Tehran
```

اگر فقط runtime را می‌خواهی نگه داری و host را موقتاً برگردانی، composeها همچنان Postgres و containerها را روی `UTC` enforce می‌کنند؛ بنابراین rollback host الزاماً rollback runtime نیست.