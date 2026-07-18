# Writer Witness Service Runbook

Status: historical dark-host evidence exists; the current release/runtime
attestation hardening is implemented only in the feature worktree and has not
been deployed to the replacement dark Witness; writer activation is not
authorized

## Current Remediation Boundary

The current worktree adds a fail-closed release/runtime trust contract. This is
source-level work: the focused verifier and deployment tests plus the complete
clean-commit source gate are the applicable local evidence. None of the
statements in this section claims that the
new release was installed on `185.206.95.94`, that its live state was attested,
that the twelve restore crash points or RH-010 ran, that Full Matrix ran, or
that external reviewers approved the final delta.

At the earlier reviewed snapshot, the focused release/deployment/Matrix verifier
groups completed `39`, `140`, and `97` tests with zero failures and zero skips.
A real
offline smoke also installed all `45` locked distributions and attested `2,771`
RECORD files inside a closed `3,087`-entry venv inventory before and after
`pip check`; that run also closed `22` installed venv ELF objects against `72`
release-bound system ELF objects. This is component-level source verification, not the final
combined exact-SHA source gate and not live-host evidence.

The two exact-SHA reviews of `21aae1de` subsequently rejected dark installation
and exposed the second-remediation defects recorded in roadmap Section 47.9.
After those source changes, the hermetic combined gate passed from the feature
worktree: `404` explicitly listed unit tests with zero skips, all `4`
guarded real-PostgreSQL tests, and the complete four-database failure drill. A
fresh minimal release also passed its closed manifest verification; the
SHA-256 of `release-manifest.json` is
`bc36beda23a49a9423aef26a60dee9eeb417b4136dcb664ab4bac56ff498562b`.
Those are historical results for the second-remediation snapshot, not evidence
for the current source. At exact SHA `bb3a17cf`, Gemini and Claude approved a
dark install, while both ChatGPT reviews rejected it. The fail-closed verdict
therefore controls. Roadmap Section 47.10 records the third source remediation;
its complete hermetic worktree gate passed `412` explicitly listed unit tests
with zero skips, all `5` guarded real-PostgreSQL tests, and the complete
four-database failure drill. A fresh minimal release passed closed-manifest
verification with `release-manifest.json` SHA-256
`7bd4fc9eb7d042fcdb3b9bcf8e84a9e46a67a15a839c841a380b023462665b9f`.
These are local source results awaiting a clean commit/push and independent
exact-SHA review; they are not deployment, live-host, preflight, external
approval, or real-host Matrix evidence.

ChatGPT Pro then rejected exact SHA `086affed`; roadmap Section 47.11 records
the fourth source remediation. Its hermetic source gate passed `421` explicitly
listed unit tests with zero skips, all `5` guarded real-PostgreSQL tests, and
the complete four-database failure drill. A fresh minimal release passed its
closed verifier with `86` manifest entries, `9` executable entries, and
`release-manifest.json` SHA-256
`9f63102e74f6dcb3526c00c63c832119f7e781d53cd1aae6116a00f885fdbf20`.
These are still source-only results pending independent review of the committed
exact SHA; they are not dark-host installation or RH evidence.

The reviewed deployment contract is:

- provisioning requires an offline wheelhouse whose exact file set and every
  wheel SHA-256 match the release-bound `wheelhouse.sha256`; missing and extra
  wheels both fail closed. `pip` is pinned in `requirements.lock` and is part
  of that wheelhouse, so there is no online installation or unbound bootstrap
  package path. The venv starts with `--without-pip`; the first installer bytes
  are executed directly from the already-attested pinned pip wheel under an
  isolated startup;
- ordinary release provisioning does not run a package manager, create the
  service account, or repair bootstrap directories. Those are separate,
  explicitly approved image/bootstrap responsibilities. Before its first
  persistent release mutation, provision acquires its host-wide lock, checks
  the pre-existing account and directory trust contract, and requires the
  exact read-only dpkg inventory digest supplied by the reviewed operator
  input. Missing or mismatched prerequisites fail closed without changing the
  host;
