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

Inventory approval is deliberately two-stage:

1. two operators sign a `planned` inventory in which PostgreSQL system IDs are
   null and all host/volume names are fixed;
2. each fresh host passes `fresh-preflight`, then only its empty DB volume is
   provisioned;
3. `measure-provisioned` records the real PostgreSQL IDs;
4. `finalize_three_site_staging_inventory.py` creates a new unsigned
   `provisioned` inventory from all four measurements;
5. two fresh signatures over that exact inventory are mandatory before restore,
   migration, service startup, or data movement.

The multi-host transport does not pretend Docker internal networks span hosts.
DR senders use dedicated egress networks; each TLS receiver is published only
on its signed inventory address (`8443`, Witness `8444`); and fixed host aliases
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

1. render and verify all four exact-SHA role bundles from a two-person-signed
   `planned` inventory;
2. pass `fresh-preflight` on each dedicated host, provision only the declared
   empty volumes, measure the four PostgreSQL system identifiers, finalize the
   `provisioned` inventory, and obtain two fresh signatures;
3. build/distribute one release, capture all four image inventories, and prove
   identical image bytes/digests for every shared image reference;
4. freeze both legacy staging sources, retaining PostgreSQL and Redis but
   stopping every application process recorded in the freeze evidence;
5. create PostgreSQL/uploads/audit backups from the frozen sources and pass the
   independent PostgreSQL-15 restore/fingerprint drill; Redis is observed but
   is never restored;
6. encrypt each artifact with the campaign age recipient, publish it to the
   exact versioned Arvan Object Storage bucket/key, read back that exact
   `VersionId`, decrypt it, and re-prove the original plaintext hash;
7. create the migration plan binding the provisioned inventory, source-freeze
   evidence, restore-verified backups, encrypted object versions, image
   inventories, target seed map, and rollback policy; obtain two independent
   Ed25519 approvals within its maximum four-hour window;
8. fetch and verify the target seed on each host, then execute the four local
   role journals and the cross-role barriers described below;
9. keep CDN routing on the legacy origin until all four roles are accepted and
   the global commit evidence exists. No command in this runbook changes Arvan
   or the production domain automatically;
10. retain frozen legacy volumes and backups until rollback and Full Matrix
    evidence have both been accepted. Cleanup is a separate owner decision.

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
  --signer-policy /etc/trading-bot/security/staging-inventory-signers.json \
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
  --signer-policy /etc/trading-bot/security/staging-inventory-signers.json \
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
to `c431d2e3f5a6`; on WebApp-IR it also converts the restored FI clone into a
locally fenced epoch-1 standby.

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

The backend is restricted to the signed provisioned inventory, release SHA,
the two exact WebApp host IPs, and `app.gold-trading.ir`. It uses key-only SSH
with strict known-host checking. SSH is only the low-volume staging management
plane; it never transports seed or Blob payloads. Source fencing captures the
destination-specific FI-to-IR or IR-to-FI stream sequence and transaction hash,
not the global producer sequence. Promotion cannot continue until the target
has applied that exact boundary, the predecessor Witness lease is drained and
expired, the target acquires exactly the next epoch, and its private control
agent proves a fresh post-acquisition renewal.

Every failover package must contain a plan with two independent Ed25519
approvals, the exact closed typed-operation manifest, the pinned approver
policy, fresh signed connectivity evidence, and the fresh two-person-signed
provisioned inventory. Run the command without `--apply`, review the returned
operation/route/term identity and exact confirmation phrase, then repeat with
`--apply` only inside the separately authorized Full Matrix window:

```bash
python3 scripts/run_three_site_failover_orchestration.py \
  --plan /root/secure/failover/active-plan.json \
  --command-manifest /root/secure/failover/typed-operation-manifest.json \
  --approver-policy /etc/trading-bot/security/dr-failover-approvers.json \
  --backend-config /root/secure/failover/staging-backend.json \
  --inventory /root/secure/provisioned-inventory.json \
  --inventory-approval /root/secure/provisioned-inventory-approval.json \
  --inventory-signer-policy /etc/trading-bot/security/staging-inventory-signers.json \
  --journal /root/secure/failover/operation.jsonl
```

The saga is hash-journaled and restart-aware. An expired or failed operation
never resumes a forward step. A failure after any mutation fences both WebApp
sites, proves zero application-role connections on both, waits for any Witness
lease to expire, and leaves the currently observed test origin unchanged for a
separate signed recovery decision. It does not silently restore availability
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
scenario set, require two or three complete repetitions, reject skips, bind all
inputs by SHA-256, retain one unique owner-only artifact for every scenario,
run zero-residue cleanup after every phase, and use a hash-chained execution
journal. A controller crash after `scenario_started` cannot silently continue:
the deployment backend must first prove zero-residue recovery. A failed
campaign is terminal and needs a newly approved campaign.

The Queue-enabled activation SHA must be one direct commit above the reviewed
baseline and may change only the code-owned readiness constant. The
provisioned inventory must then be re-attested at that activation SHA; evidence
from the baseline deployment and activation release cannot be mixed. Generate
the transition evidence first, then prepare an unsigned campaign/approval
request with the builder:

```text
python3 scripts/build_three_site_staging_full_matrix_campaign.py prepare \
  --baseline-sha BASELINE_40_HEX \
  --activation-sha ACTIVATION_40_HEX \
  --approver-policy /etc/trading-bot/security/full-matrix-approvers.json \
  --object-bucket staging-three-site-full-matrix \
  --bound-artifact provisioned_inventory=/root/secure/matrix/inventory.json \
  --bound-artifact inventory_approval=/root/secure/matrix/inventory-approval.json \
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

Two people on independent custody devices sign the exact lowercase campaign
hash from the request. Each returns a document shaped like
`three-site-full-matrix-approval.example.json`. Assemble and cryptographically
verify the final campaign without manually editing it:

```text
python3 scripts/build_three_site_staging_full_matrix_campaign.py finalize \
  --draft /root/secure/matrix/campaign.draft.json \
  --approver-policy /etc/trading-bot/security/full-matrix-approvers.json \
  --approval /root/secure/matrix/approval-operator-1.json \
  --approval /root/secure/matrix/approval-operator-2.json \
  --output /root/secure/matrix/campaign.approved.json
```

The tracked approver policy and approval files are templates only and are
deliberately unusable. Real key material and every generated artifact stay
outside Git with owner-only permissions. Reusing one Ed25519 public key under
two names/custody labels is rejected across inventory, migration, failover, and
Full Matrix approval policies.

The controller core is source-complete and hermetically validated, but it does
not by itself authorize or perform a live staging campaign. The deployment
backend that implements every closed scenario operation on the migrated hosts,
and the explicit owner-authorized execution window, remain mandatory before
any evidence can be called authoritative. The standalone verifier
`verify_three_site_staging_full_matrix_campaign.py` only validates retained
evidence; it cannot manufacture or upgrade a partial campaign into a pass.
