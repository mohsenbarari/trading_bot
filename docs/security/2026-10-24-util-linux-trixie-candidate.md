# Stable Trixie util-linux candidate: build evidence only

Continuation CI40087272190 actually passed full-family compilation, the six
old-caller ABI probes, the mountfd-disabled variant and compiled old/fixed
source negative controls. Its upstream report counted340 suite scripts and162
skip-status lines; this does not prove privileged/kernel coverage. Public
artifact12557508721 retains those actual receipts and the nine packages.
The job then failed disposable runtime installation with EACCES opening
`/packages/build-receipt.json`: the bound source directory was nonroot-owned
0700 and the installer correctly had no DAC-override capabilities. A closed
ten-file public installation capsule now uses0755/0644 below the private0700
parent, retaining the original private artifact directory. No capabilities,
scanner gate or production settings are relaxed. A corrected actual install
and raw scan remain required. Do not rebuild successful compilation merely
to obtain this missing installation evidence; reuse the bound public artifact
through a separately checked CI-only installation path.

Follow-up CI40108449213 again passed compilation, all six ABI probes and source
variants. It reached `dpkg --install` in the disposable candidate, then failed
processing `mount` and `util-linux`; installation and scanner acceptance remain
unproven. The old bounded diagnostic omitted dpkg's explanatory continuation
lines, so no package or capability fix is inferred from that failure. Public
artifact12565781476 retains the completed build evidence and nine packages.

Actual diagnostic CI40083981841 completed the coherent Debian family, six
old-caller ABI probes and normal distclean, then failed the isolated disabled
variant at `configure: error: libblkid is needed to build libmount`.
`--disable-all-programs` disables libraries as well; that variant now explicitly
enables both libblkid and its libmount consumer. The actual generated config
must prove both libraries enabled and mountfd undefined. This changes only the
separate feature-regression build, not the full Debian package configuration.
That source fix alone proved no build or security pass; later CI40087272190
provided the actual disabled-variant execution evidence described above.

Status: the complete candidate job has not passed. Compilation, six old-caller
ABI checks, the disabled variant and source regressions passed in40087272190
and40108449213; a successful actual installation and scan remain required.
No production package installation or activation is asserted.

First actual CI40082608496 failed during builder preparation. All seven
public input sizes/digests independently match. BuildKit does not create the
Docker runtime-only `/.dockerenv`; preparation now binds the sealed recipe
location instead. Compilation still requires the nonroot Docker runtime.
Actual second CI40083356266 completed the full package build and all six
old-caller ABI checks, then failed a later command before source-regression
completion. No final build receipt or acceptance exists. Command failures now
name their bounded public log, and diagnostics cover post-ABI configure/build
stages as well as the original build. Error lines and tails remain bounded.
The earlier
[source research](2026-10-24-util-linux-trixie-remediation-research.md) remains the
advisory source and explains the Debian/upstream discrepancy for CVE-2026-78409.

The recipe builds upstream **2.41.6**, with the three exact stable follow-ups
79c2881c, 485dbb67 and 233cf732. It keeps the original Trixie packaging from
2.41.5-0+deb13u1. All seven public input sizes and SHA256 values are recorded in
`deploy/maintenance/util_linux_build/build.py`; the old upstream archive used
for negative controls is pinned to its Debian source descriptor. Maintainer
signatures have not been authenticated; the receipt states that limitation.

The honest local version is `2.41.6-0+deb13u0+gold1`. `bsdutils` retains epoch
`1:` and `login` retains `1:4.16.0-2+really`. This version is **not** changed to
2.42.x to influence a scanner. Debian currently has no recognized Trixie fixed
version for these four rows. A successful candidate can therefore remain blocked
by the existing raw HIGH/CRITICAL gate.

## What the manual CI job actually does

