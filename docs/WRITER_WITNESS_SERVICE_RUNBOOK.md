# Writer Witness Service Runbook

Status: dedicated dark host deployed and verified; writer activation is not authorized

## Purpose And Boundary

`writer_witness_app:app` is a private control-plane process for the single
global WebApp writer term. It is not part of the public WebApp API, must not be
placed behind the public WebApp hostname, and must not share the WebApp product
database identity.

The process exposes only:

- `GET /health/live` with no ownership details;
- `GET /health/ready` with no ownership details;
- authenticated `GET /v1/writer-witness/status`;
- authenticated `POST /v1/writer-witness/transitions`.

The two WebApp sites use separate HMAC credentials. Each signed request binds
the physical site, key id, method, path, exact body hash, timestamp, and stable
request id. The witness database clock evaluates the timestamp and lease.
Successful and state-dependent rejected transition requests both receive a
durable receipt, so a delayed rejected packet cannot become valid after lease
expiry.

## Mandatory Isolation

For every deployment:

1. Create a dedicated PostgreSQL database and apply
   `deploy/writer-witness/001_initial.sql` using a migration identity.
2. Create a runtime role with only `CONNECT`, `USAGE`, `SELECT`, `INSERT`, and
   `UPDATE` on the two witness tables and schema-version read access. It must
   not own the database or have DDL privileges.
3. Set `WRITER_WITNESS_DATABASE_URL` to that runtime role. By default the
   service refuses to start unless `WRITER_WITNESS_PRODUCT_DATABASE_USER` is
   supplied and differs from the witness connection username.
4. Store the raw-base64 Ed25519 private key in an absolute `0600` secret file.
   Only the witness process receives it. WebApp processes receive the matching
   public key only.
5. Generate independent random HMAC secrets of at least 32 bytes for
   `webapp_fi` and `webapp_ir`. Never copy one site's client secret to the other.
6. Bind the process to a private interface or loopback reverse proxy. Restrict
   ingress to the two fixed WebApp control-plane sources and use verified TLS
   or mTLS. Arvan and the public WebApp origin must not expose these routes.

## Service Settings

The witness process requires these settings in its private environment:

```text
LOGICAL_AUTHORITY=webapp
PHYSICAL_SITE=webapp_ir
WRITER_WITNESS_SERVICE_ENABLED=true
WRITER_WITNESS_DATABASE_URL=postgresql+asyncpg://<least-privilege-user>:<secret>@<db>/writer_witness
WRITER_WITNESS_PRODUCT_DATABASE_USER=<product-database-username-for-separation-check>
WRITER_WITNESS_REQUIRE_DISTINCT_DATABASE_IDENTITY=true
WRITER_WITNESS_PRIVATE_KEY_FILE=/run/secrets/webapp_writer_witness_ed25519
WRITER_WITNESS_PUBLIC_KEY=<raw-ed25519-public-key-base64>
WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID=<fi-key-id>
WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET=<fi-random-secret>
WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID=<ir-key-id>
WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET=<ir-random-secret>
```

Use `deploy/production/writer-witness-runtime.env.example` as the minimal
template and load it through the process supervisor. The witness settings class
intentionally does not auto-read the repository `.env`. After provisioning and
staging approval, the process entry point is:

```text
uvicorn writer_witness_app:app --host 127.0.0.1 --port 8011 --workers 1
```

Process supervision, restart limits, private TLS termination, and health probes
must be supplied by the deployment layer before enablement.

The service validates explicit `webapp_ir` placement, distinct database
identity, key-file permissions, public/private key correspondence, pairwise
credential strength, and safe lease timing before serving.

## Current Dark Deployment Evidence

On 2026-07-15 the dedicated Witness was deployed to the Iran-reachable host
`185.231.182.6`. This is a dark control-plane deployment, not a writer
activation:

- PostgreSQL 16 listens only on loopback and uses separate migration and
  runtime roles;
