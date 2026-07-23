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

## Three-site integration staging (not yet activated)

`docker-compose.three-site.yml` is the production-like integration topology for
Bot-FI, WebApp-FI, WebApp-IR, and the independent Writer-Witness. It is not
started by the legacy `scripts/deploy_staging.sh` flow above and must not be
pointed at production volumes, tokens, certificates, domains, or databases.

The authoritative Full Matrix is deliberately split by host safety, not by
feature. A `shared-host-safe` campaign contains 104 container/application
scenarios and may run beside production on the existing four hosts. It may
reuse only the physical host, Linux machine identity, and Docker daemon;
Compose projects, PostgreSQL/Redis/uploads volumes, PostgreSQL system IDs,
audit roots, credentials, ports, domains, buckets, and evidence roots remain
staging-only. Every service has explicit CPU/memory/PID ceilings and must be
placed below the `STAGING_CGROUP_PARENT` aggregate host slice. Production is
observe-only throughout this campaign.

A separate `dedicated-host-destructive` campaign contains the six operations
that can affect a whole machine or exhaust its host resources:
`witness_partition_and_vm_pause`, `fi_host_loss_without_national_cutoff`,
`permanent_fi_recovery_hub_loss`,
`ir_only_active_origin_loss_is_safe_unavailable`,
`power_loss_between_fence_and_enable`, and
`wal_event_redis_blob_capacity_exhaustion_safe`. It requires four disposable,
non-production hosts with distinct machine/Docker identities. Both campaigns
must use the same release SHA and Gate-D group UUID; neither report is
individually sufficient for Gate D.

Before rendering a shared-host role, review
`trading-bot-three-site-staging.slice.example` against measured host headroom,
install the reviewed unit as
`/etc/systemd/system/trading-bot-three-site-staging.slice`, run
`systemctl daemon-reload` and `systemctl enable --now
trading-bot-three-site-staging.slice`, and retain `systemctl show` plus cgroup
limit evidence. The example's 200% CPU and 50% hard memory ceilings are
starting bounds, not permission to consume capacity needed by production.
Lower them or increase host capacity when production headroom requires it.

The cgroup boundary does not limit disk growth. Every role therefore also
requires an independently mounted staging filesystem at
`/srv/trading-bot-three-site-staging-data`. The exact filesystem UUID is part
of approved inventory v3 and the live host snapshot verifies the mount target,
UUID, filesystem type, total capacity, and remaining capacity before Docker is
allowed to provision the role volumes. All named Compose volumes are local
bind volumes below that mount. If the disk is absent, the host attestation
fails instead of falling back to the operating-system or production disk.

Provision this boundary only after recording the new disk UUID and selecting
limits below measured production headroom. The command plans by default and
prints the exact confirmation phrase required by `--apply`:

```bash
sudo scripts/provision_three_site_staging_host_boundary.sh \
  --role bot-fi \
  --device /dev/disk/by-id/replace-with-staging-device \
  --expected-uuid replace-with-filesystem-uuid \
  --cpu-quota 200% --memory-high 4G --memory-max 5G --tasks-max 2048
```

The provisioner installs only the dedicated mount and the empty staging slice;
it does not start Compose, restart Docker, or restart a production service.

Its configuration shape is documented in
`env.three-site.staging.example`. Copy that file to an untracked mode-0600
location, replace every `CHANGE_ME`, and verify the resulting effective Compose
before any migration. The Witness initializer, migrator, and runtime database
identities are deliberately distinct; only the initializer may receive
`WITNESS_POSTGRES_PASSWORD`, and `witness_api` must use the runtime identity.

Telegram Queue-v1 remains an explicit cutover inside this topology. Initial
migration uses `TELEGRAM_DELIVERY_EXECUTION_OWNER=legacy` with both queue guards
false. The immutable Full Matrix campaign may switch all three controls only
after the dedicated staging Bot identities/channel permissions pass live
preflight; the editor token is present only in `bot_fi_bot`.

