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

If the manifest file does not exist, the script will prompt for these values and create it in the repo path automatically.

The script also updates `/etc/hosts` on both servers so the foreign and Iran domains resolve to the expected internal IPs during sync and runtime traffic. That matters because the sync worker still resolves peer URLs from the configured server domains.

Both servers are forced to `UTC` during the release flow.

## Primary command

The normal path is one command on the foreign server:

```bash
scripts/production_deploy_online.sh --manifest /root/secure-envs/trading-bot/online.env
```

Equivalent Make wrapper:

```bash
make production-release MANIFEST=/root/secure-envs/trading-bot/online.env
```

`release` remains the production-authoritative path. Standalone subcommands are still available for controlled recovery work, but they now inherit the same runtime env observability validation through `check-local`, and the sampler-sensitive commands also install or verify the sync-health sampler explicitly.

## Full flow

### 1. Local validation
- validates local tools (`ssh`, `scp`, `rsync`, `python3`)
- auto-installs missing local `docker` / `npm` / `pip` packages on the foreign server when needed
- validates the manifest
- checks SSH connectivity to the Iran server
- detects Docker Compose command and CPU architecture on both servers before deploy
- if either env file is missing, prompts for the required values and creates both:
  - local foreign `.env`
  - Iran runtime `.env`
- blocks the release if the runtime env files still use observability placeholders or missing production-only values:
  - `TRUSTED_PROXY_CIDRS`
  - `OBSERVABILITY_TELEGRAM_USER_HASH_SALT`
  - non-local Grafana alert receivers / webhook / email targets
- blocks the release on a dirty git working tree because the payload sync uses local rsync, not only committed Git state
- use `IRAN_ALLOW_DIRTY_RELEASE=1` only for an intentional emergency deploy from uncommitted local files
- the same validation now runs before standalone deploy subcommands too, because `check-local` is shared by `deploy-foreign`, `bootstrap-iran`, `build-release`, `sync-project`, `deploy-iran`, and `healthcheck`

### 2. Local build + local foreign deploy
- first deploys the foreign server with host-native dependencies and compose command detection
- then builds the Iran artifacts after the foreign deploy completes
- prepares Python wheel caches per target architecture
- builds:
  - host-native foreign image path via `deploy.sh foreign`
  - `trading_bot_base_iran` for the detected Iran target architecture
- skips the frontend `npm ci` / `npm run build` step when the frontend source/config/env signature matches and `mini_app_dist/index.html` already exists
- skips the Iran Docker image build/save step when the prepared build context signature matches the existing local image bundle
- use `IRAN_FORCE_RELEASE_REFRESH=1` when the cached frontend, wheel, or image artifacts must be rebuilt deliberately
- `deploy.sh foreign` uses the same frontend and wheelhouse signatures so the foreign deploy step no longer rewrites the production wheelhouse hash on every run
- prepares a loadable Docker bundle containing:
  - `trading_bot_base_iran`
  - `postgres:15-alpine`
  - `redis:7-alpine`
- uses `buildx` for the Iran image when foreign and Iran architectures differ
- `deploy-foreign` also installs and verifies the local sync-health sampler when used standalone

### 3. Iran scenario question
- asks whether the Iran server currently has working internet access
- if `no`, the script stops after the foreign deploy
- if `yes`, it continues with the Iran-online flow

### 4. `bootstrap-iran`
- skips package transfer/install entirely when the Iran host already has the required Docker/Compose/Nginx/Certbot/Python tooling, Docker is healthy, Nginx is active, and timezone is UTC
- prepares a package bundle on the foreign server when foreign and Iran share the same Debian architecture
- invalidates and rebuilds that bundle automatically when the bootstrap package set changes
- transfers the bundle over SSH
- installs baseline packages on Iran from the transferred `.deb` files with `--no-download`
- installs `docker`, `docker compose`, `docker-compose`, `npm`, and `python3-pip` from the transferred bundle when needed
- if the offline apt install still fails in the Iran-online scenario, refreshes apt indices once and retries with `--fix-missing`
- when foreign and Iran architectures differ, skips the foreign-built `.deb` bundle and performs a retry-hardened direct apt install on Iran instead
- verifies that either `docker compose` or `docker-compose` is actually available before bootstrap is considered successful
- creates deploy directories
- forces UTC on the Iran host
- when used standalone, also installs and verifies the Iran sync-health sampler

### 5. `/etc/hosts` sync
- writes a managed hosts block on the foreign server
- writes the same managed hosts block on the Iran server
- keeps the foreign and Iran domains mapped to their production IPs for sync and runtime calls

### 6. `sync-project`
- rsyncs the production payload to the Iran server
- copies the private Iran runtime `.env` file
- syncs the prepared `pip_packages/` wheelhouse only when the remote `.requirements_hash` differs from the local requirements signature