- the runtime role can read the schema marker, read/update the singleton, and
  read/insert receipts, but cannot create a database, role, schema, or table;
- Uvicorn runs as the non-login `writer-witness` user on `127.0.0.1:8011` with
  one worker and systemd hardening;
- Nginx is the only `443` listener and admits only WebApp-FI
  (`65.109.220.59`), WebApp-IR (`87.236.212.194`), and local health/smoke
  traffic; UFW denies every other external source on `443`;
- the private CA has explicit critical CA/key-signing constraints, and the
  leaf has explicit non-CA, server-auth, key-usage, and IP SAN constraints;
- SSH password and keyboard-interactive authentication are disabled. Both
  `ubuntu` and `root` administration use the installed public key;
- the service, PostgreSQL, Nginx, UFW, and the daily backup timer survived a
  real VM reboot and returned automatically;
- daily custom-format PostgreSQL backups are checksumed and retained for 30
  days. A real restore into a temporary database verified schema `001`, the
  singleton, and receipt count before cleanup. Each new local dump is also
  encrypted with an off-host `age` recipient and uploaded to immutable Hetzner
  Object Storage;
- Python 3.12 dependencies are fully pinned in
  `deploy/writer-witness/requirements.lock`. A CPython 3.12 wheelhouse and its
  per-file `SHA256SUMS` are verified before offline installation because the
  Iran path did not expose the pinned `cryptography` package reliably.

Measured evidence after TLS rotation and reboot:

| Probe | Result |
|---|---|
| WebApp-FI to `/health/ready` with private CA | `200`; about `0.48-0.67s` observed RTT |
| WebApp-IR to `/health/ready` with private CA | `200`; about `0.03s` observed RTT |
| Unauthenticated status from either allowed site | `401` |
| Non-allowlisted Bot-FI/current operator source to `443` | connection timeout |
| Signed read-only status with FI credential | `200`, vacant epoch `0` |
| Signed read-only status with IR credential | `200`, vacant epoch `0` |
| Approximate Witness clock offset observed from FI | about `-0.55s` |
| Approximate Witness clock offset observed from IR | about `-0.50s` |
| NTP status on FI, IR, and Witness | synchronized |
| Process restart and VM reboot | service ready; state and backups preserved |

No credential bundle was installed in a WebApp runtime. Pairwise credentials
and the Ed25519 private signing key remain on the dedicated Witness host. The
CA may be copied for read-only TLS probes, but the WebApp environment, product
containers, origin routing, and Arvan configuration must remain unchanged
until the activation gates below are approved.

The repeatable deployment assets are:

- `scripts/build_writer_witness_release.sh` for the minimal manifest-bound
  source payload;
- `scripts/build_writer_witness_wheelhouse.sh` for the locked CPython 3.12
  wheelhouse and checksums;
- `scripts/provision_writer_witness_host.sh` for PostgreSQL, credentials, TLS,
  Nginx, systemd, SSH hardening, UFW, backup, restore drill, and final health;
- `scripts/provision_writer_witness_object_storage.py` for dry-run-first bucket,
  Object Lock, retention, private ACL, and least-privilege policy setup;
- `scripts/configure_writer_witness_s3_backup.sh` for installing the write-only
  encrypted backup path without copying a decryption identity to the Witness;
- `deploy/writer-witness/writer-witness-rotate-hmac.py` for guarded one-site
  overlap, revocation, rollback, and secret cleanup;
- `scripts/run_writer_witness_offsite_restore_drill.sh` for downloading with
  the off-host admin identity, verifying retention/checksum, decrypting outside
  the Witness, and streaming into an isolated temporary restore database;
- `scripts/smoke_writer_witness_client.py` for authenticated read-only status
  verification without printing client secrets.

## Encrypted Off-Host Backup Evidence

On 2026-07-15 the lower-cost Object Storage path was activated instead of a
dedicated backup VM or Bot-FI volume:

- private bucket `tb-witness-15352997-44732f4a35` is in HEL1 with versioning
  enabled and default 90-day `COMPLIANCE` Object Lock;
