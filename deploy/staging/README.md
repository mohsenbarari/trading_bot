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
- Frontend dist: `mini_app_dist_staging` by default, separate from production
  `mini_app_dist`

Staging is intentionally isolated from production sync. The default compose file
does not start `sync_worker`, and `.env.staging` leaves peer URLs empty unless
the staging bot profile is enabled.

When `STAGING_ENABLE_BOT=1` is used, compose also starts an internal-only
`foreign_app` API service with `SERVER_MODE=foreign`. The public WebApp `app`
service stays `SERVER_MODE=iran`, and `scripts/deploy_staging.sh` points
`FOREIGN_SERVER_URL` and `GERMANY_SERVER_URL` to `http://foreign_app:8000`.
This lets WebApp requests against bot-owned offers forward to the foreign
authority and lets foreign-owned offers expire through the foreign expiry loop,
without exposing the foreign WebApp/API surface publicly.

For the real two-server staging topology, run the foreign host with
`STAGING_ENABLE_BOT=1 STAGING_FOREIGN_ONLY=1 STAGING_FOREIGN_PUBLIC_SURFACE_GUARD=1`.
In this mode the deploy starts only the foreign API, bot, and foreign sync worker
plus shared db/redis/migration services.
It intentionally does not start the Iran-mode `app` service on the foreign host,
so the foreign staging database cannot be polluted by local Iran-mode runtime
rows or source-sequence watermarks. Keep the public-surface guard enabled on this
host: setting it to `0` is valid only for the single-host staging topology where
the Iran-mode `app` runs locally; on a foreign-only host it routes public API
health traffic to an absent service and produces `502` responses.

The public staging site is protected with Basic Auth. Credentials are generated
in `.env.staging` as `STAGING_BASIC_AUTH_USER` and
`STAGING_BASIC_AUTH_PASSWORD`. The Nginx staging site also injects the staging
`DEV_API_KEY` only for `/api/auth/dev-login`, so the frontend dev-login flow can
be used for manual testing after Basic Auth succeeds. The staging frontend build
exposes this quick-login button when `STAGING_ENABLE_DEV_LOGIN=true`.

Staging also sets `TRUSTED_PROXY_CIDRS` by default to loopback plus the Docker
bridge private range used by the host Nginx to reach the app container. This is
required so trusted-proxy-aware request parsing records the real client IP from
Nginx headers instead of collapsing all requests to the Docker gateway address.
Override `STAGING_TRUSTED_PROXY_CIDRS` only when the staging network topology is
changed and the new proxy hop has been verified.

Staging frontend builds must never write to the production `mini_app_dist`
directory. `scripts/deploy_staging.sh` passes `FRONTEND_BUILD_OUT_DIR` to Vite,
serves `mini_app_dist_staging` from the staging Nginx site, and passes the same
directory into the staging Docker build. Override `STAGING_FRONTEND_DIST_DIR`
only with a project-local path that is not `mini_app_dist`.

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