- the release verifier binds bytes and metadata: the canonical tree must be
  root-owned, directories must be exactly `0755`, data files `0644`, only the
  eight reviewed verifier/smoke scripts `0755`, and every file must have one
  hard link. Ownership, mode, inode and link-count drift fail closed;
- `python-runtime.json` is a release-bound Ubuntu 24.04 host-runtime manifest,
  not only an interpreter version marker. It binds the canonical CPython
  executable, the complete active standard-library tree (including
  `lib-dynload` and every external symlink target), the transitive ELF
  interpreter/shared-library closure, dynamic-loader cache/configuration and
  preload state, OS identity, and exact installed dpkg package
  version/architecture/status plus package ownership/checksum metadata. The
  installed virtual environment is then accepted only when its distribution
  set exactly matches the lock and every RECORD-listed file and every other
  venv node is closed into deterministic digests. Verification runs from an
  empty environment with the exact flags `-I -S -B -X utf8 -X
  pycache_prefix=/dev/null`; inactive system `__pycache__` files therefore
  cannot enter the import path, and the venv rejects all bytecode/cache nodes,
  `.pth`, `sitecustomize.py`, `usercustomize.py`, and unclaimed entries;
- provisioning writes a root-owned dynamic `runtime-provenance.json` that
  binds the release manifest, requirements lock, wheelhouse manifest, Python
  host-runtime manifest/digest, and resulting installed-runtime digest. This file is
  evidence about one concrete installation and is not a substitute for the
  source release manifest;
- one atomic `/opt/trading-bot-witness/active` pointer selects an immutable
  activation directory containing the release, its virtual environment, and
  runtime provenance. Code and runtime therefore change generation together;
  compatibility links must resolve through that same active activation. Every
  staged release/runtime/provenance file and containing directory is fsynced
  before the active pointer is changed, and the exact nftables gate passes
  before the new generation is exposed;
- activation is a journaled `begin -> late unit-intent snapshot -> publish -> credential finalize -> commit
  -> service completion` transaction over the
  code, venv, runtime provenance, runtime/client env, Nginx, systemd, and helper
  files. Candidate mode/size/digest bindings are written durably before the
  first publication, every managed publication has a crash-injection point,
  and the stable predecessor intent is recorded immediately before the first
  systemd mutation. Transitional and failed states are not replayable. Backup
  timers are frozen first; an already-running backup/offsite oneshot finishes
  under its old generation and is never stopped or replayed, while its durable
  rollback intent is normalized to inactive. All managed units are then proven
  inactive and runtime-masked before publication. Errors and
  handled signals roll back immediately. A mandatory boot recovery unit
  performs rollback reconciliation before Nginx/Writer start; a separately
  ordered periodic watchdog owns service completion. Both paths serialize
  with provision and HMAC rotation. An uncommitted generation is rolled back,
  but its journal remains durable in a rollback-service-completion phase until
  the exact predecessor load, active, and unit-file intent is restored,
  resampled, and compared by the transaction helper, and any required health
  check passes. A committed generation is likewise completed
  only after the exact services are healthy. Freshly absent units remain
  absent rather than being enabled as a side effect. SIGKILL or power loss
  therefore cannot erase the service intent needed to finish either direction;
- credentials use an independent two-phase prepare/finalize contract. The
  root-only bootstrap file is created exclusively, existing rotated HMAC state
  wins over bootstrap state, finalize and durable bootstrap-HMAC scrubbing occur
  before activation commit, and a committed journal is retained until service
  completion. A descriptor-held rotation
  lock spans the entire operation; reprovision cannot race HMAC rotation or
  resurrect a scrubbed key. Bootstrap, signing-key, and first-install TLS
  publication use reclaimable initialization namespaces so a crash cannot
  wedge a fresh retry. Database credentials are parsed from a closed rendered
  schema, are never evaluated by shell `source`, and PostgreSQL receives only
  generated SCRAM verifiers in role DDL rather than clear passwords. During a
  bounded Matrix overlap, the campaign key is current and the baseline key is
  previous; both must carry the exact same expiry. Any other expiry on a
  non-campaign credential fails service startup;