- a separate admin credential remains off the Witness; the credential installed
  on the Witness is explicitly denied list, read, delete, retention, ACL,
  policy, lifecycle, and bucket-control actions and can only upload under
  `witness/`;
- a live policy probe proved upload succeeds while list/read/delete return
  access denied;
- the `age` decryption identity remains off the Witness with mode `0600`; only
  its public recipient is installed on the Iran host;
- `writer-witness-offsite-backup.timer` runs daily after the local backup timer,
  and a root-only marker prevents a successful dump from being uploaded again;
- the first encrypted production-Witness dump was uploaded as
  `witness/writer-witness-20260715T114726Z.dump.age`, received an S3 version ID,
  and is locked in `COMPLIANCE` mode until
  `2026-10-13T14:11:36.178523941Z`;
- the end-to-end drill downloaded that exact object, matched its S3 metadata
  SHA-256, verified its retention, authenticated the `age` payload, streamed it
  to a temporary PostgreSQL database, and reproduced schema `001`, state
  `webapp:0:vacant`, and zero receipts before cleanup.

This closes encrypted off-host custody and a data-level restore. A clean
replacement-host rebuild and guarded live restore was subsequently proven on
`185.206.95.94`, including candidate validation, rollback preservation, and a
real reboot. During an Iran-to-global outage the local 30-day copies continue;
S3 upload catches up after connectivity returns.

## WebApp Client Settings

Each WebApp site receives only its own pairwise credential:

```text
WRITER_WITNESS_REQUIRED=false
WRITER_WITNESS_AUTO_RENEW_ENABLED=false
WRITER_WITNESS_INTERNAL_URL=https://<private-witness-host>
WRITER_WITNESS_CLIENT_KEY_ID=<this-site-key-id>
WRITER_WITNESS_CLIENT_SECRET=<this-site-secret>
WRITER_WITNESS_VERIFY_TLS=true
WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem
WRITER_WITNESS_HTTP_TIMEOUT_SECONDS=3
WRITER_WITNESS_AUTH_MAX_AGE_SECONDS=15
```

Both enable flags remain false until the real-host activation drills and
operator gates pass.
When later enabled, the active WebApp background leader renews every 30 seconds.
An ambiguous network failure retries the exact same request id. A validated
signed proof is then imported into local writer state in one transaction. If
renewal cannot be proved, no local expiry is extended and ordinary fencing
stops authoritative writes at the safety deadline.

## Pairwise HMAC Rotation Procedure

Rotate only one site at a time. Confirm the expected writer epoch and require a
vacant Witness before starting. The helper refuses mismatched client/service
material, unsafe file modes, an existing overlap, or an unexpected database
state.

```text
writer-witness-rotate-hmac prepare --site webapp_fi --expected-epoch 0
# Prove the old and new client materials both return HTTP 200 from WebApp-FI.
writer-witness-rotate-hmac revoke --site webapp_fi --expected-epoch 0
# Prove the new material returns 200 and the old material returns 401.
writer-witness-rotate-hmac finish --site webapp_fi --expected-epoch 0
```

Repeat separately with `webapp_ir`. Before `finish`, use `rollback` if either
the new credential or the source-site path fails. Never print or copy an env
file into evidence; hold old/new probe material only in owner-only `/run`
directories and remove it after each verification.

On 2026-07-15 this procedure rotated FI and IR independently from `v1` to `v2`
on replacement Witness `185.206.95.94`. Each overlap accepted old and new with
HTTP `200`; after revocation the new key returned `200` and the old key returned
`401` from the corresponding WebApp host. The Witness remained vacant at epoch
`0`, no transition receipt was created, no previous slot remained, and no
credential was persisted in a WebApp runtime.

## Production Stop Conditions

