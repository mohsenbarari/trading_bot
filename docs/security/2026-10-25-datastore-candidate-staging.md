# Fixed datastore candidate staging and measurement

This packet is source only. Neither image has been downloaded, staged, loaded or
activated by this implementation. Root reviews and executes each separate phase.
No datastore activation, service/container mutation, retag, pull, image removal,
backup, private runtime configuration read or migration is provided here.

The scope is the two actual public vendor candidates:

| Profile | Actual CI run | Config identity | Ordered layers | Raw findings |
|---|---|---|---:|---|
| PostgreSQL15.19 |40142076769|49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1|12|1UNKNOWN; zeroCRITICAL/HIGH/MEDIUM/LOW|
| Redis7.4.11 |40142214416|98c4c0abd245b4b65178494e4727c69c63a28ed604cfb7dbf74205203a987015|8|zero findings|

Both were built from `ecfb667ee1583168f2777a6fa5f54766a63ef787`. Existing signed
vendor/APK, ordinary TLS/data/restart, complete image Config, server/support byte
and RootFS-prefix evidence remains the source of their build claims. PostgreSQL's
unchanged x/sys Windows-only UNKNOWN and its separately measured Linux code
absence stay visible. This packet grants no finding waiver or global eligibility.

## Immutable public inputs

`datastore_candidate_contract.PROFILES` fixes both complete12/8-layer lists,
the actual public evidence ZIPs and producer receipts, and these image artifacts:

| Profile | Image artifact | ZIP bytes | ZIP SHA256 |
|---|---:|---:|---|
|postgres|12578982553|114543515|7d22f13f6cb225efe325c297e777536973f7de1082da12629ca2df44a1f601da|
|redis|12578882888|18444160|2faa867c2f3863740e823f0e20f8dc867d9ce04f33367083580fdbfd4392b97d|

PostgreSQL's gzip is115441880 bytes, SHA256
`b3b3cdf220b9bb373308ce5199b5eb13335cf39d5465cab96ce1bc69182186f3`,
expanding to303898112 TAR bytes. Redis's gzip is18534734 bytes, SHA256
`59a62cfd9d2a00b6bde7eaa484a0c2187922456bd72351aab2f599e3a1033d9a`,
expanding to46384128 TAR bytes. The raw TAR SHA is measured during admission;
no future measured value is invented in this source packet.

The real `alpine_datastore_security_export/1` receipts are5302/3485 bytes,
SHA256 `6e68ee177520cd4f1e2a7f1bab2c91ed8f9017d44f3a5e4d477cfc056f208e45`
and `c5e1e46600771119ad45904ffee872b3df998c1716d53db6237e721a18cb2a64`.
Their sparse severity counters are respectively `{"UNKNOWN":1}` and `{}`.
Only omitted zeroes are normalized across the closed five severities. Boolean,
negative, unknown or changed nonzero values refuse. Original bytes stay intact.

The source validator reads the pinned evidence ZIP in RAM and reconciles all
producer-bound report hashes, image/config identity, layers and raw findings.
Complete image Config values are comparison operands in RAM. No Config/Env
projection, field-value hash or diagnostic copy is emitted. The normal public
image/evidence archive bytes themselves retain their authorized transport role.

## Reviewed primitive reuse and archive format

The packet loads same-buffer SHA-sealed versions of:

- `runtime_image_archive_transport.py` at20c4b90f: bounded borrowed reader,
  physical TAR inventory, raw/gzip layer hashing, digest and bounded zero tail.
- `admit_security_component_image_reuse.py` at9adddae3: only `moby_go_json`.
- The preserved actual App Native loader `load_native8da.py` at2243eb34:
  only bounded byte I/O, owned-client stop, child limits and public Docker-unit
  inspection. Its old execute/profile/dependencies/window APIs are never called.

