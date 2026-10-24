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

Actual combined application evidence identifies Native's `foreign` role as
project `trading_bot`, but Web's `iran` role as project `current`. These real
identities are preserved, not relabeled. Web maintenance requires the four
**Web/iran/current** backup digests off-host; the older receipt covering only
Native/foreign backups does not satisfy this gate. The finite
`web_application_backup_offhost.yml` verifies the original combined receipt and
four exact existing Web files, fetches only those four into a new private Native
directory, verifies size/SHA256 and fsync, then exclusively publishes
`security-web-engine-20261024-app-offhost.json`. It never overwrites the old
combined or old off-host receipt and never starts a database or changes runtime.

The backup tool's explicit `--bluegreen-source-env` tuple correlates the backup
journal with a real immutable Market image for **host-maintenance correlation**;
it is not a claim about a future activation target, that the current processor
uses this build signature, or that all seven Market components use one version.
The current processor lacks its input-signature label,
so no value is invented for it. An actual active capture image with all three
authentic revision/tree/input-signature labels is selected instead. Admission
requires that exact image in the frozen active Market baseline and re-reads only
those three public OCI labels. The last active-daemon quiesced checkpoint durably
records that exact proof. Only the same owned stopped-engine phases may rely on
it while Docker is intentionally unavailable; active-daemon phases and abort
restoration re-read the actual labels. The unchanged 27-row baseline records the actual
mixed images independently. The receipt basename remains the existing backup
tool's mandatory `market-pipeline-backup-receipt.json`.

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
- Preflight checks private paths/metadata, not env/key contents. It never runs a
  custom `runpy` validator or reads a key merely to diagnose validity. The normal
  approved backup create/verify and encryption operations validate internally.
  Invalid role data can therefore fail in `writers_quiesced`, which deliberately
  has no automatic restore; retain its owned evidence/fences and use reviewed
  recovery. This is not a claim of failure-free prevalidation or full automation.
- Close the unrelated read-only image scanner's Docker socket tunnel before
  maintenance. Root owns that coordination; authors never execute this tool.
- Native default full guard must pass before explicit apply. Observe and retain
  actual journal, backup, remote verification, active daemon/runc/containerd,
  exact restored identities/intents and API health evidence afterward.
- After the package boundary, ambiguous failures retain the durable reservation
  and phase. No generic automatic adoption/retry/downgrade exists; a separately
  reviewed bounded recovery is required. A completed operation's last restorable encrypted backup must stay
  until superseded by another verified backup; remove superseded artifacts by
  the project's bounded retention policy, never retain every attempt forever.

## New 24 October window: bounded work and truthful recovery

The user clarified that the approved 90 minutes starts with actual downtime.
The conservative fresh06 outer deadline is **2026-10-24 07:00:00 UTC**. Only the
fresh `host-patch-06` reverse-SSH lane receives that expiry; previous identities
retain their old expiry (`05`:06:00, `04`:05:20, `03`:04:41, `02`:01:30 UTC) and are not extended or adopted. Native's completed
kernel transaction and its original bound guard are not modified by this work.
The exact06 launcher additionally refuses **start before05:30 UTC**, while
read-only checks remain possible earlier, so this fixed07:00 outer deadline
cannot admit more than90minutes of project downtime. The controller's unchanged
65-minute minimum start budget gives a latest first admission of05:55 UTC.
The unused05 plan (`c1c295242fe7ab8ce3355b25af1fbfe2dec88829e3f56bd591d453ab302a7f63`)
must be proved unapplied and preserved whole before fresh06 preparation; its
sealed source and old06:00 expiry are never overwritten, extended or adopted.

Root explicitly serializes **one** preparation invocation for this fixed
workspace; this is not a concurrency guarantee. With no final plan, reservation
or pending operation, that invocation may refresh only the fixed authenticated
13 public sources, empty namespace marker, metadata inputs and two metadata
receipts, then independently checks all staged hashes before execution. A final
plan ends the play before any copy; its source bundle is never refreshed.
An unused finalized previous-window plan must be preserved by separately
reviewed exact-identity retirement, not edited, adopted or overwritten.

