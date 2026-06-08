# Production Deployment v1 — Iran Online Scenario

This document defines the first production deployment flow for the case where the Iran server is reachable from the foreign server over SSH and still has internet access.

## Scope

This is intentionally the **online-first** version:

- orchestration runs on the foreign server
- the Iran server is bootstrapped over SSH
- SSL is requested directly on the Iran server with `certbot`
- frontend is built on the foreign server
- the production payload is rsynced to the Iran server
- the Docker image is still built on the Iran server

The later offline scenario should replace the remote build/pull steps with shipped release artifacts.

## Files

| File | Purpose |
| --- | --- |
| `scripts/production_deploy_online.sh` | Main deployment helper |
| `deploy/production/online.env.example` | Example deployment manifest |
| `deploy/production/nginx-iran-online.conf.template` | Templated Nginx config for the Iran host |

## Manifest

Create a private manifest by copying the example:

```bash
cp deploy/production/online.env.example /root/secure-envs/trading-bot/online.env
```

Then fill:

- SSH host/user/port
- target project directory on the Iran server
- public Iran domain
- certbot email
- path to the private runtime `.env` file that should become `IRAN_PROJECT_DIR/.env`

## Commands

All commands run on the foreign server:

```bash
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env check-local
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env bootstrap-iran
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env configure-nginx
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env issue-cert
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env build-release
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env sync-project
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env deploy-iran
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env healthcheck
```

Or run the full sequence:

```bash
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env full
```

## What the script does

### `check-local`
- validates local tools (`ssh`, `scp`, `rsync`, `docker`, `npm`, `python3`)
- validates the manifest
- checks SSH connectivity to the Iran server

### `bootstrap-iran`
- installs baseline packages
- installs Docker if missing
- installs Nginx, Certbot, and `python3-certbot-nginx`
- attempts to install Docker Compose plugin
- creates deploy directories
- applies host timezone

### `configure-nginx`
- renders a domain-aware Nginx config from the template
- copies it to `/etc/nginx/sites-available/trading-bot`
- enables the site and reloads Nginx

### `issue-cert`
- runs `certbot --nginx -d <domain>`
- can be skipped with `IRAN_SKIP_CERTBOT=1`

### `build-release`
- builds the frontend locally
- prepares the Python wheel cache in `pip_packages/`

### `sync-project`
- rsyncs the production payload to the Iran server
- copies the private runtime `.env` file

### `deploy-iran`
- builds `trading_bot_base_iran` on the Iran host
- runs `docker compose -f docker-compose.iran.yml up -d --wait`

### `healthcheck`
- checks the local API endpoint on the Iran host
- optionally checks the public HTTPS endpoint

## Known limitations of v1

1. The Iran image is still built on the Iran server.
2. SSL still depends on live internet and ACME reachability from the Iran server.
3. The flow does not yet create immutable release bundles or rollbacks.
4. Firewall hardening is optional and conservative.
5. The script assumes Debian/Ubuntu style package management.

## Next step after this test phase

After the temporary two-server test succeeds, the next iteration should add:

1. immutable release artifacts
2. remote `docker load` instead of remote build
3. secret/cert separation from project sync
4. rollback support
5. DNS-based certificate issuance for the Iran-offline scenario