The canonical Compose is rendered into four deterministic role bundles. The
generated Compose may live outside the Git worktree because build contexts and
Nginx bind sources use the attested absolute `STAGING_SOURCE_ROOT`; this keeps
the deployed exact-SHA worktree clean. Never distribute the full source env:

```bash
python3 scripts/render_three_site_staging_role_compose.py \
  --role bot-fi \
  --compose deploy/staging/docker-compose.three-site.yml \
  --output /etc/trading-bot-three-site/bot-fi.compose.yml \
  --env-source /root/secure/three-site-full.env \
  --env-output /etc/trading-bot-three-site/bot-fi.env
```

The role Compose must be mode `0640`; the role env and every host snapshot must
be mode `0600`. `verify_three_site_staging_role_bundle.py` validates one host.
`verify_three_site_staging_campaign_bundle.py` validates all four together and
additionally proves that directional DR secrets match only their two endpoints,
Witness credentials match their WebApp/Witness endpoints, database credentials
are globally distinct, and intentional WebApp session/readiness secrets match.

Inventory approval is deliberately two-stage. Human authorization uses the
passphrase-plus-TOTP issuer documented in
`docs/THREE_SITE_HUMAN_APPROVAL_TOTP_RUNBOOK.md`; legacy two-device signature
documents are rejected:

1. the owner approves a `planned` inventory in which PostgreSQL system IDs are
   null and all host/volume names are fixed;
2. each target role passes `fresh-preflight`, then only its empty, staging-owned
   DB volume is provisioned (the destructive campaign additionally requires a
   fresh dedicated host for every role);
3. `measure-provisioned` records the real PostgreSQL IDs;
4. `finalize_three_site_staging_inventory.py` creates a new unapproved
   `provisioned` inventory from all four measurements;
5. one fresh passphrase-plus-TOTP token over that exact provisioned inventory is
   mandatory before restore, migration, service startup, or data movement.

The multi-host transport does not pretend Docker internal networks span hosts.
DR senders use dedicated egress networks; each TLS receiver is published only
on its approved inventory address (`8443`, Witness `8444`); and fixed host aliases
bind the reviewed TLS names directly to signed peer IPs. Host firewalls must
still allow these ports only from the exact peer addresses. The role/campaign
verifiers reject loopback template values, peer-IP drift, missing egress,
missing host aliases, or an unbound/wildcard TLS port.

### Migration runbook and stop boundary

The migration is a short, explicitly approved maintenance window. Source data
must not remain writable while backups, encrypted seed objects, and the signed
migration plan are prepared. Run every mutating command once without `--apply`,
review the emitted confirmation phrase, then rerun it with `--apply` and the
exact phrase. A failed or interrupted role phase is rollback-only; deleting or
editing its journal is forbidden.

The required order is:

1. render and verify all four exact-SHA role bundles from an owner-approved
   `planned` inventory;
2. pass `fresh-preflight` on each dedicated host, provision only the declared
   empty volumes, measure the four PostgreSQL system identifiers, finalize the
   `provisioned` inventory, and obtain a fresh exact-subject approval token;
3. build/distribute one release, capture all four image inventories, and prove
   identical image bytes/digests for every shared image reference;
4. freeze both legacy staging sources, retaining PostgreSQL and Redis but
   stopping every application process recorded in the freeze evidence;
5. create PostgreSQL/uploads/audit backups from the frozen sources and pass the
   independent PostgreSQL-15 restore/fingerprint drill; Redis is observed but
   is never restored;
   the source PostgreSQL identifiers must be present in the signed protected
   boundary inventory because they are pre-existing, read-only migration
   inputs, while every newly provisioned target identifier must remain outside
   that boundary;
6. encrypt each artifact with the campaign age recipient, publish it to the
   exact versioned Arvan Object Storage bucket/key, read back that exact
   `VersionId`, decrypt it, and re-prove the original plaintext hash;
7. create the migration plan binding the provisioned inventory, source-freeze
   evidence, restore-verified backups, encrypted object versions, image
   inventories, target seed map, and rollback policy; obtain one fresh
   passphrase-plus-TOTP approval within its maximum four-hour window;