- all installed privileged Python helpers use the pinned system interpreter
  under `env -i`, `-I -S -B`, UTF-8 mode, and disabled bytecode, and reject an
  unisolated direct start. The Writer service starts through its exact
  activation venv Python with isolated environment handling; preflight binds
  `/proc` executable, argv, and forbidden-environment absence to that runtime.
  The offsite configurator parses S3 credentials through the same pinned clean
  system interpreter, and the online wheelhouse builder uses isolated pip then
  runs the verifier through the closed trust-anchor startup. Systemd also
  clears shell and ELF-loader injection variables and invokes shell helpers by
  absolute path; every secret-bearing shell disables xtrace before reading
  input. Before ordinary release mutation, a separately approved host
  toolchain digest binds every privileged executable used by provisioning and
  recovery to its resolved path, owner package, exact SHA-256, and safe
  metadata; its complete observable ELF dependency closure is bound with the
  same file/package evidence, along with required non-executable bootstrap
  packages. Preflight verifies
  the live Writer process maps against the exact release-bound system and venv
  ELF closure, binds every executable mapping's `/proc` device/inode to the
  currently hashed file, and rejects deleted, replaced, escaped, changing, or
  otherwise unbound mapped objects;
- attestation checks the effective systemd service, not only the unit file:
  its fragment path, absence of drop-ins, effective user/group/working
  directory/command, and required hardening properties must match the frozen
  contract;
- the complete effective nftables policy is read as JSON and compared with a
  release-bound semantic digest. Canonicalization removes only nft metainfo,
  runtime handles, and counter totals; tables, chains, rules, expressions,
  ordering, and all other policy-bearing values remain bound. Any extra or
  missing policy fails closed;
- live-restore cleanup owns a database only as the exact journaled
  `name + PostgreSQL OID` pair. A same-name recreated database, an unjournaled
  matching name, or any ambiguous inventory is never a cleanup target;
- a preflight artifact's five-minute authorization freshness is measured from
  its `completed_at`; `generated_at` records provenance and cannot make a long
  preflight appear fresh;
- offsite readiness requires more than installed helper bytes: the root-owned
  configuration and age-recipient files, enabled/active timer, last successful
  service result, and a secure fresh upload marker binding the latest local
  backup basename and checksum must all be proven.
- the Matrix authorization budget covers all network transports, not only API
  calls. The minimum is `64 MiB`, including conservative HTTP, SSH handshake
  and command, SCP, reconnect, abort-probe, cleanup, and final-postflight upper
  bounds; `16 MiB` is reserved only for cleanup. Ordinary cleanup may use at
  most `14 MiB`; an isolated `2 MiB` sub-reserve is usable only by exact
  emergency revocation. Every scenario has a
  Witness-clock `not_after` no later than 900 seconds after start. That expiry
  is carried by each scenario HMAC credential and is checked again with fresh
  database time inside the transition transaction before any state or receipt
  mutation. Cleanup has a separate non-renewable 900-second recovery window;
  every subprocess timeout is clamped to its remaining allowance. Expiry never
  blocks exact credential revocation, rollback, or campaign-tombstone release.
  The cleanup epoch and expiry are persisted before cleanup effects, so a new
  recovery-controller process cannot renew the allowance. After that deadline,
  only a narrowly bounded emergency path may prove exact campaign ownership,
  revoke its credentials, reconcile owned rotation state, and release its
  tombstone. That entire path shares one durably persisted, non-renewable
  30-second aggregate deadline and independent operation/byte counters; it may
  not resume ordinary inspection or baseline repair. SSH
  ControlMaster sockets live only on verified tmpfs, but DPI accounting
  charges a complete handshake upper bound for every SSH/SCP operation because
  OpenSSH can transparently recreate a dead master and still report success;
