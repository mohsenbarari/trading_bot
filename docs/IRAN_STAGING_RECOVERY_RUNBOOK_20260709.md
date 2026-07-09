# Iran Staging Recovery Runbook - 2026-07-09

## Current Failure Shape

Observed from the foreign/orchestration host:

- `ssh -p 37067 root@87.107.3.22` connects at TCP level but resets before SSH key exchange:
  `kex_exchange_identification: read: Connection reset by peer`.
- `https://staging.gold-trade.ir/api/config` with a Bearer header reaches FastAPI and returns `200`.
- The same endpoint with Basic Auth returns an Nginx `500`.
- `https://staging.gold-trade.ir/api/sync/health` returns FastAPI `500`.

This means the Iran staging app is at least partially alive, but the host cannot
be remediated from the foreign server until SSH is restored. The Basic Auth
failure is consistent with an unreadable/missing htpasswd file or a stale
duplicate Nginx server block for `staging.gold-trade.ir`.

## Code-Side Hardening

Commit `93506b7e` hardened `scripts/deploy_staging.sh` so future staging Nginx
deploys:

- generate the Basic Auth file with the detected Nginx worker group;
- fail if the htpasswd file is empty or unreadable;
- disable duplicate enabled Nginx server blocks for the same staging domain
  before running `nginx -t`.

## Console Recovery Steps On Iran

Use the provider console or another already-trusted shell on the Iran server.
Do not run these commands on the production foreign host.

1. Restore SSH on port `37067`.

```bash
ss -ltnp | grep -E ':37067|:22' || true
journalctl -u ssh --since '2 hours ago' --no-pager | tail -160 || true
journalctl -u sshd --since '2 hours ago' --no-pager | tail -160 || true
grep -R --line-number -E '^(Port|ListenAddress|MaxStartups|AllowUsers|DenyUsers|Match|PermitRootLogin|PasswordAuthentication)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d 2>/dev/null || true
command -v fail2ban-client >/dev/null && fail2ban-client status sshd || true
nft list ruleset | sed -n '1,260p' || true
```

Fix the concrete cause found above. At minimum, the foreign host must receive
a normal OpenSSH banner on `37067` and `ssh -p 37067 root@87.107.3.22 true`
from the foreign server must complete without the pre-kex reset.

2. Update the Iran staging checkout.

```bash
cd /srv/trading-bot/staging-iran
git fetch origin
git switch candidate/webapp-ui-ux-unification
git pull --ff-only
```

3. Reinstall and redeploy Iran staging with the hardened script.

Preserve any existing staging-only `.env.staging` secrets. Do not copy
production `.env` into staging.

```bash
cd /srv/trading-bot/staging-iran
STAGING_DOMAIN=staging.gold-trade.ir \
STAGING_FRONTEND_URL=https://staging.gold-trade.ir \
STAGING_PROJECT_NAME=trading_bot_staging_iran \
STAGING_NGINX_SITE=trading-bot-staging-iran \
STAGING_ENABLE_BOT=0 \
STAGING_INTERNAL_FOREIGN_SERVER_URL=https://staging.362514.ir/foreign-sync \
STAGING_PUBLIC_FOREIGN_SYNC_URL=https://staging.362514.ir/foreign-sync \
STAGING_NGINX_DEDUPLICATE=1 \
scripts/deploy_staging.sh deploy
```

4. Validate Nginx and app health from Iran.

```bash
cd /srv/trading-bot/staging-iran
set -a
source .env.staging
set +a
nginx -t
curl -skS -u "$STAGING_BASIC_AUTH_USER:$STAGING_BASIC_AUTH_PASSWORD" https://staging.gold-trade.ir/api/config
curl -skS -H "Authorization: Bearer probe" https://staging.gold-trade.ir/api/config
curl -skS -H "X-Observability-Api-Key: $OBSERVABILITY_API_KEY" https://staging.gold-trade.ir/api/sync/health
```

5. Validate from the foreign/orchestration host.

```bash
cd /root/trading-bot/trading_bot
set -a
source .env.staging
set +a
ssh -p 37067 root@87.107.3.22 true
curl -skS -u "$STAGING_BASIC_AUTH_USER:$STAGING_BASIC_AUTH_PASSWORD" https://staging.gold-trade.ir/api/config
curl -skS -H "X-Observability-Api-Key: $OBSERVABILITY_API_KEY" https://staging.gold-trade.ir/api/sync/health
```

6. Run the two-server staging preflight.

```bash
cd /root/trading-bot/trading_bot
set -a
source .env.staging
set +a
STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM=execute-staging-two-server-full-matrix \
STAGING_OBSERVABILITY_API_KEY="$OBSERVABILITY_API_KEY" \
python3 scripts/run_staging_two_server_full_matrix.py \
  --mode preflight \
  --expected-branch candidate/webapp-ui-ux-unification \
  --expected-release-sha "$(git rev-parse --short=12 HEAD)"
```

## Success Criteria

- SSH to Iran on `37067` no longer resets before key exchange.
- Basic Auth and Bearer requests to `https://staging.gold-trade.ir/api/config`
  both return `200`.
- `https://staging.gold-trade.ir/api/sync/health` returns `200` and does not
  report missing/stale parity for a clean preflight baseline.
- `nginx -t` has no duplicate `staging.gold-trade.ir` server-name warnings.
- The two-server staging preflight passes before any mutating full matrix run.