8. fetch and verify the target seed on each host, then execute the four local
   role journals and the cross-role barriers described below;
9. keep CDN routing on the legacy origin until all four roles are accepted and
   the global commit evidence exists. No command in this runbook changes Arvan
   or the production domain automatically;
10. retain frozen legacy volumes and backups until rollback and Full Matrix
    evidence have both been accepted. Cleanup is a separate owner decision.

Image inventory schema v2 deliberately separates the host-local Docker
`image_id` from the cross-host `content_identity`. Docker's legacy `overlay2`
image store reports a config digest as `image_id`, while its containerd image
store reports a manifest digest for the same save/load bytes. The canonical
content identity instead binds the architecture, OS, creation timestamp,
canonical runtime-config hash, and ordered rootfs diff IDs. Cross-host equality
uses this recomputable identity; the local ID is still retained and checked
against containers on that same host. Third-party images additionally require
stable provider repository digests.

First freeze the legacy Compose project. The evidence records exactly which
services were running so rollback cannot accidentally start an unrecorded
service:

```bash
python3 scripts/freeze_three_site_staging_sources.py \
  --repo /srv/trading-bot/current \
  --compose /srv/trading-bot/current/deploy/staging/docker-compose.staging.yml \
  --env-file /root/secure/.env.staging \
  --project-name trading_bot_staging \
  --source-role webapp_fi \
  --expected-source-release-sha webapp_fi=<current-staging-sha> \
  --inventory /root/secure/provisioned-inventory.json \
  --inventory-approval /root/secure/provisioned-inventory-approval.json \
  --approval-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --output /root/secure/source-freeze-webapp-fi.json
```

Then run the backup tool against that still-frozen source. Applied execution
creates owner-only PostgreSQL/uploads/audit artifacts outside Git, excludes
Redis from restore, restores the dump into an isolated PostgreSQL 15 container
with no published port, and records a full table/sequence fingerprint:

```bash
python3 scripts/run_three_site_staging_source_backup.py \
  --source-role webapp_fi \
  --repo /srv/trading-bot-three-site/current \
  --compose /srv/trading-bot-three-site/current/deploy/staging/docker-compose.staging.yml \
  --env-file /root/secure/.env.staging \
  --output-dir /srv/trading-bot-three-site-backups/<campaign>/webapp_fi \
  --project-name trading_bot_staging \
  --source-freeze-evidence /root/secure/source-freeze.json \
  --inventory /root/secure/provisioned-inventory.json \
  --inventory-approval /root/secure/provisioned-inventory-approval.json \
  --approval-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --expected-source-release-sha <current-staging-sha> \
  --target-release-sha <integration-candidate-sha>
```

`publish_three_site_staging_seed.py` and
`fetch_three_site_staging_seed.py` provide the only approved seed transport.
Their S3 credential file, age identity/recipient material, fetched artifacts,
and evidence outputs must be owner-only and outside Git. A moving key without
an exact Object Storage version id is rejected.

### Four-role execution and barriers

Each host begins with `run_three_site_staging_role_migration.py begin`. Run
`restore-seed`, `configure-database`, and `start-private` on all four roles in
their journal order. `configure-database` upgrades every supported predecessor
to `b986c7d8e0f1`; on WebApp-IR it also converts the restored FI clone into a
locally fenced epoch-1 standby.

Before Alembic starts, `configure-database` fails closed unless every
application/migration/worker service is stopped and three consecutive database
samples show zero other client sessions. This runtime proof complements the
signed legacy-source freeze evidence. The `b986c7d8e0f1` migration then takes a
write-excluding lock on all DR event/cursor/binding tables before its final
cursor-led history preflight; an old-rule transaction must therefore either
commit before the lock and be included in validation, or wait until the new
contract is installed.

The supported DR retention model is full-prefix retention: a producer or
destination cursor may not outlive any member in its `1..last_sequence`
history. Pruning `dr_events` or their destination sequence evidence while the
cursor survives is unsupported and intentionally makes migration fail. Legacy
protocol-v1 members remain valid in the producer prefix; destination allocation
starts with protocol v2, and pre-`source_xid` v2 envelopes are accepted as
historical destination evidence.

