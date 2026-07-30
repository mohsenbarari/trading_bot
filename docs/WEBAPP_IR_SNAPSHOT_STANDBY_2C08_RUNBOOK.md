# WA-IR Snapshot Standby: Production 2c08

This is the compact continuity path for the currently deployed WebApp release:

- release: `2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5`
- database schema: `f2c7d8e9a0b1`
- source: WebApp-FI
- fenced target: WebApp-IR

It is not the later three-site application runtime and must not use a later
staging image or migration chain.

## Fixed Boundary

1. WebApp-FI creates a local PostgreSQL custom dump, an `uploads/` gzip
   archive, and, when requested, an `audit_trail/` gzip archive. The database
   role is short-lived and read-only; the volume archives are tar reads only.
2. `manage_webapp_ir_snapshot.py publish` age-encrypts each artifact and an
   upload-last commit manifest, then uses only private, versioned Arvan Object
   Storage. It never uses an FI-to-IR peer address, SCP, SFTP, rsync, or a
   direct HTTP receiver.
3. WebApp-IR consumes the exact object `VersionId`s, decrypts with its local
   root-only age identity, and validates hashes before `restore_webapp_ir_snapshot.py`
   creates new candidate DB/uploads volumes.
4. The only warm process is PostgreSQL on `network_mode: none`. No application,
   Redis, migration, `core.sync_worker`, Nginx upstream, or public write path
   starts during a refresh.
5. Promotion and public routing remain separate controlled operations. A ready
   snapshot alone cannot make WA-IR a writer.

The normal initial target is a verified candidate no more than 30 seconds old
from the actual PostgreSQL snapshot start. This is a measured service
objective, not a claim: both the Object Storage consumer and the final active
pointer reject a cycle that exceeds the configured bound, and the last
verified candidate remains intact.

## Prerequisites

- The Arvan bucket is private and has versioning enabled. Do not send
  provider-side SSE headers; the transport uses recipient-based `age` only.
- Create the WA-IR age identity locally. Keep its private identity under a
  root-only path on WA-IR and give WebApp-FI only the public recipient.
- The source has a dedicated database role with `CONNECT`, schema `USAGE`, and
  needed `SELECT` privileges, but no DML, DDL, ownership, superuser, role,
  database, replication, or bypass-RLS capability. Its root-only credential
  file contains `CAPTURE_DB_USER` and `CAPTURE_DB_PASSWORD`.
- Both hosts have a root-owned tool bundle containing the two local wrappers
  and `manage_webapp_ir_snapshot.py`, Python with `boto3`, Docker Compose v2,
  and `/usr/bin/age`. The tool bundle arrives via private/versioned Object
  Storage; it does not modify `/srv/trading-bot/current`.
- Create the standby data root on the dedicated large volume with `0700` root
  ownership. All transport workspaces, candidate bind-volume directories, and
  state receipts must live beneath it, never under `/tmp` or the system disk.

## Exact Release Bootstrap

WA-IR has no exact `2c08` application image. The available later three-site
staging images are not compatible substitutes, even with a source-tree bind
mount. Use a new immutable Object Storage bundle containing these local images:

```text
trading_bot_base:rollback-2c08da14-9ed63dd3e446
postgres:15-alpine
redis:7-alpine
```

The rollback app image is approximately 711 MB on Bot-FI. Save/load it only as
an age-encrypted, private/versioned Object Storage artifact with a recorded
digest and `VersionId`. Stage the exact Git/release bundle separately beneath
`/srv/trading-bot/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5`; it
provides the immutable `trading_settings.json` bind-mounted only after a future
promotion. Do not build, pull, or mount a later staging image on WA-IR.

## Root-Only Configuration

Use these tracked files only as schemas, then create root-only copies outside
Git:

- `deploy/production/webapp-ir-snapshot-standby-2c08.env.example`
- `deploy/production/webapp-fi-snapshot-publisher.env.example`
- `deploy/production/webapp-fi-snapshot-transport.json.example`
- `deploy/production/webapp-ir-snapshot-transport.json.example`

The WebApp-FI source env names only local capture paths and the publisher
config. The WebApp-FI transport config contains its scoped S3 credential, the
WA-IR public age recipient, and a new 32-byte raw WebApp-FI Ed25519 private
signing key. The WebApp-IR config contains its independent scoped S3
credential, local age identity, and the matching WebApp-FI public signing key.
Neither config contains a presigned URL, WebApp-FI TLS key, or WA-IR
application activation permission.

## Source Capture And Publish

Run the source wrapper on WebApp-FI from the S3-delivered tooling directory.
It creates the source artifacts with local Docker reads only, requires the
audit archive, and then invokes only the local immutable Object Storage
publisher. It does not use SSH, SCP, SFTP, rsync, a peer HTTP receiver, or any
other FI-to-IR transport. It leaves all application containers running.