The actual Web systemd no-job tuple `(uo) 0 "/"` renders as `Job=` even with
`show --all`. Rescue therefore requires exactly explicit `LoadState=loaded`
plus `Job=` or `Job=0`; missing, unloaded, duplicate or nonzero job metadata
refuses before restoration. All daemon-side ambiguity restrictions still apply.

Preparation downloads and verifies all nine old/new public packages, checks the
actual SSH/off-host capacity, freezes the real metadata and validates the source
before the first downtime action. The remote **12 GiB** free-space admission
floor remains unchanged; a shortfall blocks admission, not a reason to weaken it.

The apply controller requires at least **65 minutes remaining** before creating
its reservation. Its first Ansible child runs only `pre_install`: quiescence,
strict Market dump/restore, quiescent store backup, encryption and verified
off-host retention. That entire child is cut off at deadline minus **35 minutes**,
inside a uniquely named transient systemd **scope**, retaining the three lock
descriptors through direct exec. Each stage has finite RuntimeMax/stop limits,
whole-control-group termination and no service restart mechanism. Before any
automatic restore, the parent verifies exact nonce/digest ownership metadata,
stops only that scope and proves its cgroup absent or recursively unpopulated;
a leader/PGID exit alone is never accepted. A foreign or still-populated scope
retains the durable fences without attempting restore. The same parent keeps
all three kernel flocks. The second `install_restore` child
starts only when that 35-minute margin remains. Immediately before recording
`installing`, after package/backup verification, a fresh check requires at least
**30 minutes**. These are conservative admission bounds, not a measured promise
that a 12 GB backup or vendor migration will finish in the window.