The compatibility migration leaves WebApp-FI active at epoch 1 but does not
invent a Witness lease. After the Witness private service is ready, run the
following first inside the `webapp_fi_writer_control` service without `--apply`,
then with its exact confirmation. The UUID must be generated and durably
recorded before the first attempt and reused after any ambiguous response:

```bash
python3 scripts/bootstrap_three_site_staging_writer_lease.py \
  --campaign-id <campaign-uuid> \
  --request-id <persisted-request-uuid> \
  --expected-release-sha <integration-candidate-sha>
```

The `attest-writer-state` role phase then requires WebApp-FI to hold and renew a
live Witness epoch-1 lease, while WebApp-IR must remain locally fenced. It does
not accept a hand-written Writer-state document.

Collect the four mode-0600 journals on the trusted migration controller and use
`coordinate_three_site_staging_migration.py` in this order:

1. `private-barrier` after Bot/Witness private readiness and both WebApp Writer
   attestations; its per-role evidence authorizes only the product workers;
2. `routing-hold` after worker startup and fresh event-checkpoint, database
   parity, Blob parity, and unchanged-Arvan observations; its evidence
   authorizes public services only while the CDN still points at legacy;
3. `role-acceptance` after fresh direct-origin checks for every role, with
   Queue ownership still `legacy` and routing still held;
4. `global-commit` only after all four role journals are committed. A role may
   enter `finish` only with this exact global document.

Evidence is bound to the current per-role journal state hash. Reusing a barrier
after any role advances or replacing a role Compose/env bundle is rejected.
Until the global commit exists, rollback stops the new topology while preserving
all target bytes, then `restore_three_site_staging_sources.py` restarts only the
exact legacy services captured by the freeze evidence.

### Typed failover/failback backend

The live staging saga is implemented by
`run_three_site_failover_orchestration.py` and the closed backend configuration
shape in `three-site-failover-backend.example.json`. The example is a template,
not an executable configuration. Create the real file outside Git with mode
`0600`; keep its SSH keys, pinned known-hosts files, Witness credential/CA,
Arvan token, connectivity evidence, readiness key, journals, plans, and
evidence directories owner-only.

The backend is restricted to the approved provisioned inventory, release SHA,
the two exact WebApp host IPs, and `app.gold-trading.ir`. It uses key-only SSH
with strict known-host checking. SSH is only the low-volume staging management
plane; it never transports seed or Blob payloads. Source fencing captures the
destination-specific FI-to-IR or IR-to-FI stream sequence and transaction hash,
not the global producer sequence. Promotion cannot continue until the target
has applied that exact boundary, the predecessor Witness lease is drained and
expired, the target acquires exactly the next epoch, and its private control
agent proves a fresh post-acquisition renewal.

Every failover package must contain a plan with one fresh action-bound human
approval token, the exact closed typed-operation manifest, the pinned public
human-approval policy, fresh machine-signed connectivity evidence, and the
approved provisioned inventory. Run the command without `--apply`, review the returned
operation/route/term identity and exact confirmation phrase, then repeat with
`--apply` only inside the separately authorized Full Matrix window:

```bash
python3 scripts/run_three_site_failover_orchestration.py \
  --plan /root/secure/failover/active-plan.json \
  --command-manifest /root/secure/failover/typed-operation-manifest.json \
  --human-approval-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --backend-config /root/secure/failover/staging-backend.json \
  --inventory /root/secure/provisioned-inventory.json \
  --inventory-approval /root/secure/provisioned-inventory-approval.json \
  --inventory-approval-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --journal /root/secure/failover/operation.jsonl
```

The saga is hash-journaled and restart-aware. An expired or failed operation
never resumes a forward step. A failure after any mutation fences both WebApp
sites, proves zero application-role connections on both, waits for any Witness
lease to expire, and leaves the currently observed test origin unchanged for a
separately approved recovery decision. It does not silently restore availability
or change the production domain.