`trixie-util-linux-build.yml` is manual-only, admits only published `main` or
the reviewed `security/remediation-20261023` branch, and has only `contents:read`
and `actions:read`.
This disposable candidate is not a release. Keeping its source iterations on
the security branch avoids additional full main acceptance runs; actual
App release gates remain unchanged. It copies exactly four published Git blobs into a private,
bounded context and records their hashes. No application source, runtime
configuration, session, credential or production volume enters the job.

The explicit manual input `reuse_verified_packages` defaults to false. When
true, the official artifact downloader retrieves only run40108449213,
artifact12565781476. Before any Docker command, admission requires build-receipt
SHA256 `3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde`,
the exact four current sealed recipe hashes and original source identity,
passing unaccepted receipt states, and the exact eighteen downloaded files.
All nine deb hashes, Debian build-record hashes, toolchain/config hashes,
ABI/source receipt contents and upstream suite summary are checked in bounded
buffers before the seventeen source artifacts are copied. The original receipt
remains unchanged. Missing or changed inputs fail without falling back to a build.

Reuse skips only builder preparation and compilation. Runtime preparation,
the unchanged real installer and both scans still execute. The separate
`build-provenance.json` identifies `reused_from_run`, `reused_from_artifact` and
`reused_receipt_sha256`, and states `compiled_in_this_run:false`; successful
candidate-image evidence carries the same provenance. This is a CI source
change, not evidence that the next installation or scan has passed.

The runtime base is the reviewed exact amd64 Python3.11.17/Trixie image. Debian
repositories are authenticated, timestamped official Trixie snapshots; disabling
archival Valid-Until checks does not disable index signatures. Input preparation
installs full Trixie build dependencies and downloads the exact old packages and
hash-pinned source/patch bytes. Failure to obtain any pin is a failure, not a
fallback to another distribution/version.

Compilation runs separately, without networking, as uid1001, with a read-only
container, no capabilities, two CPUs, 4GiB RAM, 512 PIDs, bounded output files and
a 3,600-second internal deadline. The only persistent writable mount is a new
public CI work directory. Running nonroot is essential: the unchanged Debian
rules pass `--nonroot` to upstream tests, which silently exit successfully when
run by root. The recipe refuses that skip message and requires an actual nonzero
upstream test count. Existing individual test skips remain visible in the full
exported build log and do not become privileged security coverage. The receipt
labels upstream's number as a reported script count and records skip status
lines; it does not relabel every reported script as an executed privileged test.

Only `noudeb` is enabled. No `stage1`, `noverity`, `nodoc` or `nocheck` feature/test
shortcuts are used. The entire normal Debian binary family is built, including
development packages and any generated debug packages. All family identities
and transformed versions are checked. Only the nine already installed runtime
packages are exported; unrelated optional binaries and build tools are excluded
from the candidate runtime installation.

The original eleven-entry quilt series must match exactly. Only its two
already-integrated upstream loopdev/source-canonicalization entries leave the
active series; their original files remain present. The other nine Debian
behavior/test patches remain active, and the three pinned upstream follow-ups
are appended. Quilt applies all active patches with zero fuzz. A conflict fails
the build; no automatic patch repair or behavior patch removal is permitted.
Before source freeze, all twelve active patches were also checked against
unique exact matching source contexts in bounded RAM: thirteen source files,
no context drops, no local compiler or source-tree extraction. This is source
applicability evidence, not a successful quilt/build/test invocation.

## Evidence required from a successful build

The six ABI probes cover blkid, fdisk, libmount, smartcols, uuid and lastlog2.
Each caller is compiled against actual old Debian headers/libraries. That exact
unchanged caller binary runs first with the old libraries and then with the
candidate libraries. `dladdr/dlsym` verifies the actual loaded directory, and
caller hashes must remain unchanged. Probes use only synthetic files: a small
ext4 image, blank disk, fstab, in-memory formatting and a new lastlog database.
No host device, live mount table or account database is opened.

