# Ordinary ACL and tar vendor candidates for Trixie

This is a source-only CI packet. No source build, ABI caller, ACL operation,
archive roundtrip, package installation or scanner pass is claimed by its
hermetic tests. Security acceptance and production installation remain false.
No custom vulnerability reproducer, source repair, malformed archive or attack
fixture is part of this packet. Actual App payload tests and the unfiltered
HIGH/CRITICAL release gate remain separate requirements.

Debian identifies ACL 2.4.0-1 as the fixed source release for CVE-2026-54369.
Debian bug1141146 identifies tar1.35+dfsg-5 as fixing the new-header namespace
collision. Its vendor patch renames static internal wrappers. Ordinary ABI or
valid-file tests do not prove that old pathname callers gained stronger security
semantics. Every receipt retains `legacy_file_api_security_assessed:false`.

## Public source provenance

All binary package indexes and dependencies remain signed Trixie snapshots at
20261024T000000Z. The additional Forky stanza has `Types: deb-src` only, at
20260820T000000Z: that source index contains both exact requested releases.
The October24 Forky index contains tar revision6 instead. There is no fallback
to a different source revision, distribution, binary package or libc family.
Normal APT signature/index checks authenticate source acquisition; each source
file then has its exact size/SHA256 checked before offline extraction. Upstream
signature bytes are retained, but individual maintainer signatures are not
claimed to have been authenticated by this recipe.

| Source file | Bytes | SHA256 |
|---|---:|---|
| acl_2.4.0-1.dsc |2624|b987ebbeb3d498794bad29cd65301b77384978493d8765c94dba8bad8fa325b6|
| acl_2.4.0.orig.tar.xz |384828|e661131456d2708a01c614a0f400e11d7d1bfaeb6f3e74b75bb980b72f0161a3|
| acl_2.4.0.orig.tar.xz.asc |833|5f4f0b9b78821764fe6b88e32aef6ec519628522c211c36706bcb78d6f3f036b|
| acl_2.4.0-1.debian.tar.xz |47932|65931c2fb3e821bda67f8d8d72d77e99ac61502748dcdf38b6805fe89339085e|
| tar_1.35+dfsg-5.dsc |2428|e026e7a19c295212fcc3250e3136b134ae09661c559bf707b8ebfdd8f5093c0a|
| tar_1.35+dfsg.orig.tar.xz |2111608|9ae57e981c1e73c0eebc2b26c9b0c4497fe310ef1d516ea430efb5470b71f7a8|
| tar_1.35+dfsg-5.debian.tar.xz |22704|51fecfaa10cbadb1ab19be88adfd34a6d876619c50aaaafd69011ddb2e283b70|

Honest local rebuild versions are `2.4.0-1~trixie+gold1` and
`1.35+dfsg-5~trixie+gold1`. Scanner recognition is not presumed.

## Coherent, isolated compiler stages

The manual workflow seals exactly Dockerfile/build.py/verify.py from the
published commit. It has no deployment credentials or production mounts.
Preparation uses the normal vendor build dependencies, fixed old Trixie packages
and the seven source files. BuildKit preparation checks its sealed recipe
location; it does not require the runtime-only Docker marker.

First, an offline read-only container builds the full ordinary ACL family:
acl, libacl1 and libacl1-dev, plus any normal debug packages. Then the runner
admits a closed public capsule containing only checked candidate libacl1,
libacl1-dev and their receipt. A separate writable, network-none CI container
installs those two local packages with ordinary APT and commits an immutable
compiler layer. This is explicitly compiler-only evidence, never production
installation or a runtime release.

The second source build has its own fresh work directory. It requires the
compiler launch to select `/usr/local/bin/python3 -I -B /recipe/build.py`
explicitly, replacing the entrypoint retained by the installer-layer commit.
Both compiler phases use that same explicit helper invocation. It requires the
candidate ACL runtime/development versions and compares the compiler-visible
public `sys/acl.h` and library hashes against the checked candidate packages.
It then builds the full tar/tar-scripts family using normal system build paths.
Thus the actual tar compilation sees ACL2.4 headers and library. Each phase
records its actual installed compiler package inventory, recipe hashes, vendor
rules/control hashes, build records and ABI results. Tar binds the original ACL
build receipt SHA. Stock vendor rules/control stay unchanged; only a normal
local changelog entry changes the package revision.