### Queue activation and remaining gate

The migrated topology deliberately starts in legacy Telegram ownership. The
Queue Full Matrix runs only from a direct one-commit successor whose entire tree
delta is the single code-owned `False` to `True` readiness gate.
`verify_three_site_queue_activation_transition.py` mechanically proves the
parent relation, changed path, exact bytes, clean checkout, and diff hash; any
second file or line blocks the campaign.

This tooling still authorizes no staging or production mutation. The concrete
typed staging backend now exists as source and focused tests, but it still
requires independent review and real-host rollback/fault proof. The single
immutable-SHA, no-skips combined Full Matrix controller remains mandatory.
CDN or production-domain changes require their own explicit authorization
after the migration evidence is reviewed.

### Immutable Full Matrix campaign contract

The source-owned campaign catalog and crash-safe controller now live in
`core/three_site_full_matrix_campaign.py` and
`core/three_site_full_matrix_runner.py`. They fix the order and complete
scenario set. The catalog is exhaustively and disjointly classified as 104
`shared-host-safe` plus 6 `dedicated-host-destructive` scenarios. Each campaign
requires exactly two complete repetitions, rejects skips, binds all
inputs by SHA-256, retains one unique owner-only artifact for every scenario,
runs zero-residue cleanup after every phase, and uses a hash-chained execution
journal. A controller crash after `scenario_started` cannot silently continue:
the deployment backend must first prove zero-residue recovery. A failed
campaign is terminal and needs a newly approved campaign. Starting a campaign
requires one fresh `start_full_matrix` token; a proven journal resume verifies
that same token historically and cannot start a different campaign.

Customer-chain coverage is not allowed to hide behind the broad application
regression scenario. Four closed lifecycle scenarios run while WebApp-FI is
normally active, while WebApp-IR is outage-active, while recovery remains
routed to WebApp-IR, and after controlled failback to WebApp-FI. Each lifecycle
scenario must retain one distinct raw artifact and one exact source-owned
assertion for every one of the 17 actor-pair families. The controller rejects a
missing pair, a shared pair artifact, an altered Tier1/Tier2 policy, wrong
Writer/origin state, missing owner/privacy/notification invariant, or a false
convergence claim. These four scenarios are repeated with the rest of the
catalog; a generic `market_trade_account_admin_regression=passed` result cannot
replace them.

The Queue-enabled activation SHA must be one direct commit above the reviewed
baseline and may change only the code-owned readiness constant. The
provisioned inventory must then be re-attested at that activation SHA; evidence
from the baseline deployment and activation release cannot be mixed. Generate
the transition evidence first, then prepare an unapproved campaign/approval
request with the builder:

```text
python3 scripts/build_three_site_staging_full_matrix_campaign.py prepare \
  --baseline-sha BASELINE_40_HEX \
  --activation-sha ACTIVATION_40_HEX \
  --gate-group-id ONE_GATE_D_GROUP_UUID \
  --execution-class shared-host-safe \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --object-bucket EXACT_BUCKET_FROM_SIGNED_INVENTORY \
  --bound-artifact provisioned_inventory=/root/secure/matrix/inventory.json \
  --bound-artifact inventory_approval=/root/secure/matrix/inventory-approval.json \
  --bound-artifact human_approval_policy=/etc/trading-bot/security/human-approval/human-approval-policy.json \
  --bound-artifact migration_plan=/root/secure/matrix/migration-plan.json \
  --bound-artifact migration_approval=/root/secure/matrix/migration-approval.json \
  --bound-artifact global_commit=/root/secure/matrix/global-commit.json \
  --bound-artifact campaign_bundle=/root/secure/matrix/campaign-bundle.json \
  --bound-artifact queue_activation_transition=/root/secure/matrix/queue-transition.json \
  --bound-artifact failover_backend_config=/root/secure/matrix/failover-backend.json \
  --bound-artifact full_matrix_backend_config=/root/secure/matrix/full-matrix-backend.json \
  --draft-output /root/secure/matrix/campaign.draft.json \
  --approval-request-output /root/secure/matrix/approval-request.json
```