- the source gate re-executes under `env -i`, supplies only non-secret
  placeholders, checks a closed shell/Python syntax list without bytecode, and
  treats any unit skip, guarded PostgreSQL failure, or four-database drill
  failure as fatal. The guarded PostgreSQL gate contains five explicitly
  counted tests, including a real two-session row-lock barrier that advances
  database time past campaign expiry before the blocked transition resumes;

The two-person Matrix approval policy has an additional source-level custody
gate. `/etc/trading-bot-witness-matrix/allowed_signers` is not trusted merely
because it is root-owned: its exact bytes must also match
`TRUSTED_ALLOWED_SIGNERS_SHA256` in the reviewed Matrix runner source. That pin
is intentionally `UNCONFIGURED` until two independently custodied public keys
are supplied. Dark installation and guarded restore prerequisites do not need
those keys, but RH-001 and every later RH scenario fail closed until a separate
reviewed commit pins the canonical policy. Neither private key may exist on the
controller or in the repository.

Deployment of this exact reviewed release to the replacement dark Witness,
read-only live attestation, offsite configuration/proof, all twelve guarded
restore crash/recovery prerequisites, a fresh exact-SHA preflight, and external
re-review are still mandatory. No merge with `main`, first lease, WebApp
mutation, production writer activation, or Arvan/CDN change is authorized by
the source-level evidence.

The bootstrap trust boundary is explicit. This is host integrity attestation,
not TPM/remote attestation: the Linux kernel, root account, filesystem and the
root-owned Ubuntu/dpkg database are the trusted base. The release-bound
verifier detects byte/package/loader drift from an approved image, but cannot
prove an already root-compromised kernel or package database honest. A target
host may not generate or bless its own manifest during provisioning; the
manifest must be emitted on the reviewed canonical Ubuntu 24.04 build image,
included in the externally SHA-bound/reviewed release tree, and compared fail-closed on the
dark Witness.

### Host-runtime build and provision integration contract

On the approved image, generate the source artifact before the immutable
release manifest is built:

```text
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
  scripts/verify_writer_witness_runtime.py \
  --emit-system-runtime-manifest \
  --wheelhouse <already-attested-offline-wheelhouse> \
  > deploy/writer-witness/python-runtime.json
chmod 0644 deploy/writer-witness/python-runtime.json
```

The build must fail if the generated file differs unexpectedly from the
reviewed baseline. After the release tree itself is bootstrap-attested, and
before venv creation or execution of the pinned pip wheel, provision must run:

```text
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
  <release>/scripts/verify_writer_witness_runtime.py \
  --system-only \
  --system-runtime-manifest <release>/deploy/writer-witness/python-runtime.json \
  --expected-system-runtime-manifest-sha256 <release-bound-sha256> \
  --expected-lock-uid 0
```

OS/image bootstrap is deliberately outside ordinary release provisioning. On
the reviewed host image, retain the exact read-only package and executable
inventory and pass its SHA-256 through
`WRITER_WITNESS_EXPECTED_HOST_TOOLCHAIN_INVENTORY_SHA256`. The inventory binds
every privileged executable used by provisioning/recovery to its resolved
path, owner package, metadata and file SHA-256; package versions alone are not
sufficient:

```text
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
  <release>/scripts/verify_writer_witness_host_toolchain.py \
  --emit-inventory \
  > /root/writer-witness-host-toolchain.inventory.json
sha256sum /root/writer-witness-host-toolchain.inventory.json
```

The account/group and bootstrap directory modes/owners must already match the
runbook. A package, account, or bootstrap-layout change is a separately
reviewed reimage/bootstrap operation; the release provisioner only attests and
refuses drift. It must not repair those prerequisites in place.

