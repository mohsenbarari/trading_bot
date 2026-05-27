# Runbook یکپارچه‌سازی Timezone و بازگشت Sync دو سرور

این runbook برای روزی است که ارتباط سرور ایران دوباره برقرار شود و بخواهیم هر دو سرور را با policy مشترک زمانی بالا بیاوریم.

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
- اگر timezone از قبل درست باشد، script هیچ تغییری اعمال نمی‌کند.

## Preflight روز reconnect

روی سرور آلمان:

```bash
timedatectl status
sudo bash /root/trading-bot/trading_bot/scripts/ensure_host_timezone.sh UTC
```

روی سرور ایران:

```bash
timedatectl status
sudo bash /root/trading-bot/trading_bot/scripts/ensure_host_timezone.sh UTC
```

اگر ترجیح بدهی تغییر host timezone فقط از مسیر deploy انجام شود، اجرای دستی script لازم نیست؛ `deploy.sh` خودش guard را اجرا می‌کند.

## ترتیب اجرا بعد از بازگشت اینترنت ایران

1. اگر هنوز در حالت single-server development mode هستی، آن mode موقت را خاتمه بده.
2. روی آلمان آخرین کد را pull کن.
3. deploy سرور ایران را اجرا کن:

```bash
cd /root/trading-bot/trading_bot
make iran
```

4. deploy سرور آلمان را اجرا کن:

```bash
cd /root/trading-bot/trading_bot
make foreign
```

5. sync worker را روی هر دو سرور فعال/بالا بیاور:

روی آلمان:

```bash
cd /root/trading-bot/trading_bot
docker compose --profile disabled up -d sync_worker
```

روی ایران:

```bash
cd /root/trading-bot/trading_bot
docker compose -f docker-compose.iran.yml up -d sync_worker
```

6. backlog sync را replay کن:

```bash
cd /root/trading-bot/trading_bot
make sync-recover
```

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

اگر فقط runtime را می‌خواهی نگه داری و host را موقتاً برگردانی، composeها همچنان Postgres و containerها را روی `UTC` enforce می‌کنند؛ بنابراین rollback host الزاماً rollback runtime نیست.# Runbook یکپارچه‌سازی Timezone و بازگشت Sync دو سرور

این runbook برای روزی است که ارتباط سرور ایران دوباره برقرار شود و بخواهیم هر دو سرور را با policy مشترک زمانی بالا بیاوریم.

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
- اگر timezone از قبل درست باشد، script هیچ تغییری اعمال نمی‌کند.

## Preflight روز reconnect

روی سرور آلمان:

```bash
timedatectl status
sudo bash /root/trading-bot/trading_bot/scripts/ensure_host_timezone.sh UTC
```

روی سرور ایران:

```bash
timedatectl status
sudo bash /root/trading-bot/trading_bot/scripts/ensure_host_timezone.sh UTC
```

اگر ترجیح بدهی تغییر host timezone فقط از مسیر deploy انجام شود، اجرای دستی script لازم نیست؛ `deploy.sh` خودش guard را اجرا می‌کند.

## ترتیب اجرا بعد از بازگشت اینترنت ایران

1. اگر هنوز در حالت single-server development mode هستی، آن mode موقت را خاتمه بده.
2. روی آلمان آخرین کد را pull کن.
3. deploy سرور ایران را اجرا کن:

```bash
cd /root/trading-bot/trading_bot
make iran
```

4. deploy سرور آلمان را اجرا کن:

```bash
cd /root/trading-bot/trading_bot
make foreign
```

5. sync worker را روی هر دو سرور فعال/بالا بیاور:

روی آلمان:

```bash
cd /root/trading-bot/trading_bot
docker compose --profile disabled up -d sync_worker
```

روی ایران:

```bash
cd /root/trading-bot/trading_bot
docker compose -f docker-compose.iran.yml up -d sync_worker
```

6. backlog sync را replay کن:

```bash
cd /root/trading-bot/trading_bot
make sync-recover
```

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