The prepare step writes the exact approval subject. Transfer it through the
private versioned Object Storage path and issue one `start_full_matrix` token
on the Witness with `manage_three_site_human_approval.py`. Assemble and
cryptographically verify the final campaign without manually editing it:

```text
python3 scripts/build_three_site_staging_full_matrix_campaign.py finalize \
  --draft /root/secure/matrix/campaign.draft.json \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --approval /root/secure/matrix/start-full-matrix-token.json \
  --output /root/secure/matrix/campaign.approved.json
```

The tracked policy, subject, and token examples are deliberately unusable.
Issuer secrets never leave the Witness; the generated public policy, subjects,
tokens, and evidence remain outside Git with owner-only permissions. Every
consumer rejects legacy two-device policies and any action, subject, policy,
environment, release, or signature drift.

`core/three_site_full_matrix_command_backend.py` and
`scripts/run_three_site_staging_full_matrix_campaign.py` provide the concrete,
shell-free execution boundary and the official execute/verify CLI. The bound
backend config must name one regular, owner-controlled driver directly under
`scripts/full_matrix_drivers/`, bind its SHA-256, enumerate the exact closed
catalog, forbid production, and define bounded operation timeouts (the
endurance timeout is at least 24 hours). Driver or catalog drift fails before
preflight. The controller independently reopens typed scenario evidence, every
raw reference, and preflight/recovery/cleanup/finalization artifacts. It also
measures endurance with its own monotonic clock; a driver cannot instantly
self-attest the 24-hour scenario.

The command backend does not make an arbitrary or test-only driver
authoritative. Before Gate D, the reviewed live driver and its exact hash must
be committed in the disabled baseline, remain unchanged at the activation SHA,
be exercised on the migrated dedicated
staging environment, and included in the already-bound backend config. A
shared-host-safe driver may use an existing physical host but is forbidden from
host reboot/power/firewall/route/Docker-daemon/storage-pressure mutation and
from every production domain, bucket, credential, volume, process, and Compose
project. Destructive driver operations are legal only on the approved disposable
inventory. Execute only in the separately authorized window.

Prepare, approve, execute, and independently verify a second campaign with
`--execution-class dedicated-host-destructive`, the same
`--gate-group-id`, and the same activation SHA. After both official reports
exist, create the only artifact that can pass Gate D:

```text
python3 scripts/build_three_site_staging_gate_d_aggregate.py prepare \
  --shared-report /root/secure/matrix/shared/report.json \
  --destructive-report /root/secure/matrix/destructive/report.json \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --draft-output /root/secure/matrix/gate-d.draft.json \
  --approval-request-output /root/secure/matrix/gate-d-approval-request.json

python3 scripts/build_three_site_staging_gate_d_aggregate.py finalize \
  --draft /root/secure/matrix/gate-d.draft.json \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --approval /root/secure/matrix/approve-gate-d-token.json \
  --output /root/secure/matrix/gate-d.approved.json

python3 scripts/build_three_site_staging_gate_d_aggregate.py verify \
  --aggregate /root/secure/matrix/gate-d.approved.json \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json
```

The aggregate verifier rejects a missing class, overlapping catalog, different
release/group, reused campaign, altered report hash, skipped scenario, residue,
or any production mutation. It requires one fresh `approve_gate_d` token bound
to both exact component report hashes.

Synchronization timing is not inferred from scenario wall-clock duration.
Migration head `b986c7d8e0f1` retains the first delivery attempt, rejects any
cursor whose retained DR history is not exactly contiguous, and takes a
write-excluding stream-table lock before its final history validation. Each role
bundle now contains a dedicated `*_sync_observer` database identity. That
identity can only read `alembic_version`, `dr_database_runtime`, `dr_events`,
`dr_event_deliveries`, and `dr_event_receipts`; it has no business, Writer,
Queue, Telegram, Blob, effect, function, or sequence authority.