Both the pinned-wheel bootstrap and later `pip check` must use `env -i`, pip
`--isolated`, and an explicitly
inserted, already-attested wheel/site-packages path. The installer uses `-I -B
-X utf8 -X pycache_prefix=/dev/null` after proving the new venv site-packages
directory empty; it deliberately does not use `-S`, because CPython 3.12 then
suppresses venv-prefix discovery and pip trips the host PEP 668 guard. Once
packages exist, both runtime attestation and `pip check` use the stricter `-I
-S -B -X utf8 -X pycache_prefix=/dev/null`. Normal
`<venv>/bin/python -m pip check` is forbidden because it processes ambient
startup/configuration before the attested path is established. Full venv
attestation before and after `pip check` must pass the system manifest and its
release-bound SHA-256. Provenance schema
`writer_witness_runtime_provenance_v3` must store that SHA-256 both at top level
and inside the exact fresh runtime object. It also stores the approved host
toolchain inventory SHA-256, and its verifier must receive both exact expected
values.

Provisioning holds the native POSIX record locks used by apt and dpkg for the
whole activation, including optional SSH hardening. It re-attests the exact
host-toolchain digest immediately before every activation journal boundary.
The journal durably snapshots the release-bound verifier and package-lock
holder before it is published, so boot/watchdog recovery can acquire the same
native locks and fail closed on toolchain drift before rollback or completion.
The real-host preflight independently runs the release-bound toolchain verifier
and requires the same digest in runtime provenance and in its retained output.

Manifest generation scans native ELF members inside the exact offline wheels
and adds every system library they require to the host closure. Full venv
attestation independently parses every installed native ELF file and rejects a
`DT_NEEDED` dependency or ELF interpreter not closed by either a RECORD-bound
venv ELF object or the release-bound system manifest. Hashing native wheel
files without closing their external shared libraries is not sufficient.

An intentional firewall-policy change is re-pinned explicitly, never during
provisioning. Capture `nft -j list ruleset` on the approved host and pipe it to
the isolated verifier with `--emit-policy-binding`; review the semantic diff,
then replace `deploy/writer-witness/nftables-policy.json` in a separate source
commit. The ordinary verify path still requires `--expected-policy-sha256` and
cannot self-approve observed host state.

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
- the active pointer resolves to one root-owned immutable activation containing
  the exact frozen release, virtual environment, and secure dynamic runtime
  provenance; compatibility links, the process executable, and the process
  working directory resolve through that same activation;
- the exact Ubuntu identity, CPython executable, active stdlib and
  `lib-dynload` trees, transitive ELF/shared-library closure, loader state and
  dpkg identities match the release-bound `python-runtime.json`; the installed
  distribution set and every RECORD-listed file match `requirements.lock`, and
  both recomputed host/venv runtime digests match the activation's provenance;
- installed helper and systemd bytes match the frozen release; the effective
  systemd fragment has no drop-ins and its identity, command, working directory,
  and hardening properties match the frozen service contract;
- the effective rendered Nginx configuration has only the two intended WebApp
  sources, UFW has only the exact approved ingress rules, and the complete
  effective nftables JSON matches the release-bound semantic policy digest;
- the installed offsite custody helpers (`writer-witness-offsite-backup` and
  `writer-witness-s3-put`) and both offsite backup unit files match the frozen
  release byte-for-byte, while `writer-witness-offsite-backup.timer` is enabled
  and active; its configuration/recipient metadata and the latest successful
  upload marker prove a fresh offsite copy of the latest checksumed backup;
- the packaged `libfaketime` library is a root-owned, non-writable regular file
  and is absent from the production Writer Witness and PostgreSQL process maps;
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

`generated_at` identifies when artifact construction started. The five-minute
execution freshness window is calculated from `completed_at`, which is written
only after every preflight check finishes; a slow preflight cannot consume most
of its work before the authorization clock starts.

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
   prove that no retry process or Matrix socket remains.