### 7. `configure-nginx`
- renders a domain-aware Nginx config from the template
- copies it to `/etc/nginx/sites-available/trading-bot`
- enables the site and reloads Nginx

### 8. `issue-cert`
- runs `certbot --nginx -d <domain>`
- enables `certbot.timer` automatically when systemd provides it
- falls back to a cron-based `certbot renew` entry if `certbot.timer` is unavailable
- can be skipped with `IRAN_SKIP_CERTBOT=1`

### 9. `ship-images` + `load-images`
- uploads the prepared Docker image bundle only when the remote tar checksum differs or the tar is missing
- runs `docker load` only when the remote loaded-image signature differs or a required image tag is missing
- use `IRAN_FORCE_RELEASE_REFRESH=1` when a deliberate frontend rebuild, image rebuild, re-upload, or re-load is required despite matching signatures

### 10. `deploy-iran`
- detects whether Iran provides `docker compose` or `docker-compose`
- runs the available compose command with `-f docker-compose.iran.yml up -d --wait`
- the Iran `app` service now has its own HTTP healthcheck on `/api/config`, so `--wait` includes actual API readiness instead of only container start
- does **not** build the image remotely
- when used standalone, also installs and verifies the Iran sync-health sampler before service startup

### 11. Shared-table seed guard
- after the Iran stack is deployed, the release flow runs `scripts/inspect_shared_sync_state.py` inside the Iran migration container
- automatic current-state seed runs only when the Iran database is classified as `fresh`
- `IRAN_SHARED_DATA_MODE=reset` forces a confirmed backup/reset before seeding, even if the inspector classifies the host as fresh with only baseline/system rows
- `fresh` means the user/business/non-system chat signal tables are empty; baseline/system rows such as the mandatory channel or `market_runtime_state` do not by themselves block fresh classification
- if existing project data is detected, the script does not change Iran data automatically
- for existing data, choose one of:
  - `skip`: keep Iran database rows unchanged and continue deploy
  - `reset`: take a `pg_dump` backup, truncate shared tables plus `change_log`, then seed current shared-table state from foreign
  - `abort`: stop release without changing Iran data
- in the interactive prompt for existing data, pressing Enter uses the safe default `skip`
- non-interactive reset requires `IRAN_SHARED_DATA_MODE=reset` plus `IRAN_SHARED_RESET_CONFIRM=RESET_IRAN_SHARED_DATA`
- after a successful seed, the script marks pre-seed foreign shared backlog as synced up to the seed cutoff, clears only Iran seed-generated mandatory/system backlog, and requires both sync-health endpoints to report zero unsynced changes

Standalone commands:

```bash
make production-online-inspect-shared MANIFEST=/root/secure-envs/trading-bot/online.env
make production-online-seed-shared MANIFEST=/root/secure-envs/trading-bot/online.env
```

### 12. `healthcheck`
- retries the local API endpoint on the Iran host with backoff until it becomes ready
- optionally retries the public HTTPS endpoint with backoff until it becomes ready
- verifies that the sync-health sampler timer is installed and active on both foreign and Iran

### 13. Observability post-release operator steps
- install the audit anchor timer on the foreign host:
  - `make audit-anchor-monitor-install`
- verify the compact anchor export path:
  - default host file: `/var/lib/trading-bot-observability/audit-anchor.jsonl`
- install the audit anchor shipper when a restricted remote sink exists:
  - `make audit-anchor-ship-install`
- verify the remote or staged replication target:
  - example: `ops@foreign-audit.example:/srv/trading-bot-audit/audit-anchor.jsonl`
- render the current metrics collection contract for operators:
  - `make metrics-targets`
- treat Loki/log-based dashboards and alerts as the authoritative cross-service source until a future dedicated multi-surface metrics exporter exists

## Known limitations of v1

1. SSL still depends on live internet and ACME reachability from the Iran server.
2. The Iran-offline branch is not implemented yet.
3. The flow does not yet create immutable rollback releases.
4. The compose file still uses bind mounts, so the runtime payload is synced alongside the loaded image.
5. Firewall hardening is optional and conservative.
6. The script assumes Debian/Ubuntu style package management.
7. For SSH password auth, `sshpass` must be installed on the foreign server.
8. Iran bootstrap currently assumes the transferred package bundle contains all required `.deb` files for the target distro family.
9. The foreign host must have root-level access to install missing packages when `docker`, `npm`, or `pip` are absent.
10. S3 support has been removed from the production deploy flow and from the generated env files.
11. Existing Iran shared data still requires an operator decision; the script intentionally fails closed instead of guessing whether old data should be preserved or replaced.

## Next step after this test phase

After the temporary two-server test succeeds, the next iteration should add:

1. immutable release artifacts
2. rollback support
3. secret/cert separation from project sync
4. compose/runtime cleanup to reduce bind-mounted code
5. DNS-based certificate issuance for the Iran-offline scenario