Bot-FI, WebApp-FI, and WebApp-IR hosts must have `chronyc` (preferred) or
`ntpq` installed and synchronized before host attestation. `timedatectl`
reporting synchronized without an offset-capable client is insufficient and
now fails preflight rather than failing halfway through timing collection.

The workload doer must create a unique correlation manifest. The independent
observer then runs `scripts/run_three_site_sync_timing_observer.py` with an
owner-only config shaped like
`deploy/staging/three-site-sync-observer.example.json`. The config is bound to
the exact provisioned staging inventory, three literal host IPs, canonical
role Compose/env paths, key-only strict-host-key SSH, and one owner-only
artifact directory. On every observation it:

- derives NTP status and a conservative clock-error bound (including root
  dispersion and half root delay) from `timedatectl` plus `chronyc` or `ntpq`;
- launches only the one-shot least-privilege observer service on each role;
- correlates the same event/envelope through direct and relayed hops;
- retains event-created, delivery-enqueued, first-attempt, receive, apply, and
  acknowledgement times, attempt count, hashes, and payload bytes;
- recomputes route and physical-hop p50/p95/p99/max values from raw samples;
- measures recovery backlog and live ingress itself until pending work reaches
  zero, rather than trusting the workload report.

Normal FI-active timing covers Bot-FI -> WebApp-FI, WebApp-FI -> Bot-FI,
WebApp-FI -> WebApp-IR, and Bot-FI -> WebApp-IR via WebApp-FI. It deliberately
does not manufacture authoritative WebApp-IR writes while that site is
standby. IR -> FI and IR -> Bot relay timing is required in the IR-active
recovery scenarios. The 300-rps profile also retains Bot and WebApp request
counts and fails if their observed split differs by more than one percentage
point from 50/50. It enforces the approved FI<->FI p50/p95/p99 upper bounds of
80/150/300 ms and the healthy FI->IR bounds of 500/750/2000 ms. The 300-rps,
reconnect/catch-up, and one-hour backlog
scenarios use the same raw verifier. Missing routes, reused event evidence,
unsynchronized/stale clocks, forged percentiles, a backlog not observed before
drain, no live ingress during recovery, or a non-zero final backlog all fail.

Every backend invocation is preceded by a hash-journaled intent carrying a
deterministic operation ID. Scenario and recovery invocations also receive a
strictly increasing `--attempt`; a controller crash replays only the same
intent or creates the next recovery attempt and can never reinterpret stale
evidence as the current execution. Preflight, recovery, cleanup and finalize
must each return a typed `three-site-full-matrix-operation-evidence-v1`
artifact. The controller reopens its raw evidence, exact expected/observed
assertions, residue count and production boundary before recording success.
Scenario evidence is likewise rejected when a named assertion says `passed`
but its observed value differs from the catalog-bound expected value.

There is still intentionally no tracked **all-catalog live scenario driver**
in the repository at this source-remediation stage. The synchronization
observer above is a real read-only component, but it does not pretend to
implement migration, provider, failover, Queue, application, and destructive
fault doers. Consequently Gate D cannot be assembled or executed yet. Adding
that reviewed all-scenario driver and its independent per-scenario oracles is
an explicit later gate; an operator-local command runner or self-attesting
placeholder is forbidden.

```text
THREE_SITE_STAGING_FULL_MATRIX_CONFIRM=execute-authoritative-three-site-staging-full-matrix \
python3 scripts/run_three_site_staging_full_matrix_campaign.py execute \
  --campaign /root/secure/matrix/campaign.approved.json \
  --approver-policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --backend-config /root/secure/matrix/full-matrix-backend.json \
  --artifact-root /root/secure/matrix/evidence \
  --journal /root/secure/matrix/evidence/campaign.jsonl \
  --bound-artifact NAME=/absolute/path \
  --output /root/secure/matrix/final-report.json
```

Repeat `--bound-artifact` once for every name shown in the preparation command.
The standalone `verify` action and
`verify_three_site_staging_full_matrix_campaign.py` only validate the complete
retained evidence set; neither can manufacture or upgrade a partial campaign
into a pass.