2. Capture, redact, hash, and retain the pre-recovery evidence before any
   restoration changes the failed state.
3. Reconcile any active live-restore journal by PostgreSQL OID. Delete only the
   exact journal-owned restore input; an orphan, symlink, hard link, or foreign
   input is an abort, not a cleanup target.
4. Stop the Writer Witness service if recovery started it, then recover HMAC
   rotation with `--leave-service-stopped`. Prove that the scenario key scope,
   staging files, claims, and deletion tombstones are absent while the service
   is still stopped.
5. Resume only the affected replacement-Witness PostgreSQL, Writer Witness,
   Nginx, and NTP components.
6. Remove only Matrix-owned tmpfs, loop, clone, and other isolated pressure
   artifacts.
7. Remove only Matrix-owned firewall and traffic-control objects, after all
   requesters and transient capability are gone.
8. Restore the replacement Witness to the pinned checksumed full-manifest
   `webapp:0:vacant`, epoch-zero baseline through the journaled live-restore
   path.
9. Prove both Witness manifests, FI health, stopped IR writers, disabled flags,
   restored paths, zero receipts, retained failure evidence, and absence of
   hidden Matrix resources.

The campaign may finish successfully only after the same full preflight passes
again and no transient credential, network rule, pause, mount, or clock
override remains. Original Witness must not be promoted automatically or used
as a second writer during rollback.

### One-scenario execution

The executor deliberately has no `--all` mode. Render one scenario first:

```text
make writer-witness-real-host-scenario-plan ARGS='--scenario RH-001 --expected-commit <sha>'
```

Before generating an approval, provision the controller's only accepted trust
policy from two independently held public keys:

```text
sudo python3 scripts/provision_writer_witness_matrix_controller.py \
  --observer-identity <observer-principal> \
  --observer-public-key-file /secure/public/observer.pub \
  --incident-commander-identity <commander-principal> \
  --incident-commander-public-key-file /secure/public/commander.pub
```

Each input public-key file must be a root-owned, mode-`0600`, single-link
regular file. The tool rejects equal identities or equal key material and
atomically installs the root-owned mode-`0600` policy at the canonical path
`/etc/trading-bot-witness-matrix/allowed_signers`; controller state and runtime
directories are root-owned mode `0700`. The two corresponding private keys
must remain on separate operator devices, outside the controller and the
repository. The runner refuses a non-canonical allowed-signers path.

The independent observer first binds out-of-band console, alternate
communications, maintenance window, DPI budget, and restore authorization to
the exact preflight and scenario:

```text
WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM=approve-one-dark-witness-scenario \
make writer-witness-real-host-scenario-approve ARGS='--scenario RH-001 --expected-commit <sha> --preflight /tmp/.../preflight.json --observer <name> --incident-commander <different-name> --reason <exact-incident-reason> --change-id <change-id> --out-of-band-console <provider-session> --alternate-communications <incident-bridge> --maintenance-window-start <timezone-aware-ISO-8601> --maintenance-window-end <timezone-aware-ISO-8601> --dpi-byte-budget <at-least-67108864> --restore-authorized-by <name> --output /tmp/.../rh-001-approval.json'
```

The observer and incident commander sign that exact owner-only JSON with their
two different SSH signing keys in namespace `writer-witness-matrix`. Rename the
first generated `.sig` before producing the second one; do not copy either
private key to the Matrix controller.

Live execution then reads only the canonical trust policy and requires both
signatures, a third distinct operator, the exact approved reason/change ID,
and two exact execution confirmations:

```text
WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM=execute-dark-witness-real-host-matrix \
WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO=RH-001 \
make writer-witness-real-host-scenario-run ARGS='--scenario RH-001 --expected-commit <sha> --preflight /tmp/.../preflight.json --approval /tmp/.../rh-001-approval.json --observer-signature /secure/.../observer.sig --commander-signature /secure/.../commander.sig --operator <third-name> --reason <exact-incident-reason> --change-id <change-id>'
```