Do not issue the first lease, copy credentials into a WebApp runtime, or enable
WebApp witness enforcement merely because the dark service is healthy.
Production writer activation remains blocked until real-host directional
partition/pause/delayed-packet tests, operator approval policy, stale-epoch
side-effect binding, sync/parity/file gates, and the higher-level
recovery/Arvan orchestrator are complete.

## Real-Host Matrix Preflight

The real-host campaign uses replacement Witness `185.206.95.94` as the only
fault-injection target. Original Witness `185.231.182.6` remains an unchanged
rollback/reference host. WebApp-FI and WebApp-IR are production hosts, so the
preflight and the later campaign must never stop, restart, reconfigure, or
inject a broad network fault into their product services.

Build the side-effect-free plan from the dedicated feature branch:

```text
make writer-witness-real-host-matrix-plan
```

Execute every read-only entry gate:

```text
make writer-witness-real-host-matrix-preflight ARGS='--expected-commit <exact-40-character-sha>'
```

The preflight fails closed unless:

- the checkout is clean, remains on
  `feature/arvan-controlled-origin-failover`, and exactly matches the explicitly
  pinned commit;
- the focused Writer/Fencing/runtime source regression suite passes with zero
  skips, including four real-PostgreSQL tests and the four-database drill;
- WebApp-FI production is healthy and its Witness flags remain disabled;
- WebApp-IR application and sync-worker writers remain stopped while its
  database is healthy;
- no Witness client URL, key ID, or client secret is installed in either
  WebApp application environment or container, and every Matrix secret path is
  verified as tmpfs-backed;
- both WebApp sites reach the exact replacement certificate on TCP `443` and
  unsigned status calls return `401`;
- the replacement Witness is healthy, NTP-synchronized, below the disk guard,
  exactly `webapp:0:vacant`, and has zero receipts;
- a checksumed backup is less than 24 hours old;
- at least one connection-disabled rollback database exists;
- no candidate/failed database, active restore/rotation/campaign journal,
  replacement-restore temporary file, or connection-enabled auxiliary database
  exists;
- installed helper and systemd bytes, the running release directory, and the
  effective Nginx/UFW allow/deny semantics match the frozen release;
- the original Witness remains healthy, vacant, and unchanged.

The run bundle pins controller, client, restore, rotation, fault-helper, Nginx,
and minimal Witness-release hashes. The replacement Witness must also return
authenticated status `200` for both pairwise credentials, unsigned `401`, a
certificate with at least seven days remaining, and the exact release hash.

The retained JSON artifact is written outside the repository under
`/tmp/trading-bot-writer-witness-real-host-matrix/`. It contains commands,
bounded stdout/stderr, release identifiers, and pass/fail evidence but no HMAC
secret, private key, or client credential. The runner forces the artifact to
owner-only mode `0600`.

This preflight deliberately does not integrate `main` in either direction. A
pass authorizes only the dark-Witness control-plane fault matrix. It does not
prove that the feature branch is compatible with the current product release,
does not authorize a first lease, and does not permit production WebApp or
Arvan/CDN mutation.

### Mandatory abort and rollback order

Every RH scenario must install a cleanup trap before its first fault. On any
unexpected state, timeout, lost SSH path, production-health regression, scope
escape, or assertion failure, stop the campaign and execute this exact order:

1. While isolation remains active, stop and join all Matrix requesters and
   prove that no retry process remains.
2. Delete/revoke transient credentials and overlap capability before
   reconnecting a path.
3. Capture, redact, hash, and retain pre-recovery evidence before restoration.
4. Resume only affected replacement-Witness process/PostgreSQL/Nginx services.
5. Remove only Matrix-owned isolated filesystem and pressure artifacts.
6. Remove Matrix-owned firewall/traffic-control objects only after requesters
   and capabilities are gone.
7. Restore through the journaled, OID-aware live restore from the pinned
   checksum and full epoch-zero manifest.
8. Prove both Witness manifests, FI health, stopped IR writers, disabled flags,
   restored paths, and absence of hidden Matrix resources.