Actual ELF checks require the same SONAME, retention of old versioned exported
symbols and no newer-than-Trixie GLIBC requirement. Debian's normal package
symbol checks run during the full build. Extracted PAM bytes and the selected
su/newgrp/login/mount/umount permission bits must stay unchanged. No source-map
comparison is presented as binary ABI proof.

The normal Debian build must enable mountfd. A separate out-of-tree libmount
build disables mountfd and is actually loaded with immediate symbol resolution;
missing helper symbols fail. Compiled source-function regressions extract the
real `mnt_context_do_mount`/failure predicate and nsenter cgroup functions. Closed
context/syscall fixtures exercise failed-helper/syscall post-hook suppression,
successful/no-op commit behavior, descriptor initialization, closing both
privileged descriptors and retaining stdin. The nsenter fixture also compiles
the exact cgroup join block from old/candidate `main`, where the stable patch
closes the second descriptor; its order before target credential changes is
checked. No fixed close is invented in the fixture. The old upstream functions must
fail the same contracts. Source text searches are not used as passing tests.

The primary build's actual config header is preserved and hashed. Only after
packages and ABI evidence are captured does normal `make distclean` run in that
primary build directory. The disabled out-of-tree variant then starts from the
normally cleaned source; Autoconf's configured-source guard is not bypassed.

These source-function tests **do not** reproduce credential boundaries or real
namespace/cgroup/mount races. The receipt explicitly records the real failed
helper ownership check, cgroup namespace/credential-drop negative controls and
restricted symlink/procfs mount races as **NOT_TESTED**. The job does not add
SYS_ADMIN or privileged mode to disguise this gap. Those real regressions and
the 78409 vendor/binary reconciliation remain mandatory before acceptance.

A second disposable, network-isolated CI container simulates the exact nine
package upgrades, rejects removals/unrelated changes, then installs those local
files. Only file-owner/setid capabilities needed by normal dpkg metadata are
added there; no host socket, device, network or application data is mounted.
Policy-rc.d refuses service starts. The exact installed versions and clean dpkg
audit are checked before committing that container to an immutable scan image.

Trivy produces the actual complete raw vulnerability JSON and SBOM from that
immutable image, retaining every severity and unfixed row. The vulnerability
report must have the pinned scanner's schema, actual Results and the exact
candidate image ID in Metadata. Nine candidate
versions must appear in the SBOM. This disposable base is not an App release;
final App regression/scan gates are still mandatory. Raw scanner exit0 here
means evidence collected, never that vulnerabilities are waived or fixed.

## Review, artifacts and acceptance

The public export contains exactly nine debs, build/ABI/source receipts, Debian
`.changes`/`.buildinfo`, the exact public toolchain inventory, the bounded full
upstream build log, primary config header, recipe identity and separate build
provenance. The receipt also hashes each old Debian
baseline package. Size/hash/regular-file/single-link/no-follow/stability guards
apply. Raw reports have separate finite limits: 32MiB vulnerability JSON, 8MiB
SBOM and 64KiB image identity, 48MiB total. Package export is independent of report
admission, so a scanner/report failure preserves completed package evidence.
All artifact groups expire after seven days. Failure diagnostics read only
the last64KiB of sixteen fixed public build logs, at most32KiB per encoded
record/512KiB total, including four matching failure lines of at most300
characters each and a4096-byte tail. Thus the complete3343-byte install log
from40108449213 would retain its explanatory continuations. A separate public
installation diagnostic artifact preserves the fixed install log's last64KiB
with total-size/truncation metadata, including on installation failure; it
contains no application or private runtime input.

Every receipt remains `candidate_built_not_accepted`, `security_accepted:false`,
`production_installed:false`. A green build does not close any advisory. Final
review still needs real privileged negative controls, exact App image behavior
and dependency checks, vendor reconciliation for 78409, and the unchanged
unfiltered HIGH/CRITICAL gate. No install, deployment, host-library replacement,
essential-package removal, scanner ignore or production acceptance is included.
