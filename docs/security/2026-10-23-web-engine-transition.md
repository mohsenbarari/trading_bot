# Web Docker vendor transition: finite preparation and required apply gates

This is **not an applied remediation**. The initial preparation helper remains
read-only and intentionally has no `--apply` option. The separately reviewed
`web_engine_controller.py` and fixed local Ansible transaction implement the
finite operation below, but source/tests are not operational evidence. No host
repository, package, daemon, container, Writer, DNS, image or application source
has been changed by authoring these files.

## Why a same-OS vendor transition is now feasible

[Docker's Ubuntu support matrix](https://docs.docker.com/engine/install/ubuntu/)
explicitly supports Ubuntu Resolute 26.04. Official Resolute stable metadata was
verified against Docker's signing fingerprint
`9DC858229FC7DD38854AE2D88D81803C0EBFCD88`; the InRelease timestamp was
2026-10-23 03:57:03 UTC. Its amd64 Packages.gz was 36,769 bytes, SHA256
`9cb4a65b8750a4ce7d6894cf7d111079b86e8736c1711cb50724632589a17497`.

The preflight pins exact version/size/SHA256 for five new official CE packages
and four currently installed Ubuntu rollback packages. The required CE set is
Docker CE/CLI 29.9.2, containerd.io 2.4.2, Compose 5.6.1 and Buildx 0.38.1, all
for `ubuntu.26.04~resolute`. Rootless extras are not an implicit addition.
Ubuntu rollback payloads total 88,152,580 bytes and remained in apt metadata;
the host's apt archive cache was empty. **Availability metadata is not a retained
rollback payload**: download, verify and retain all four before any transition.

[Docker 29 release notes](https://docs.docker.com/engine/release-notes/29/)
and actual installed/server/package proof must establish the security fixes.
An exact version pin alone does not prove the active runc binary or daemon.
No cross-distribution repository, forced phased update or OS migration is needed.

## Actual read-only Web inventory, 2026-10-23

- Ubuntu 26.04, active kernel `7.0.0-27-generic`.
- 27 existing containers: 26 running, one stopped Iran migration. Five Compose
  projects: Market 7, current application 4, Iran staging 5, older three-site
  staging 1 plus 10. Older staging is **still running**, not disposable by default.
- Docker already uses the **containerd image store**: `overlayfs`,
  `driver-type=io.containerd.snapshotter.v1`, DockerRootDir `/var/lib/docker`,
  live-restore false. Do not switch to classic overlay2 or force a feature flip.
- `/var/lib/docker` about 0.95 GB; `/var/lib/containerd` about 10.94 GB.
  Both are on the root filesystem. Both native config files were absent.
  Retain both stores, namespace/snapshotter and any discovered configuration.
- Market staging-shadow root is about 48.12 GB: PostgreSQL 12.86 GB, capture
  1.70 GB, state 27.88 GB, calibration 55.8 MB, sessions about 250 KB. The name
  "staging" does not imply these live production bindings may be deleted.
- Market production root about 6.33 GB, application production data 1.75 GB.
  Root filesystem free space was about 52.67 GB. **Do not duplicate all 48 GB**
  beside a full engine backup. Current allocated sizes are not future headroom.
- Current application named volumes remain under Docker's volume directory;
  Market PostgreSQL, capture, state, calibration and Telegram sessions use bind
  paths outside the engine store. A DockerRootDir backup is not a Market backup.
- The Web maintenance marker exists. Native Bot journal/source-lock paths and
  controller/two-host state were absent. Do not invent/adopt a Bot authority
  receipt to make a reused guard pass.

## Preparation tool contract

`deploy/maintenance/web_engine_preflight.py` checks a private SHA256-bound JSON
manifest (`web_engine_preflight/1`): operation/deadline, native Web host identity,
current marker device/inode/size/digest, both engine-root identities/UUIDs, and
all 27 public container identities, images, restart policies and six-field mount
sets. Mount ordering alone is ignored; duplicate destinations and real drift
are rejected. Optional offline-deb verification streams bounded files and
checks `dpkg-deb` name, exact version and amd64 architecture.

Default execution is read-only. It makes **no** backup, restore, quiescence,
source-activation or security-closure assertion. It never imports the app,
opens its environment, returns the private manifest, or prints subprocess
exceptions. The accompanying Ansible preflight uses existing strict SSH and
refuses `maintenance_apply=true`; independently reviewed staging must place the
public helper/private manifest before that preflight can be used remotely.

## Finite operational controller — separate from preparation

`web_engine_controller.py run` defaults to read-only validation, including the
strict existing SSH lane's capacity/access probe. Only its explicit `--apply`
path creates a new nonce-bound reservation/journal and invokes the fixed local
Ansible child with all three lock descriptors inherited. Direct playbook
invocation lacks those kernel locks and is refused. It never repairs/adopts the
older Market marker or performs a Writer/role transfer.

The private manifest is `web_engine_transaction_plan/1`, supplied together with
its SHA256. It binds native host/deadline, current marker identity, both physical
store roots, 27 container identities/images/mounts/network modes/configured
ports/network membership, 10 native capture/timer targets, 4 engine/nginx
enablement intents, 8 datastores/probes, immutable public source files and exact
old/new offline debs. It refers to the actual existing application backup and
off-host-copy receipts. No placeholder Market/off-host `PASS` receipt is used.

Admission proves containerd namespaces exactly `moby` and `moby_history`, only
the expected running Docker task IDs in `moby`, no tasks in `moby_history`,
healthy overlayfs snapshotter, systemd cgroups v2, and the existing image store.
Unknown non-Docker tasks are refused, never stopped as collateral work.

The real existing Market backup tool is called **inside** the reservation,
after every non-database container/native writer is stopped and only the eight
frozen databases remain running. It creates its own strict dump/restore/journal
and cleanup proof; its separate verifier must pass. The exact dump digest and
receipt become owned transaction evidence. There is no hot-backup row-count
waiver. The live Market database has no published host port; the source phase
rechecks that fact. Known root crontab and `/etc/crontab` metadata had no project
or PostgreSQL writer launchers at inventory time.

`web_engine_offhost.py` actually receives encrypted bytes over the configured
strict SSH lane, allocates an exclusive private operation/nonce directory,
streams a bounded ciphertext, verifies digest/length, and fsyncs artifact and
receipt. It can independently reverify the retained copy before packages.
It never reads an encryption key or materializes plaintext off-host. The hard
ciphertext cap is 10 GiB with a 2 GiB remote free-space floor; absent capacity
or access fails before quiescence, and transfer failure fails before packages.
The Web preflight reserves conservative space for the logical dump, compressed
archive, encryption output and an 8 GiB floor. No blind 48 GB directory copy.

The quiesced tar includes both engine stores, exact current session/secret
directories, original engine configuration where present, and the freshly
verified Market dump. GNU tar compares the archive against the still-quiescent
originals without listing contents. The existing authenticated encryption tool
then verifies decryption; the redundant owned plaintext tar is removed only
after verified off-host ciphertext exists. The PostgreSQL dump and authenticated
ciphertext remain private, bounded rollback evidence.

The installed Ubuntu containerd configuration was absent, but the CE package
legitimately installs a vendor default. The controller derives allowed exact
configuration bytes from the **hash-pinned `.deb` payload**, stores their digests
in its journal before installation, and rejects non-vendor drift. It does not
force a snapshotter or containerd-store flip. This packaging behavior is visible
in [Docker's official Debian packaging rules](https://raw.githubusercontent.com/docker/containerd-packaging/main/debian/rules).

The full sequence is:

1. Obtain fresh signed official metadata and all exact new/old payloads; verify
   signatures/digests/architecture and simulate the **local-deb** transaction.
   Reject unrelated removals/upgrades. Avoid global apt sources/update and
   automatic recommends; remove old Compose only under the reviewed conflict
   plan. No autoremove, purge, prune, new image pulls or container recreation.
2. Freeze a fresh complete runtime/unit/control baseline. Include four enabled
   Web timers and their static targets, disabled legacy capture services and
   runtime authority drop-ins, daemon/socket enablement and all five projects.
   Inventory cron/path triggers separately. Do not generalize the Bot's 15 IDs.
3. Reuse fresh matching application backup/isolated-restore/off-host evidence.
   Validate the real existing Market role-file metadata and release binding
   without emitting environment data. Preserve bind data in place: package
   maintenance is not data migration.
4. Under exact current marker/source coordination and a new, exclusive,
   maintenance-schema reservation plus durable phase journal, stop captured
   timers/targets, set all restart policies to no, stop prior-running non-database
   containers gracefully, and prove only the eight databases remain running.
   Create and verify the real strict Market dump/restore under this reservation,
   prove its labelled test resources were removed and all 27 identities still
   match, then stop the frozen databases. Prove all identities quiescent. Stop Docker
   socket/service before containerd. Never repair an old marker or adopt an
   unrelated reservation. A controller crash must leave a visible safety fence.
5. Back up **quiesced** Docker and containerd stores/configuration with ownership,
   xattrs/ACLs and filesystem semantics preserved. Apply measured size, capacity,
   timeout and free-space floors. Encrypt sensitive archive/session/config
   artifacts using the existing `crypt_market_pipeline_backup.py`; verify and
   retain an off-host encrypted copy. Never dump env/session/key contents into
   logs or public manifests. A live ad-hoc directory copy is not this proof.
6. Transition only exact approved local packages with automatic service starts
   suppressed (`policy_rc_d=101`, `NEEDRESTART_MODE=l`). This is a vendor/package
   replacement, not an ordinary Ubuntu patch. Recheck unchanged control facts.
7. Start runtime, prove the **same containerd store** and all 27 original IDs,
   images and mounts while containers remain stopped. Start only prior-running
   datastores first and test PostgreSQL/Redis readiness, then the remaining
   prior-running containers and captured services/timers. The stopped migration
   must remain stopped. Restore exact policies/enablement only after health.
8. Prove real daemon/containerd/runc versions and intended CVE closure; preserve
   Writer/DNS/capture ownership. Release only this transaction's reservation
   after durable success. On ambiguous failure, retain reservation/journal and
   fail closed; no automatic re-creation, downgrade or broad cleanup.

Retained old debs alone do **not** prove containerd 2.4 to 2.2 downgrade safety.
The pre-upgrade quiesced engine-store backup is the rollback boundary. Any store
restore is a separate bounded recovery preserving outside-engine Market data,
not an improvised package downgrade. Automation remains paused until all open
security gates and the separately requested clean-unit acceptance are complete.

## Remaining operational admission, not silently satisfied by tests

- Independent source review and source-controlled integration; then stage a
  private immutable source bundle and exact signed-source deb payloads.
- Fresh private actual manifest, real application backup/off-host receipts,
  actual existing Market role metadata and release/image binding, encryption
  key managed by the existing tool, and verified temporary SSH lane valid for
  the whole window. Do not invent receipts or change legacy authority.
- Close the unrelated read-only image scanner's Docker socket tunnel before
  maintenance. Root owns that coordination; authors never execute this tool.
- Native default full guard must pass before explicit apply. Observe and retain
  actual journal, backup, remote verification, active daemon/runc/containerd,
  exact restored identities/intents and API health evidence afterward.
- On failure the durable reservation and phase remain. No generic automatic
  adoption/retry/downgrade exists; a separately reviewed bounded recovery is
  required. A completed operation's last restorable encrypted backup must stay
  until superseded by another verified backup; remove superseded artifacts by
  the project's bounded retention policy, never retain every attempt forever.
