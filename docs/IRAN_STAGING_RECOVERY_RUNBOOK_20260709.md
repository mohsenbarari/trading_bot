# Iran Staging Recovery Runbook - 2026-07-09

## Current Failure Shape

Observed from the foreign/orchestration host:

- `ssh -p 37067 root@62.220.124.174` connects at TCP level but resets before SSH key exchange:
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
a normal OpenSSH banner on `37067` and `ssh -p 37067 root@62.220.124.174 true`
from the foreign server must complete without the pre-kex reset.

2. Update the Iran staging release from the foreign/orchestration host.

`/srv/trading-bot/staging-iran` is an artifact receiver, not a Git checkout.
Do not run `git fetch`, `git switch`, or `git pull` in that directory. Prepare
the exact candidate revision on the orchestration host, then transfer the
runtime tree while protecting the receiver's environment and stateful paths:

```bash
cd /root/trading-bot/trading_bot
git switch candidate/webapp-ui-ux-unification
git pull --ff-only

rsync -az --delete -e 'ssh -p 37067' \
  --exclude='/.git/' \
  --exclude='/.github/' \
  --exclude='/.agents/' \
  --exclude='/.claude/' \
  --exclude='/.codex/' \
  --exclude='/.env*' \
  --exclude='/.venv/' \
  --exclude='/.vscode/' \
  --exclude='/.deploy_count' \
  --exclude='/__pycache__/' \
  --exclude='/app_logs/' \
  --exclude='/docs/' \
  --exclude='/frontend/' \
  --exclude='/tests/' \
  --exclude='/tmp/' \
  --exclude='/uploads/' \
  --exclude='/map_data/' \
  --exclude='/mini_app_dist*/' \
  ./ root@62.220.124.174:/srv/trading-bot/staging-iran/

rsync -az --delete -e 'ssh -p 37067' \
  mini_app_dist_staging/ \
  root@62.220.124.174:/srv/trading-bot/staging-iran/mini_app_dist_staging/
```

The excludes are release boundaries, not cleanup candidates. In particular,
the transfer must never replace `.env.staging`, uploads, temporary evidence,
or any database/Redis volume.

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
ssh -p 37067 root@62.220.124.174 true
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
