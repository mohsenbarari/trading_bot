# Staging Deployment

This staging environment runs beside production on the foreign/current host. It
uses a separate Docker Compose project, separate PostgreSQL and Redis volumes,
and a separate loopback API port.

Defaults:

- Domain: `http://staging.362514.ir`
- Compose project: `trading_bot_staging`
- API loopback port: `127.0.0.1:8100`
- Env file: `.env.staging`
- Nginx site: `/etc/nginx/sites-available/trading-bot-staging`
- Basic Auth file: `/etc/nginx/.htpasswd-trading-bot-staging`

Staging is intentionally isolated from production sync. The default compose file
does not start `sync_worker`, and `.env.staging` leaves peer URLs empty.

The public staging site is protected with Basic Auth. Credentials are generated
in `.env.staging` as `STAGING_BASIC_AUTH_USER` and
`STAGING_BASIC_AUTH_PASSWORD`. The Nginx staging site also injects the staging
`DEV_API_KEY` only for `/api/auth/dev-login`, so the frontend dev-login flow can
be used for manual testing after Basic Auth succeeds.

Common commands:

```bash
scripts/deploy_staging.sh check
scripts/deploy_staging.sh deploy
scripts/deploy_staging.sh ps
scripts/deploy_staging.sh health
scripts/deploy_staging.sh logs
```

The bot is disabled by default. Start it only with a dedicated staging bot token:

```bash
STAGING_ENABLE_BOT=1 scripts/deploy_staging.sh deploy
```

Do not use the production Telegram bot token in `.env.staging`.