Both source builds run nonroot with networking disabled, no capabilities and a
read-only root; only their new public work and temporary directories are
writable. Source/tool children use `Popen(umask=0o022)` so normal vendor install
rules preserve readable package directory modes. The Python controller, private
artifact directories and receipt writes remain under umask077. Default tests
remain enabled; `noudeb` excludes installer udebs. No `nocheck`, ignored test
failure or source patch is introduced. Privileged/interactive coverage stays
`NOT_TESTED` rather than being inferred from successful default tests.

The first actual [CI attempt 40114616816](https://github.com/mohsenbarari/trading_bot/actions/runs/40114616816),
job120933227323, authenticated the source and dependency inputs,
then failed the ordinary ACL default tests: their temporary
`libtestlookup.so` could not map a segment through `LD_PRELOAD`. Docker's
[tmpfs implementation](https://github.com/moby/moby/blob/v28.5.1/daemon/oci_linux.go#L491)
prepends `noexec`; merely omitting that option from the caller did not make
`/tmp` executable. Both compiler phases now request
`/tmp:rw,exec,nosuid,nodev,size=256m,uid=1001,gid=1001`, using Docker's documented
[`exec` tmpfs option](https://docs.docker.com/engine/storage/tmpfs/#options-for---tmpfs).
The executable temporary directory permits normal vendor-built test libraries
and programs. Compiler identity, capability removal, network isolation,
read-only root and `nosuid,nodev` remain required. Installer temporary mounts
retain their existing options. No vendor test is skipped; a fresh actual build
must establish its result before any family-build or runtime evidence is claimed.

## Ordinary compatibility and final installation

One caller is compiled against the old Trixie ACL headers and actual old ELF
library. That exact unchanged binary runs against old and candidate libraries,
with loaded-path proof, retained libacl.so.1 SONAME, all old versioned exports
and no newer-than-Trixie GLIBC requirement. Only ordinary owned regular files
and valid ACL text are used. Parse/validate/set/get/fd-get/compare operations
must agree. Check mode never writes an ACL.

Valid PAX archives preserve ordinary ASCII/Unicode file bytes and an extended
ACL. ACL-phase profiles exercise old tar with old/new ACL libraries; tar-phase
profiles exercise old/new tar with the new library. Extraction uses
no-same-owner and same-permissions on owned temporary files. Byte comparison and
the non-writing ACL check must pass. No hostile path, race, malformed archive,
privileged boundary or attack outcome is tested or asserted.

Only libacl1 and tar are exported for final runtime installation. The full
source-family identities remain in receipts; development tools and tar-scripts
are excluded from the runtime pair. A separate capsule has mode0755 with only
verified public files0644, while original evidence stays private. Both CI
installer phases use absolute local package paths and normal APT ordering.
Simulation admits exactly their two packages and no removals. Real installation
intentionally omits `--no-download`, allowing APT's local-file acquisition; the
container's network-none boundary still prohibits network retrieval. Full
before/after package inventories, exact versions and clean dpkg audit reject
extra packages, other upgrades or libc changes. No Essential removal, package
force flag, SYS_ADMIN or DAC-override capability is used.

Final installation produces an immutable disposable runtime scan image. Actual
raw Trivy JSON and SBOM must bind that image ID and both installed versions.
Every severity and unfixed row is retained. Scanner exit0 collects evidence; it
does not waive the unchanged final App gate. Public package proof is retained
for seven days even if final installation or scans fail. Owned CI container
cleanup is bounded; an ambiguous daemon request remains a failed job, not an
accepted candidate. No active-host cleanup or production action is authorized.

Primary sources:

- [Debian ACL security tracker](https://security-tracker.debian.org/tracker/CVE-2026-54369)
- [Debian tar namespace compatibility bug](https://bugs.debian.org/1141146)
- [August Forky source index](https://snapshot.debian.org/archive/debian/20260820T000000Z/dists/forky/main/source/Sources.xz)
- [Trixie dependency index](https://snapshot.debian.org/archive/debian/20261024T000000Z/dists/trixie/main/binary-amd64/Packages.xz)