No sealed module global is changed. In particular, the old App/Market legacy
serializers deliberately support narrower Config fields and are not repurposed.
The new closed typed adapter implements the official
[Moby v28.5.1 Config declaration order](https://github.com/moby/moby/blob/v28.5.1/api/types/container/config.go),
[saveImage compatibility chain](https://github.com/moby/moby/blob/v28.5.1/image/tarexport/save.go)
and [CreateID calculation](https://github.com/moby/moby/blob/v28.5.1/image/v1/imagev1.go).
It preserves the actual nonempty image Comment, ExposedPorts, Volumes,
Entrypoint, nil OnBuild and optional PostgreSQL StopSignal. Absent optional API
defaults are explicitly typed; unknown Config keys refuse.

Admission expects one normal OCI runtime manifest plus Docker `manifest.json`.
Its Config digest must be49a8/98c4, every ordered raw DiffID must match the fixed
profile, and all physical members must belong to that graph or the exact complete
reconstructed Moby v28 compatibility chain. Only four ordinary raw/gzip layer
media types are accepted. No filesystem extraction or code execution occurs.
Selected manifest and outer-index identities are recorded as source facts;
admission does not pretend either was observed in a daemon. Unknown layouts stop
unaccepted and need read-only review, never a blind LOAD retry.

## Root-supplied finite controls

Root must first finish App5, genuinely park the approved legacy set and bind a
fresh public baseline. This code creates no future baseline or authority file.
`datastore_candidate_public_baseline/1` contains exactly site/host/port,
machine ID/kernel, Docker store, authenticated public mount projection,
data-root identities, complete container public policy, complete image/tag map,
and SHA bindings to actual App completion and legacy parking receipts. It must
have15/9 total/running on Native and27/11 on Web. Counts alone are insufficient:
the full42 actual IDs, images, restart/mount/network/port/privilege/PID policy
are compared before and after. The two existing Market health incidents remain
OPEN; no other running unhealthy, paused or restarting process is accepted.

Each Root-reviewed `datastore_candidate_operation/1` is a new nonce and one of
`stage-web`, `load-web`, `load-native`, with a fresh UTC issued/deadline pair at
most900 seconds apart. It binds all six source buffers, two per-host public
baseline files and exact fixed evidence bytes. The latter phases also bind the
actual Web admission receipt/source nonce; Native loading additionally binds the
actual Web loaded-site receipt. These public receipt bindings contain distinct
`paths.bot`/`paths.web` and one exact SHA. No Native ZIP/TAR path is required.
All operation acceptance/activation booleans must remain false.

Stage the reviewed source buffers as separate root-owned single-link0600 files
in private canonical tools directories on both hosts. Root must also stage the
public evidence ZIPs, public baselines and operation controls with the same
protection. Web tools are fixed under
`/srv/trading-bot/security-datastore-candidate-tools-20261025-01`; its operation
file is `controls/<nonce>.json`. Image work roots are respectively
`/srv/trading-bot/production-data/security-datastore-candidate-20261025-01`
and `/srv/trading-bot/security-datastore-candidate-20261025-01`, both0700.
Every phase uses a fresh `<profile>-<nonce>` child. Existing attempts refuse.
The Native launcher likewise accepts only `HERE/controls/<nonce>.json`; the
filename must match the control nonce. It cannot be pointed at an Env, key or
session filename. Root stages the same small public tools/evidence paths on both
hosts, while the explicit per-site receipt/baseline bindings may differ.

Use the published `datastore_candidate_stage.BOOTSTRAP` literal to execute the
launcher/remote entrypoint from the same bounded verified retained source buffer,
preserving `__file__` and argv. An earlier `stat` followed by executing a pathname
is not this promise. The Native entrypoint is `datastore_candidate_launch.py`
with only `--control`, `--control-sha256` and the exact confirmation
`DATASTORE-CANDIDATE-IMAGES-ONLY-20261025-01`. No alternate owner, image, host,
mutable tag or arbitrary command can be supplied through that CLI.

Run the Native launcher in a fresh transient scope named
`security-datastore-stage-<nonce>-bot.scope`, CPUQuota100%, MemoryMax2G,
MemorySwapMax0 and TasksMax64. The authenticated Web hop creates its corresponding
`-web.scope` with the same limits. Workers independently verify cgroups and enforce
the common absolute deadline. Both scopes additionally require RuntimeMaxSec900s,
TimeoutStopSec5s, KillModecontrol-group and SendSIGKILLyes; public systemd scope
properties are checked before work. [Scope runtime limits](https://www.freedesktop.org/software/systemd/man/latest/systemd.scope.html#RuntimeMaxSec=)
and the [v255 transient scope properties](https://github.com/systemd/systemd/blob/v255/src/core/dbus-scope.c)
are supported vendor interfaces. The900-second forward bound has a5-second owned
scope termination allowance; neither proves external daemon cancellation.
No virtual address-space limit is inherited by Go
CLIs. Scope limits cover owned workers/clients, not the external dockerd daemon.

The launcher holds unchanged M, the existing empty SOURCE inode and a fresh
exclusive RESERVATION in that order. It never reads/writes/hashes/unlinks SOURCE
or M. Their full nine-field identities are supplied by the reviewed operation.
Canonical reservation JSON carries the new nonce, PID and control SHA. Known
completion removes/fsyncs only the exact owned reservation while both permanent
flocks remain held. Unknown clients retain reservation/history. No old window,
marker, operation token or historical source mutex is adopted as new authority.

## Three serial phases

1. `stage-web`: the normal Native GitHub CLI streams the fixed artifact directly
   through strict SSH to the Web receiver. Native creates no ZIP/TAR file. Web
   checks exact bytes/SHA before ZIP parsing, then retained ZIP/gzip/TAR descriptors,
   prospective writes and the complete closed graph before fsyncing admission.
   A partial directory never becomes an admitted source automatically.
2. `load-web`: Root supplies that actual admission SHA in a new operation. A
   durable outcome-unknown request is fsynced before one `docker image load` from
   the same retained verified TAR FD. No tag/pull/container API is used. Actual
   daemon ID, full RootFS and full image Config are measured after client0/reap.
3. `load-native`: Root supplies the actual Web loaded-site leaf and admission in
   another new operation. Web rechecks the same source FD, image identity and
   complete public profile/map, then emits a bounded header, exact TAR stream and
   retained-FD/hash/public-parity footer. Native hashes while passing bytes
   directly to one Docker stdin. Both clients must return integer0 and be reaped;
   header/footer/EOF/hash checks and Native postchecks must all pass.

The actual retained TAR descriptor belongs to the enclosing operation, including
its final checkpoint. A source-only integration review caught an earlier
lifetime mismatch before any actual datastore LOAD: a phase-local context could
close the TAR and leave its checker registered, causing a false EBADF failure
after an otherwise completed Web LOAD. The operation now retains that original
descriptor through final verification; standalone phase callers detach only
their own registration immediately before the held-input context performs its
final identity check and closes. No EBADF, inode/byte drift or unknown outcome
is globally suppressed. Owned-FD tests prove post-phase checks still read the
same open inode and that path replacement continues to refuse acceptance.

Classic Native Config-ID measurement never mentions `Descriptor` in its inspect
projection. Manifest/index identities require the guarded optional Descriptor
query and exact source-graph digest/media/size. Missing or unfamiliar identities
remain unaccepted. The standard `runtime_image_archive_binding/1` leaf preserves
daemon ID versus source Config identity; no equality fiction or mutable alias is
introduced. Only the actual two-host result can publish `datastore_candidate_loaded/1`.

Before and throughout owned writes, each data filesystem preserves8GiB plus64MiB
margin, and Native OS root preserves2GiB plus margin/control bytes. Web admission
budgets simultaneous ZIP+gzip+raw TAR. LOAD admission conservatively reserves
twice TAR plus twice measured ordered raw layer bytes; the original public-unit
check supports the Native default store/tmp layout while honestly retaining
`runtime_override_absence_proven:false`. These are prospective allocation checks
and measured free-space floors, not a filesystem quota or dockerd memory limit.

Native LOAD also keeps that complete profile-bound import fallback budget on
the OS filesystem: root must have at least2GiB+64MiB+2MiB of control margin
**plus** twice the actual admitted TAR bytes plus twice its measured expanded
layer bytes. A machine just above the old2GiB threshold therefore refuses before
the SSH source or Docker LOAD request. The budget remains active through stream,
footer/EOF, bounded client waits and final publication; it is not silently spent
down as stream bytes arrive. The public `/` and `/tmp` device/inode/mode/owner
observations must stay unchanged, both directories must share the root device,
and `/tmp` must remain root-owned1777. Request and completion record the exact
fallback budget and metadata while keeping unobservable runtime Env overrides
explicitly unproven. Each client wait rechecks capacity at most every250ms;
these repeated observations still are not an allocator quota or a guarantee
about external daemon cancellation.

Timeout/client death/one absent poll never proves daemon cancellation. A failed
or missing receipt never permits replay. Root may commission separate bounded
read-only reconciliation; this code never performs a second LOAD, retag or image
cleanup to turn ambiguity into success. Failure to publish the final controller
receipt after conclusive reservation cleanup returns
`known_outcome_final_receipt_pending`, preserving the verified phase outcome.
It cannot rerun, roll back or mutate after those fences were released.

The focused tests use real private temporary files, ordinary TAR/gzip/ZIP members,
pipes and flocks plus named inert client fixtures. Import and per-case guards deny
ambient host/network/native/signal/resource calls without leaking into other
test modules. Real pinned5302/3485-byte producer exports and independent actual
12/8-layer public identities are fixtures. Neither real image archive admission
nor LOAD has yet been proved by these source tests; both remain Root execution
and evidence gates. Datastore backup/restore, serial rollout, runtime Config
parity, rollback and final retirement/deletion are separate required work.
Root-pressure tests cover refusal before any LOAD/stream RPC and during client
completion, plus real owned temporary-directory inode/mode changes and an
explicitly scoped device-change fixture. They use actual fixture UID/GID rather
than pretending the test process is root. The literal refusal-code catalogue is
checked across all three new source files.
Additional dedicated suites exercise evidence/report/SBOM identity and semantic
failures, actual owned ZIP/gzip/TAR pipelines, stream framing/EOF/hash/client
failure, Root-owned descriptor lifecycle and coordinator/CLI orchestration.
Coverage is measured with the real root UID and ambient operations denied;
test observations never establish an actual CI archive admission or daemon LOAD.