Every approval nonce and preflight hash is globally single-use, not merely
single-use within one tag. A durable authorization-intent record reserves both
values before either consumption index is published, so a crash between those
writes cannot make either value reusable by another campaign. Every scenario
receives a unique `wwm_*` ownership tag and unique scenario-only FI/IR HMAC
keys. The replacement activates those keys only for the campaign; the original
Witness is proved not to contain their key IDs. Controller/WebApp copies exist
only in verified tmpfs and are never installed in application containers or
persistent WebApp configuration. Cleanup restores the exact pre-scenario
credential-bundle hash before reconnect/restore success is possible.

The controller keeps an owner-safe descriptor-held `flock`, a local durable
campaign journal, and a matching remote claim. The remote claim is one complete
owner-only `active.json` file published atomically and durably before any fault;
its identity is the exact `tag + commit + scenario + not_after` tuple. New
claims and authorization consumption fail closed after the replacement-Witness
clock reaches `not_after`. The scenario HMAC credential carries that same
expiry, and the transition transaction rechecks fresh database time before
reading/writing a receipt or changing Writer state, so a request delayed across
expiry cannot commit. Exact cleanup ownership and release remain available.
Release atomically moves that same record to the exact append-only
`releases/<tag>.json` tombstone. A lost SSH response is resolved by inspecting
and repeating only that exact identity; generic path absence is never accepted
as release proof. Intent is fsynced before each credential, firewall, restore,
and fault mutation. SIGINT, SIGTERM, and SIGHUP enter the same cleanup path;
SIGKILL leaves the durable journal dirty and blocks every later scenario. After
the dead controller releases its kernel lock, reconcile only that journal from
the exact clean commit:

```text
make writer-witness-real-host-scenario-recover ARGS='--campaign-journal /var/lib/trading-bot-witness-matrix/campaigns/wwm_<tag>.json'
```

Recovery follows the same nine-step abort order above and reruns the exact-SHA
preflight. Any failed step leaves the journal dirty and does not authorize
another scenario.

Live restore keeps a mode-`0600` phase journal under
`/var/lib/trading-bot-witness/restore-state`. Before publishing any replacement
dump, it durably records the exact owned input path and checksum in the journal;
publication is exclusive and owner-only. Recovery deletes only that exact
journal-owned input and refuses unjournaled, foreign, linked, or ambiguous
inputs. It locates the original and candidate databases by PostgreSQL OID, so a
crash between a database rename and journal update remains unambiguous. The
`--recover` action is idempotent. Before RH-001 is authorized, the dark target
must pass all twelve RH-012 prerequisite failure points: `input_validated`,
`candidate_created`, `candidate_restored`, `candidate_validated`,
`grants_applied`, `prepared`, `service_stopped`, `current_disabled`,
`current_renamed`, `candidate_promoted`, `candidate_enabled`, and
`service_started`; every injected failure must recover the exact pre-attempt
manifest before the final successful vacant restore.

RH-010 creates a disposable PostgreSQL cluster entirely on Matrix-owned tmpfs.
It sets `listen_addresses=''` and connects only through its private Unix socket;
no TCP listener is permitted. `libfaketime` is loaded only into that disposable
PostgreSQL child, never the production Writer Witness or production PostgreSQL.
Before and after the probe, the controller compares production state-manifest,
database-inventory, credential-bundle and PostgreSQL system-identifier hashes,
process identities/start ticks, and process maps. The probe proves backward
lease theft rejection, one forward-expiry epoch advance, and rejection of the
old epoch without changing the host clock or production database.

All statements in this section describe the source and required execution
contract. They do not claim that the updated helpers were deployed, that the
twelve live-restore prerequisites passed, that RH-010 ran on a host, or that any
RH scenario was authorized.