```bash
python3 scripts/publish_webapp_fi_snapshot_standby.py \
  --source-env /root/secure-envs/trading-bot/webapp-fi-snapshot-publisher.env \
  --capture-script /srv/trading-bot-standby-tools/scripts/create_webapp_fi_snapshot_artifacts.py \
  --transport-script /srv/trading-bot-standby-tools/scripts/manage_webapp_ir_snapshot.py \
  --capture-python /usr/local/bin/python3 \
  --transport-python /usr/local/bin/python3 \
  --apply --json
```

The wrapper takes a root-only non-overlap lock, generates a new source
generation, passes the actual local capture timestamps and read-only client
lifetime to the publisher, and records a new root-only local receipt under
`state/published/`. It never reconstructs timestamps from shell variables.
Render and install
`deploy/production/webapp-fi-snapshot-publish.service.template` and
`deploy/production/webapp-fi-snapshot-publish.timer.template` only after their
placeholders are locally verified. The timer and both transport configs use the
same 15-30 second freshness bound, measured from the PostgreSQL snapshot start.

```bash
python3 scripts/publish_webapp_fi_snapshot_standby.py \
  --source-env /root/secure-envs/trading-bot/webapp-fi-snapshot-publisher.env \
  --capture-script /srv/trading-bot-standby-tools/scripts/create_webapp_fi_snapshot_artifacts.py \
  --transport-script /srv/trading-bot-standby-tools/scripts/manage_webapp_ir_snapshot.py \
  --capture-python /usr/local/bin/python3 \
  --transport-python /usr/local/bin/python3 \
  --timer-interval-seconds 15 --json
```

The command without `--apply` is a local plan only; it does not start Docker
capture or contact Object Storage. With `--apply`, the publisher creates
immutable age-encrypted database, uploads, audit, and upload-last manifest
objects, then reads back each returned `VersionId`. It neither overwrites nor
deletes a prior object. Every attempted local artifact set is deliberately
retained: do not add automatic cleanup before an explicit capacity and
recovery policy is reviewed.

## WA-IR Receive And Candidate Restore

The deterministic host interface is:

```bash
python3 scripts/refresh_webapp_ir_snapshot_standby.py \
  --standby-env /root/secure-envs/trading-bot/webapp-ir-snapshot-standby.env \
  --transport-script /srv/trading-bot-standby-tools/scripts/manage_webapp_ir_snapshot.py \
  --restore-script /srv/trading-bot-standby-tools/scripts/restore_webapp_ir_snapshot.py \
  --transport-python /usr/local/bin/python3 \
  --restore-python /usr/local/bin/python3 \
  --apply --json
```

It first consumes a new immutable candidate, then restores it into a new
generation-qualified DB/uploads volume. It atomically updates
`active-snapshot.json` only after PostgreSQL reports healthy, the restored
Alembic revision equals `f2c7d8e9a0b1`, and the actual
`source_db_snapshot_started_at` is still within the 30-second bound. The
matching staged candidate receives a root-only `snapshot-restore.json` marker;
only after a newer candidate is active is the prior marker atomically changed
to `active_pointer_state: "inactive"`, allowing bounded local transport
retention without deleting unknown state. The prior candidate volume remains
for rollback; its labelled database container is stopped only after the new one
is ready.

Install the two provided systemd templates only after locally rendering their
placeholders and validating their paths. The timer is intentionally 15 seconds
with a non-overlap lock. Its interval, transport configuration, and restore
configuration must all use the same 15-30 second freshness bound; the bound is
measured from DB snapshot start, not capture completion.

