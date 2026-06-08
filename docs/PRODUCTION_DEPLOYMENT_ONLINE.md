# Production Deployment v1 — Foreign-Controlled Release Flow

This document defines the first production release flow driven from the foreign server.

## Scope

This is intentionally the **online-first** version for Iran:

- the full release starts on the foreign server
- the foreign server is deployed first
- the operator is then asked whether Iran currently has working internet
- if the answer is `yes`, the Iran-online flow runs
- if the answer is `no`, the script stops because the Iran-offline flow is not implemented yet
- frontend, Python wheel cache, and Docker images are prepared on the foreign server
- Docker images are transferred to Iran over SSH and loaded there
- the Iran server does **not** build app images or pull runtime images during deployment

The later offline scenario should extend this with a dedicated Iran-offline path.

## Files

| File | Purpose |
| --- | --- |
| `scripts/production_deploy_online.sh` | Main production release script |
| `deploy/production/online.env.example` | Example deployment manifest |
| `deploy/production/nginx-iran-online.conf.template` | Templated Nginx config for the Iran host |

## Manifest

Create a private manifest by copying the example:

```bash
cp deploy/production/online.env.example /root/secure-envs/trading-bot/online.env
```

Then fill:

- foreign and Iran public IP/domain pairs
- SSH host/user/port
- SSH auth mode and key path or password
- target project directory on the Iran server
- public Iran domain
- certbot email
- path to the private runtime `.env` file that should become `IRAN_PROJECT_DIR/.env`

The script also updates `/etc/hosts` on both servers so the foreign and Iran domains resolve to the expected internal IPs during sync and runtime traffic. That matters because the sync worker still resolves peer URLs from the configured server domains.

## Primary command

The normal path is one command on the foreign server:

```bash
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env
```

Equivalent Make wrapper:

```bash
make production-release MANIFEST=/root/secure-envs/trading-bot/online.env
```

## Full flow

### 1. Local validation
- validates local tools (`ssh`, `scp`, `rsync`, `docker`, `npm`, `python3`)
- validates the manifest
- checks SSH connectivity to the Iran server

### 2. Local build + local foreign deploy
- builds the frontend on the foreign server
- prepares Python wheel cache
- builds:
  - `trading_bot_base`
  - `trading_bot_base_iran`
- prepares a loadable Docker bundle containing:
  - `trading_bot_base_iran`
  - `postgres:15-alpine`
  - `redis:7-alpine`
- deploys the foreign server locally

### 3. Iran scenario question
- asks whether the Iran server currently has working internet access
- if `no`, the script stops after the foreign deploy
- if `yes`, it continues with the Iran-online flow

### 4. `bootstrap-iran`
- installs baseline packages
- installs Docker if missing
- installs Nginx, Certbot, and `python3-certbot-nginx`
- attempts to install Docker Compose plugin
- creates deploy directories
- applies host timezone

### 5. `/etc/hosts` sync
- writes a managed hosts block on the foreign server
- writes the same managed hosts block on the Iran server
- keeps the foreign and Iran domains mapped to their production IPs for sync and runtime calls

### 6. `sync-project`
- rsyncs the production payload to the Iran server
- copies the private runtime `.env` file

### 7. `configure-nginx`
- renders a domain-aware Nginx config from the template
- copies it to `/etc/nginx/sites-available/trading-bot`
- enables the site and reloads Nginx

### 8. `issue-cert`
- runs `certbot --nginx -d <domain>`
- can be skipped with `IRAN_SKIP_CERTBOT=1`

### 9. `ship-images` + `load-images`
- uploads the prepared Docker image bundle to the Iran host
- runs `docker load` on the Iran host

### 10. `deploy-iran`
- runs `docker compose -f docker-compose.iran.yml up -d --wait`
- does **not** build the image remotely

### 11. `healthcheck`
- checks the local API endpoint on the Iran host
- optionally checks the public HTTPS endpoint

## Known limitations of v1

1. SSL still depends on live internet and ACME reachability from the Iran server.
2. The Iran-offline branch is not implemented yet.
3. The flow does not yet create immutable rollback releases.
4. The compose file still uses bind mounts, so the runtime payload is synced alongside the loaded image.
5. Firewall hardening is optional and conservative.
6. The script assumes Debian/Ubuntu style package management.
7. For SSH password auth, `sshpass` must be installed on the foreign server.

## Next step after this test phase

After the temporary two-server test succeeds, the next iteration should add:

1. immutable release artifacts
2. rollback support
3. secret/cert separation from project sync
4. compose/runtime cleanup to reduce bind-mounted code
5. DNS-based certificate issuance for the Iran-offline scenario
