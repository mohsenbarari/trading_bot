# WebApp-IR Dark Standby Runbook

Status: data-only emergency readiness path; no writer promotion, CDN/DNS
mutation, public ingress, or three-site production cutover is authorized

## Purpose And Boundary

This runbook prepares the current production WebApp release and an encrypted
WebApp-FI snapshot on the Iran host while the full three-site architecture
continues independently. It reduces recovery time but does not claim continuous
replication or transparent failover.

Validate a private copy of
`deploy/production/webapp-ir-dark-standby.env.example` before every run:

```bash
python3 scripts/verify_webapp_ir_dark_standby_manifest.py \
  --manifest /root/secure-envs/trading-bot/webapp-ir-dark-standby.env \
  --check-files --json
```

The fixed safety boundary is:

- WebApp-FI remains the active public WebApp writer.
- WA-IR remains dark: no public Nginx site, CDN/DNS target, `app`,
  `sync_worker`, background jobs, or writer promotion.
- PostgreSQL may run on WA-IR only to receive and validate a restored snapshot.
- Redis may be retained as backup evidence but is never restored into standby.
- Bulk release/snapshot bytes use private Arvan Object Storage. SSH is limited
  to control commands and small administrative files.
- Every bundle is age-encrypted before leaving Finland. WA-IR receives only a
  short-lived presigned HTTPS URL, never the S3 HMAC credential.
- Ciphertext SHA-256, object version id, release SHA, source manifest, and
  restore evidence are retained for every run.

The Arvan bucket remains private and versioned. Live tests showed that Arvan's
advertised Public Access Block and default bucket SSE controls cause its data
plane to reject `PutObject`. Those controls are absent, so client-side
encryption is a mandatory gate rather than optional defense.

## Domain Safety Boundary

The two DNS namespaces are intentionally separate:

| Purpose | Root domain | Current WebApp host |
| --- | --- | --- |
| Production application | `gold-trade.ir` | `coin.gold-trade.ir` in the current production manifest |
| CDN/failover validation | `gold-trading.ir` | `app.gold-trading.ir` |

A read-only Arvan API check on 2026-07-19 showed only `gold-trading.ir` enrolled
and active in the current CDN account. Both `app.gold-trading.ir` and the
isolated `switch-test.gold-trading.ir` record remained proxied to WebApp-FI
`65.109.220.59`. No CDN or DNS mutation was made.

All origin-switch drills are test-only. The origin-switch tool rejects applied
changes for `gold-trade.ir` before any API request. Enrolling or routing the
production namespace is a separate post-Full-Matrix change with fresh
snapshots, explicit approval, rollback evidence, and a stability window. Test
results never authorize a production-domain change by themselves.

## Time Contract

Every host, container, PostgreSQL instance, log, manifest, and object key uses
UTC. The application renders user time explicitly with `Asia/Tehran`; server
timezone must never be changed to affect display.

| Role | Address | Required timezone |
| --- | --- | --- |
| Bot-FI | `65.109.216.187` | `UTC` |
| WebApp-FI | `65.109.220.59` | `UTC` |
| WA-IR dark standby | `185.206.95.250` | `UTC` |
| Primary Witness | `185.206.95.94` | `UTC` |
| Transitional Witness | `185.231.182.6` | `UTC` |

## Ordered Procedure

### 1. Preflight

1. Require clean `main` at the exact SHA reported by both production runtimes.
2. Validate the private dark-standby manifest.
3. Prove source/target are different hosts, Object Storage uses HTTPS, URL
   lifetime is at most 900 seconds, and all activation flags are false.
4. Confirm all five hosts report UTC and synchronized NTP.
5. Confirm WA-IR has no public 80/443 listener and retains default-deny ingress.

Stop on release mismatch, dirty source, missing encryption identity, public
routing drift, active WA-IR application process, or failed private-bucket probe.

### 2. Bootstrap WA-IR From Iran

Install Docker Engine, Compose v2, age, curl, CA certificates, PostgreSQL client
tools, Nginx, and basic monitoring directly from the Iran host's configured
mirrors. Create `/srv/trading-bot/{current,releases,backups,dark-standby}` with
safe root ownership, force UTC, and retain UFW default-deny ingress. Package
downloads are not tunneled through SSH; SSH only starts and verifies local work.

### 3. Deliver The Exact Release

Build the Iran image and frontend from the exact production `main` SHA on
Bot-FI. Record SHA-256 values for source, frontend, and Docker image archives.
Encrypt the bundle with age and upload it under:

```text
dark-standby/releases/<release-sha>/<ciphertext-sha256>.tar.age
```

WA-IR downloads directly through a presigned GET, verifies ciphertext before
decryption, rejects unsafe archive paths, installs an immutable release, and
loads the images. Compose remains stopped.

### 4. Deliver The Snapshot

Use `scripts/run_production_backup.py` against WebApp-FI for PostgreSQL,
uploads, and audit data. Redis is excluded from restore. Package the generated
manifest and files, encrypt, and upload under:

```text
dark-standby/snapshots/<UTC-stamp>/<ciphertext-sha256>.tar.age
```

The small temporary files may move between the two Finland hosts; no bulk
payload crosses the Finland-Iran SSH link.

### 5. Restore Without Activating The Application

1. Download through a fresh presigned URL and verify the ciphertext hash.
2. Decrypt into a root-only staging directory and validate archive members.
3. Start only WA-IR PostgreSQL with compatible database identity.
4. Restore into an empty target database and restore uploads into its volume.
5. Do not restore Redis.
6. Keep `app`, `sync_worker`, Nginx public ingress, and writer lease stopped.

Always combine the production Iran Compose file with
`deploy/production/docker-compose.webapp-ir-dark-standby.yml`. The overlay puts
`app`, `sync_worker`, `migration`, and Redis behind the
`activation-forbidden` profile; an ordinary data-ready start can therefore
bring up only PostgreSQL.

The current API startup performs rollout writes even when background jobs are
disabled. Starting it is therefore not a valid parity probe in this phase.

### 6. Evidence

Record host identities, release/artifact hashes, backup manifest, S3 key and
version id (never a presigned URL), restore status, table/schema counts, upload
manifest, stopped service state, UFW/listeners, UTC/NTP, disk, and memory.

A refresh must restore into a new empty database/volume or use the later
incremental DR protocol. Never replay a full dump over a database that accepted
local writes.

## Promotion Boundary

This procedure ends at `data-ready-dark`. Promotion still requires witness
enforcement, current-main integration, three-site event/file recovery, Failure
Matrix, final delta and parity, operator approval, and controlled Arvan routing.