The data-ready health check is local and does not activate an app:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/srv/trading-bot-three-site-staging-data/wa-ir-standby/state/active-snapshot.json')
state = json.loads(p.read_text())
print(state['status'], state['alembic_revision'], state['candidate']['db_container'])
PY
docker inspect --format '{{.State.Health.Status}}' trading_bot_wa_ir_snapshot_db_REPLACE_GENERATION
```

Expected result: `ready`, revision `f2c7d8e9a0b1`, and `healthy`. The target
DB has no Docker network and no host port, so this check cannot serve users.

## Writer Witness Receipt Bridge

The normal refresh does not publish a Writer Witness receipt. Only after the
separate Writer Witness controller is installed with active-pointer binding
checks may the root-only standby env set:

```text
WA_IR_WITNESS_RESTORE_RECEIPT_PATH=/var/lib/trading-bot-three-site/snapshots/latest-restore-receipt.json
```

With that explicit setting, a successful restore first records the active
candidate (including exact DB/uploads/audit volume names), then requires
`audit.status: verified` and the matching nonempty candidate audit volume. It
builds a strict `gold-trade-snapshot-restore-receipt-v1` from the verified
transport receipt, strips the generic transport-only artifact `format` field,
and keeps the immutable manifest descriptor and exact Object Storage
`VersionId`s. The active pointer is atomically bound to the strict receipt's
canonical hash before the canonical latest receipt path is atomically replaced.

The bridge creates no runtime, routing, Witness lease, failover, or migration
action. A crash leaves either the prior receipt or a hash-bound pointer with no
matching new receipt; the independent promotion controller must reject the
latter. Do not enable this path until that controller enforces the pointer
binding and source-fencing policy.

## Bounded Emergency Term

The standby pointer remains strict: source snapshot start through verified
WA-IR restore readiness must take no more than 30 seconds.  Promotion permits
that verified pointer to age only through the bounded former-writer hand-off,
local snapshot DB stop, promoted runtime health check, listener reload, and
route change; it rejects any candidate older than 150 seconds at route time.
This is a refusal boundary, not an expected recovery point.

Use the root-only
`deploy/production/production-writer-lease-agent.webapp-ir.json.example` as
the shape of the WA-IR writer config.  Its `60/15/10` term is valid only when
the Witness service independently pins acquire and renew requests to 60
seconds.  The snapshot refresh env must set `WA_IR_WRITER_LEASE_FILE` once
that agent is installed; refresh checks it before download and again before
restore, so a live local WA-IR writer cannot have its selected candidate
replaced.

## TLS And Fenced Listener

Use the constrained DNS-01-only
`scripts/manage_three_site_mvp_arvan_acme_dns.py` hook on WA-IR after its
separate controlled installation. It keeps the Arvan DNS token and ACME state
root-only and creates the certificate key locally on WA-IR. Do not use HTTP-01
and do not copy the WebApp-FI certificate or key. Render
`deploy/production/nginx-webapp-ir-standby-dark-https.conf.template` with the
local certificate paths. Before promotion it returns `503` for every external
request; it has no upstream and no cross-site sync endpoint.

Only the independent promotion controller may replace this fenced listener
with the loopback-only `18000` promotion runtime after source fencing. For a
rollback, return this 503 listener first, preserve the candidate volumes, and
make no `current` or Object Storage deletion.

## Promoted Listener Activation Gate

The local promoted listener is a separate host-side gate.  It must complete
after the promoted application has passed its local health check and before
any public route change is attempted.  It has no Arvan, DNS, Object Storage,
SSH, or cross-host capability.

Create a root-only copy of
`deploy/production/webapp-ir-promoted-listener-2c08.env.example` outside Git.
The local TLS directory, certificate, and key must be owned by `root`, private
(`0700` for the directory and `0600` for both files), and generated locally on
WA-IR.  The immutable release root must end in the exact `2c08` release SHA,
and the configured Nginx enabled symlink must already target the dark-listener
site that will be replaced.  The receipt directory must already exist as
root-only `0700`.

Run the gate locally on WA-IR as root:

```bash
python3 scripts/activate_webapp_ir_promoted_listener.py \
  --config /etc/trading-bot-three-site/webapp-ir-promoted-listener.env \
  --apply --json
```

The helper renders only
`nginx-webapp-ir-promoted-2c08-https.conf.template`, verifies the fixed
`127.0.0.1:18000` API backend and direct-sync fence, atomically replaces only
the existing enabled site, runs `nginx -t`, and then runs `nginx -s reload`.
Any validation or reload failure restores the previous site bytes and emits no
receipt.  A successful receipt is root-only and records the exact release and
rendered-config hash; the external routing controller must require that fresh
receipt before it changes a public origin.  The helper never copies a TLS key
from another host and never makes a route change itself.

## Promotion Coordinator

The long-running promotion watch must not be followed by a route command in an
`ExecStartPost`: it does not exit while waiting.  The single systemd promotion
unit runs the local coordinator, which waits inside `promote-watch` until a
safe promotion is possible, then runs only this serial sequence: the local
listener gate and the route bridge with the newly written listener receipt.

Create a root-only (`0600`) JSON copy of
`deploy/production/webapp-ir-promotion-coordinator.json.example` outside Git.
All referenced configs, receipts, proof directories, token files, and audit
parents must already be root-only.  The config includes the canonical active
snapshot pointer; the coordinator passes it to the writer watch so the selected
candidate is bound again immediately before promotion.  It has no configurable
script paths, SSH, or Object Storage operation, and uses only the three fixed
scripts from the exact release directory.

```bash
python3 scripts/run_webapp_ir_promotion_coordinator.py \
  --config /etc/trading-bot-three-site/webapp-ir-promotion-coordinator.json \
  --apply --json
```

Any failed stage stops the sequence without starting a later stage.  The route
bridge is invoked only after the listener helper has returned `reloaded` and a
fresh root-only listener receipt exists at the configured path.