The campaign may finish successfully only after the same full preflight passes
again and no transient credential, network rule, pause, mount, or clock
override remains. Original Witness must not be promoted automatically or used
as a second writer during rollback.

### One-scenario execution

The executor deliberately has no `--all` mode. Render one scenario first:

```text
make writer-witness-real-host-scenario-plan ARGS='--scenario RH-001 --expected-commit <sha>'
```

The independent observer first binds out-of-band console, alternate
communications, maintenance window, DPI budget, and restore authorization to
the exact preflight and scenario:

```text
WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM=approve-one-dark-witness-scenario \
make writer-witness-real-host-scenario-approve ARGS='--scenario RH-001 --expected-commit <sha> --preflight /tmp/.../preflight.json --observer <name> --incident-commander <different-name> --reason <exact-incident-reason> --change-id <change-id> --out-of-band-console <provider-session> --alternate-communications <incident-bridge> --maintenance-window-start <timezone-aware-ISO-8601> --maintenance-window-end <timezone-aware-ISO-8601> --dpi-byte-budget <at-least-3276800> --restore-authorized-by <name> --output /tmp/.../rh-001-approval.json'
```

The observer and incident commander sign that exact owner-only JSON with two
different SSH signing keys in namespace `writer-witness-matrix`. The
root-owned mode-`0600` allowed-signers file must map the two identities to
different public keys. Rename the first generated `.sig` before producing the
second one.

Live execution then requires both signatures, the allowed-signers file, a
third distinct operator, the exact approved reason/change ID, and two exact
execution confirmations:

```text
WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM=execute-dark-witness-real-host-matrix \
WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO=RH-001 \
make writer-witness-real-host-scenario-run ARGS='--scenario RH-001 --expected-commit <sha> --preflight /tmp/.../preflight.json --approval /tmp/.../rh-001-approval.json --observer-signature /secure/.../observer.sig --commander-signature /secure/.../commander.sig --allowed-signers /secure/.../allowed_signers --operator <third-name> --reason <exact-incident-reason> --change-id <change-id>'
```

Every approval nonce and preflight hash is consumed once. Every scenario
receives a unique `wwm_*` ownership tag and unique scenario-only FI/IR HMAC
keys. The replacement activates those keys only for the campaign; the original
Witness is proved not to contain their key IDs. Controller/WebApp copies exist
only in verified tmpfs and are never installed in application containers or
persistent WebApp configuration. Cleanup restores the exact pre-scenario
credential-bundle hash before reconnect/restore success is possible.

The controller keeps an owner-safe descriptor-held `flock`, a local durable
campaign journal, and a matching remote campaign marker. Intent is fsynced
before each credential, firewall, restore, and fault mutation. SIGINT, SIGTERM,
and SIGHUP enter the same cleanup path; SIGKILL leaves the durable journal dirty
and blocks every later scenario. After the dead controller releases its kernel
lock, reconcile only that journal from the exact clean commit:

```text
make writer-witness-real-host-scenario-recover ARGS='--campaign-journal /var/lib/trading-bot-witness-matrix/campaigns/wwm_<tag>.json'
```

Recovery stops requesters and sockets, revokes all scenario capabilities while
isolation remains, retains evidence, resumes the dark runtime, removes scoped
faults, restores the exact backup if needed, deletes only tag-owned auxiliary
databases, proves the full baseline, and reruns the exact-SHA preflight. Any
failed step leaves the journal dirty and does not authorize another scenario.

Live restore keeps a mode-`0600` phase journal under
`/var/lib/trading-bot-witness/restore-state`. Recovery locates the original and
candidate databases by PostgreSQL OID, so a crash between a database rename and
journal update remains unambiguous. The `--recover` action is idempotent, and
RH-012 exercises twelve guarded failure points from initial journal creation
through service restart before accepting the restore. RH-010 also uses a
disposable tmpfs PostgreSQL on localhost to prove backward-clock lease theft is
rejected, forward expiry advances the epoch once, and an old epoch cannot be
revived; it never changes the live host clock or production database.