If the first child fails or time is insufficient **before** `installing`, only
the original still-fenced parent may consider the narrow abort/restore path. It
is admitted only from `reserved`, `backing_up` or `backup_ready`, with no pending
jobs on all 10 captured native targets and three engine units; both backup phases
also require all 13 targets inactive/failed. Intermediate quiescing, Market dump,
database-stop and engine-stop phases require separate recovery and retain fences.
This distinction is necessary because a daemon-side Docker stop can outlive its
client; a cgroup kill or fixed sleep is not proof that accepted daemon jobs ended.
See the [upstream stop implementation](https://github.com/moby/moby/blob/docker-v29.1.3/daemon/stop.go)
and [kill implementation](https://github.com/moby/moby/blob/docker-v29.1.3/daemon/kill.go).
The admitted abort/restore path
requires the exact old four package versions, unchanged source/marker/control
proof, unchanged 27 IDs/images/mounts/networks and compatible original/no restart
policies. It restarts the old engine, proves the original containerd store and
task inventory, starts the eight original datastores, checks PostgreSQL/Redis,
then starts only the 26 prior-running containers. Real container-loopback API
smoke must pass before exact restart policies and native daemon/timer intent are
restored. The stopped Iran migration stays stopped; enablement is never changed.
The whole rescue has a 30-minute maximum within the remaining outer deadline.
Only full baseline/health proof permits `aborted_restored` and deletion of its
own two nonce/inode-bound fences. That outcome is **not security remediation**;
the command returns failure. Failed or ambiguous rescue keeps all evidence and
fences. SIGKILL/controller loss is not claimed to be automatically recoverable.

Once `installing` is recorded, automatic old-runtime rescue is forbidden even
if apt failed before reporting success. The separately reviewed recovery decision
must distinguish two cases:

1. **No application/native writer has restarted:** verify original control and
   ownership, stop any engine/socket, preserve the failed stores and package
   diagnostics privately, reverify both retained ciphertext copies, decrypt to
   an owned bounded staging location, and verify the authenticated archive. A
   proposed offline old-four/new-five package reversal must be simulated and
   constrained before execution, with service auto-start suppressed. Restore
   **both** original quiescent engine stores and original configuration together;
   never run containerd 2.2 against a potentially modified 2.4 store. Restore
   sessions/configuration only to the exact originally bound paths. Then prove
   old daemon/store/27 identities before the same DB-first/health/policy/trigger
   sequence. No recreation, role change or blind data-directory overwrite.
2. **Any writer has restarted or that fact is unknown:** the old store snapshot
   is not a safe live-database rollback. Preserve current stores and bind data;
   choose a reviewed forward repair or a coordinated application/Market restore
   with explicit data-loss/consistency analysis. The old debs and existence of an
   archive alone do not authorize a downgrade or prove that this can fit the
   remaining window.

The second recovery is a concrete operator review boundary, **not an implemented
automatic rollback tool**. If that residual risk and the measured preparation
facts do not support recoverability before the deadline, stop at preparation;
do not begin quiescence merely to meet a security-completion target. Neither
these tests nor the 30-minute reserve guarantees a 90-minute recovery outcome.

## Daemon-only scope proof after the04 launch was refused

The attempted04 start was refused **before** commit or launch. It was not
retried through another executor. Read-only inspection then authenticated the
actual Web prepared controller as
`96bab1152eb4ce105e56717e87c27d32836a8c06a0ae01962178dd034f3df367`
and the unused04 plan as
`5ad57c8c91d758df46a50c5cd661f2c35665e5561b671b669f12ba40f57fb373`.
The05 operation must create a new immutable plan; it never extends or adopts04.

The source call path is specifically Docker maintenance, **not kernel reboot**:

- The launcher executes only this exact controller's `run --apply`. Its fixed
  local transaction has no reboot module or reboot/shutdown/poweroff command.
  The reachable backup/crypto/release-primitives sources were also checked;
  reboot text appears only in explicit `reboot_performed=false` metadata and
  descriptive non-reboot comments, not an executable reboot path.
- `apt_argv` installs only the five SHA-pinned local CE/CLI/containerd/Compose/
  Buildx packages and removes the four exact old Docker/containerd/runc/Compose
  packages. It uses `--no-download --no-install-recommends`, not full upgrade.
  `apt_scope` refuses any simulated installed/removed name outside those exact
  sets. No kernel, initrd, bootloader, systemd or general OS package is admitted.
- Immediately before the actual package command, `install` exclusively creates
  its own `/usr/sbin/policy-rc.d` containing `#!/bin/sh\nexit 101\n`, fsyncs it,
  sets `NEEDRESTART_MODE=l` and `DEBIAN_FRONTEND=noninteractive`, and afterward
  removes only that same inode/content. Existing policy is a refusal, not an
  overwrite. This suppresses package service auto-start; the original captured
  engines and containers are restored by the guarded DB-first transaction.

All nine retained `.deb` files were independently rehashed against the prepared
plan, then their control archives were read in RAM with a1MiB streamed output
cap and15-second deadline, without extracting or installing files. Exact script
hashes below identify the actual authenticated public package scripts:

| Package | Maintscript | SHA256 |
| --- | --- | --- |
| New `docker-ce` | postinst | `31fe64d3324bd501e2b87af9c339772e1cbdc05197140efeba4712cf38395bc8` |
| New `docker-ce` | preinst | `795697b58c15a48888f2b64113a6f8e284acdc07c1cc9e6657c4ff96e96ba034` |
| New `docker-ce` | prerm | `46e1a9a9f0c96c4a2e7eb7c3d380aab2919c617ecfae89d0658c3db9ceb6ee4e` |
| New `docker-ce` | postrm | `44264f6685872e15b1ac067a1a65fc6dd1aa248164ade6576fe6aee3740c0b42` |
| New `containerd.io` / old `containerd` | postinst | `8eb27a42216980e4718b36df9bca1ca5106f6ee6cd5e530b01317ed6b57fd837` |
| New `containerd.io` / old `containerd` | prerm | `fb57783af42240ca75150d5befc5a7cf2c353088dbf237ca2d78401929b83332` |
| New `containerd.io` / old `containerd` | postrm | `07708390cab040da3c2cf628f04dd1ba71299bf5fcd5ec5e039712961ced75a2` |
| Old `docker.io` | postinst | `eb565a299e76c65bca86708766bf2d4c158b83fb7b25b7eea5098da4f522df15` |
| Old `docker.io` | preinst | `0126ea7a95f68fd15cf0d621e1d745b49ccd7f3b0e8b4fc42f8b5e79f57af3a1` |
| Old `docker.io` | prerm | `4e2e352933d74f3c35486e34042cf9a582dcef01efe4405e6610fec647cd9c35` |
| Old `docker.io` | postrm | `5b2e41555d6fdd376fe96ea0ed97e06f8b0510327891c39b7a32bff343680ed7` |

New CLI/Compose/Buildx and old runc/Compose have no such maintscripts. None of
the inspected scripts contains reboot/shutdown/poweroff/halt actions. Service
actions name only Docker/containerd and use `invoke-rc.d`, `deb-systemd-invoke`,
`deb-systemd-helper` or `systemctl daemon-reload`; they do not remove collector
authority drop-ins or their condition path.

Both actual public runtime collector drop-ins contain exactly
`[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n`.
Their SHA256 is
`7c76f1096916e6d7541684abee33514e281aaa81b096098b633f0dd4c5498066`,
at `/run/systemd/system/{coin-capture,market-channel-capture}.service.d/market-container-authority.conf`.
The captured device/inodes are29/12386748 and29/12386747 respectively. A Docker/
containerd restart or PID1 daemon-reload does not clear `/run`; the transaction
also rechecks original unit fragment/drop-in hashes, disabled collector intent,
all27 original container IDs/images/mounts and exact26-running baseline.

This evidence narrows the same reviewed executor to **daemon-only**. It is not
a claim that an unexpected host reboot is impossible, that host reboot is
authorized by this launcher, or that the separate persistent collector boot
inhibition gap is closed. That gap remains a blocker for the later Web kernel
reboot. If the execution reviewer still refuses, do not bypass it; close the
boot inhibition through reviewed source and refreeze a fresh plan first.

## Fresh06: independent artifact and diagnostic bounds

Before05 activation, review found that the former `bounded` helper applied its
2MiB diagnostic `RLIMIT_FSIZE` to every descendant file, including a real
`pg_dump` or unpacked Docker executable. No downtime or package write occurred.
The corrected independently reviewed controller is
`966970743e3e96a1b7ffac3665486d8b7db589b952751f49c6568d45005eec43`.
Fresh06 alone binds it. Captured diagnostic stdout is now read incrementally
from a pipe and refused at maximum+1 bytes, before full collection; it retains
its 2MiB maximum, total monotonic deadline and exact-child process-group cleanup.
Separate finite per-file limits admit the actual intended writers: Market
create/verify16GiB, encrypted engine archive10GiB, exact offline APT512MiB.
Direct tar/vendor-payload FDs retain their earlier finite payload limits.
Neither an unlimited file allowance nor a larger diagnostic capture is used.

Owned synthetic subprocess regressions actually wrote a3MiB private artifact
with tiny stdout, refused a2MiB artifact overflow, and killed/reaped an output
flood after at most4097 captured bytes for a4096-byte cap. These establish the
limit separation, not a successful production dump, installation or recovery.
All backup/capacity/restore/source/authority gates remain required on real run.

## Fresh07: real Ansible locale admission, terminal06 retained

Actual06 stopped at `reserved`: Ansible rejected the C locale before the first
quiescence checkpoint. Its authentic terminal receipt is `aborted_restored`,
`aborted_from=reserved`, `security_transition_completed=false`; it is not a
successful upgrade and is not an unused plan. The failed exited unit, original
plan (`dc01b663f6fa3e23762804b5c6cf0f0ec7b0e45d14aeb820d6641c905e8d275a`),
source and journal remain intact. Fresh07 preparation requires that exact
terminal identity, no old reservation/source fences and the actual unchanged
27-container baseline. No terminal record is deleted, reset or adopted.

Fresh07 uses the separate private workspace
`/root/.local/security-web-engine-20261024-task07`, nonce
`304b2ec68d304738a43794f5a666b6c8`, with the SAME07:00 UTC deadline and
05:30 not-before boundary; no earlier key expiry is extended. Its controller
`81b8983cb0f790c95d0e51c3f3225bd60aa5eb8ef5037dd9c20f8dbc549e64b9`
uses one shared `ansible_environment(plan)` for preflight and both real stages.
It sets `LC_ALL=C.UTF-8`, preserving Ansible's mandatory encoding guard.
Before any reservation or downtime, the real `ansible-playbook --version`
must succeed under that exact environment and report core2.21.5. This is
operationally read-only; trusted Ansible may create private scratch/cache
files. Metadata CLI locale behavior and every artifact/diagnostic bound are
unchanged. Actual backup, restore and installation evidence is still required.

## Current-profile successor: cold archive planning only

The original C07 source, plan, expired deadline and aborted journal remain
historical evidence. They cannot be made runnable by replacing a deadline or
pretending the QA/Capture changes never occurred. The new pure current-profile
helper derives the accepted 27-container intent: parked10, retained17/16 running,
six running datastores and the functional Capture06 image. It binds managed04's
configuration identity separately from the source image's OCI provenance and
validates the actual HOT3 control chain as backup data. It grants no new window,
current runtime attestation, package-write authority or cold-archive proof.

The first successor packet, `web_engine_cold_archive_plan.py`, is also pure and
has no CLI/apply, host, SSH, package or reboot action. It validates that profile
and proposes the exact cold scope: both engine roots, Market sessions and secure
Market configuration, plus only explicitly present Docker/containerd optional
configuration paths. `du -sx --block-size=1` over those operands supplies allocated
bytes without opening file contents. The existing 24-GiB raw scope bound remains;
the archive preserves ACLs/xattrs/numeric ownership/sparse data and does not cross
other-device mounts beneath an operand. This is not a whole-host root backup.

Historical HOT3 data and its encrypted Native copy stay retained; neither its old
deadline nor its aging snapshot becomes new engine-window authority. The current
successor requires a **fresh quiesced database backup/restore/verify proof** and a
**separate cold engine-store archive proof**. The fresh managed04 PRIMARY backup
uses the configuration/image identities separately, while all writers are stopped
and only the six intended datastores run. Its receipt and journal remain normal
v1.4-quiesced/v1.2 evidence; no hot receipt is relabelled.

After that normal strict verification, an explicit typed fresh-dump operand may
be included with the cold engine roots in one encrypted offhost archive. Its
OP08/nonce, release/tree/image identity, restore/cleanup status, receipt/journal
SHA256, actual path/size/hash and allocated bytes are bound. Only the normal dump
basename under this fresh operation's own `market` directory is allowed; arbitrary
paths and the old HOT3 artifact refuse. Its measured allocation counts toward the
same 24-GiB raw scope. Free space must be sampled again after creating the dump.
The plan records `hot_dump_embedded_in_cold_archive:false` and
`fresh_quiesced_dump_required:true`; an initial proposal without the fresh operand
cannot satisfy the later package-write gate.

This retains the earlier controller's legitimate shape of embedding a newly
verified logical dump alongside the quiescent stores, without duplicating old
HOT3 data or creating another receiver/key protocol. Before any package write,
the combined cold archive must still be created under owned quiescence, compared
against its sources, encrypted/reconciled by the normal tool and independently
verified durable offhost. External Market data remains preserved in place;
fresh backup age/schema admission and data-first recovery are explicit gates.

The proposed cold destination is a new cipher-only namespace on the existing
Finland host `65.109.214.203`, not an application deployment or target stack.
Its caller-SHA-bound public receipt uses `web_engine_cold_destination/1` with
host/port/user/root, full directory binding, four-field mount identity and host
fingerprint. The source plan checks the reviewed root ext4 mount UUID
`ce366399-3430-441e-8a6f-6f5621f0c7a2`, device2049, approved ED25519 fingerprint
and root-only `0700` directory. It does not reuse Native's attached-backup UUID
or claim that this proposed directory, fresh transport or receipt already exists.
Actual destination staging and the dedicated finite SSH lane remain separate.

Free-space samples are caller-reviewed metadata, never reservations. The finite
attempted compressed-archive cap is the smallest of the existing 10-GiB bound,
the destination's available bytes after an 8-GiB reserve, 1-MiB control allowance
and cipher overhead, and Web's capacity for simultaneous compressed plaintext
and ciphertext with the same reserve. No compression ratio or actual cipher fit
is inferred from allocated input size. A fresh finite controller must remeasure
and enforce stream/disk bounds, actual cipher fit and durability. A new approved
window, fresh transport, live runtime/storage checks and restore reserve are
required before mutation. Persistent boot guards and any Web kernel reboot
remain separately admitted work; this source packet authorizes neither.

## Fresh OP08 executable adapter (source reviewed before any window)

`web_engine_current_controller.py` and `web_engine_current_transaction.yml`
provide a separate executable path. They consume the reviewed current profile;
they do not run or patch C07's old invariant, entrypoint, receipts or deadline.
Only the stable low-level package checks, App backup gate, native Ansible scope
executor and selected owned-backup resource helpers are reused. The new plan,
journal, source fence, supervisor and private backup workspace have OP08 names.
The permanent production marker is opened without creation and remains intact.

The caller stages a **fresh**, root-only task08 directory with the public source
tree at `source/`, the fixed fourteen controls at `evidence/<logical-key>.json`,
the destination receipt, fresh public transport receipt, fresh App receipts and
`window.json`. Every staged file is root-owned `0600`; directories are `0700`.
Existing workspaces, partial plans, fences or backup work directories are never
retried or adopted. The source inventory is exactly the controller's `PINS` plus
its two `OWN_SOURCES`, all SHA256-bound by the new window. The window schema is
`web_engine_current_window/1`: exact OP08/nonce, not-before/deadline at most
90 minutes apart, engine-package permission true, and reboot, kernel packages,
recreation, DNS, capture ownership and automatic downgrade permissions false.
At actual start at least 65 minutes must remain. No old window is authority.

Fresh normal App backup evidence was independently produced at
`2026-10-24T20:18:21.418579Z`: both `foreign` and `iran/current`, four artifacts
each, and both restore/cleanup checks passed. Its source receipt is
`/root/secure-envs/trading-bot/production-backups/evidence/security-web-engine-20261024-08.json`,
SHA256 `873f1ca88b7ad1e3aa491017e1bde8bc5512316030405625906cbceec60bd5b6`.
The separate Native offhost receipt
`.local/security-web-engine-20261024-app-offhost-08.json` has SHA256
`47e444b61879f431c556bec26923a6618308fdd61ae0b553c444d6baf384ceb9`;
all four Web artifact hashes/lengths/inodes and actual fsync were verified.
Stage those public controls as `evidence/app-backup.json` and `app-offhost.json`.
The adapter retains the normal strict SSH/fsync/four-hash and 24-hour age gate.

The actual Final Finland destination control SHA256 is
`258870a2ff17e4fa0ad4309969d4a307edb6eb2c7a05e85962c773e700efcdb1`.
The public receiver is already staged and its capacity-only check passed; that
check created no ciphertext or backup receipt. A separate finite OP08 forced
SSH grant is still required. Its `web_engine_cold_transport/1` receipt must bind
the new operation/nonce, Web source IP, Final Finland destination, receiver and
guard SHA256, destination-control SHA256, known-hosts bytes, expiry, and only the
private key's nine metadata fields. The fixed Web identity directory is
`/root/.ssh/security-web-engine-cold-20261024-08`; no private key contents are
diagnostic inputs. The grant must cover the new deadline and remain within the
receiver's two-hour limit. Native's retained HOT3 cipher remains untouched.

Run `prepare` with the four public transport/destination/App receipt SHA256
arguments. It performs read-only runtime, provenance, package, App, managed04,
APT-closure and measured cold-scope/capacity checks before exclusively publishing
`plan.json`; it creates no fence or backup. `web_engine_current.yml` defaults to
the same read-only `check`. Its explicit `start` launches one durable native
supervisor; failure or an unknown launch is inspected, never retried. Trusted
Ansible's exact version/UTF-8 environment is checked in the new private work
directory before any fence publication or writer action.

The actual apply path holds the existing marker plus two fresh flocks and arms
an independent finite watchdog before quiescing. It stops native triggers first,
sets restart=no for the exact27, stops only the ten running non-datastores, and
keeps the six intended databases running for the strict fresh PRIMARY quiesced
CREATE and VERIFY APIs. CREATE performs one owned restore; VERIFY retains that
restore proof and rechecks fresh current source parity without a second restore.
The dump/control hashes, exact config-versus-OCI identity and nonce are bound.
Only after owned backup cleanup passes are the six stores and engines stopped.

Cold creation then includes that exact fresh dump and the approved cold roots.
Retained dump/plaintext/cipher FDs guard the normal tar create/compare, normal
authenticated encryption/decryption reconciliation, cipher-only send and fresh
remote reread/fsync verification. Capacity samples remain measurements rather
than reservations; attempted caps and the full 8-GiB reserves are not waived.
Only a verified cold archive permits the exact simulated offline package closure.
The normal local-file APT install disables source lists and refuses any simulated
additional install/removal; it uses no force, downgrade or repository addition.
The last 35 minutes are reserved for installation/restoration; databases start
and pass readiness before the other ten containers, exact policies return, and
only previously active native targets and timers restart, timers last. QA10 and
the migration remain stopped; the functional f79 image and open incidents remain.

The watchdog survives parent/client death, retains the inherited marker FD and
cancels only its exact stage scopes, nonce-owned PG backends and labelled restore
resources. Unknown daemon creation completion is sticky even after an absence
sample: it retains fences through the finite cleanup boundary and requires
manual recovery. A failed second fence publication or journal write before any
worker action removes only already-created exact FD/inode/owner-payload fences;
a foreign reservation and all history remain intact. Automatic pre-install
restoration is limited to acknowledged, unambiguous stopped phases with the exact
old packages, empty owned scopes and no pending native jobs. Package-write or
descendant uncertainty retains fences; no automatic package/store downgrade runs.

This source packet is not an actual engine transition, cold backup or fresh
maintenance-window receipt. Completion still leaves security/deployment acceptance
false, preserves known health incidents, and performs no reboot. The older boot
guard accepts only completed C07, so a separately reviewed OP08 completion binding
is still required before persistent boot-guard work or any later kernel reboot.

The normal v1.4 artifact is validated with its exact four-field contract,
including `format:postgres_custom`, before the stopped-engine cold phase.
Owned mutable journal/watch controls tolerate only the closed retained-FD/path
replacement race from legitimate atomic publication, for at most three reads
within 0.75 seconds. Every successful buffer is checked again against its exact
schema, operation, nonce and manifest; stable wrong ownership, invalid typed
phase/flags, or persistent instability still refuses and retains fences.
Immutable source/window/history controls receive no such retry or adoption.
