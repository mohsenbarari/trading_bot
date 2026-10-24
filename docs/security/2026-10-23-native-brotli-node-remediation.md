# Native Brotli / Node security follow-up — proposal, not remediation

Scope: CVE-2026-111497 on the native Ubuntu hosts. This is not the unrelated
Scrapy CVE-2026-6176. No package installation, service restart, global Node
replacement, IDE update or production action is implemented by this document.

## Verified primary-source facts, 2026-10-23

- [Google's security release](https://github.com/google/brotli/releases/tag/v1.1.1)
  fixes the decoder flaw in Brotli 1.1.1. The other maintained branches have
  1.0.10 and 1.2.1 fixes, according to the
  [Google CNA record](https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2026/111xxx/CVE-2026-111497.json).
- The exact 1.1 branch backport is
  [`da9806362874b9bd4b552a3af360b5d6347beb12`](https://github.com/google/brotli/commit/da9806362874b9bd4b552a3af360b5d6347beb12):
  `c/dec/decode.c` and `c/include/brotli/decode.h`. It propagates invalid
  block-switch errors before continuing decoding. Do not substitute the main
  branch patch without checking its applicability to the native 1.1 source.
- Upstream [1.1.0](https://raw.githubusercontent.com/google/brotli/v1.1.0/c/common/version.h)
  and [1.1.1](https://raw.githubusercontent.com/google/brotli/v1.1.1/c/common/version.h)
  keep ABI current=2 and age=1, with revision 0 becoming 1. The shared-library
  SONAME therefore stays 1 under the
  [upstream build rules](https://raw.githubusercontent.com/google/brotli/v1.1.1/CMakeLists.txt).
  This supports a narrow backport; it does not replace actual ELF/consumer tests.
- [Ubuntu's current tracker](https://ubuntu.com/security/CVE-2026-111497)
  still says Needs evaluation for both `brotli` and `nodejs` in Noble and
  Resolute. A currently unchanged apt candidate is not evidence of a fix.
- [Node's release advisory](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
  fixes supported lines in 22.23.4, 24.22.1 and 26.12.1. EOL lines are not an
  acceptable new project runtime. [24.22.1](https://nodejs.org/en/blog/release/v24.22.1/)
  cherry-picks the Brotli fix: its component version can still report 1.2.0.
  Do not label that verified official Node build vulnerable solely by that field.

The parent recorded native `libbrotli1` / `python3-brotli` 1.1.0-2build2,
apt Node 18.19.1+dfsg-6ubuntu5 and a separate global Node 20.19.5. These are
provided inventory facts, not a new consumer or process audit. The existing
scoped, verified Node 24.22.1 secures its explicit build/test paths only.

## Smallest safe persistent approach

1. Prefer an authenticated Ubuntu security update if a fixed candidate appears
   before execution. Require its changelog/CVE evidence and exact version;
   do not infer status from a package's upstream version alone.
2. If the vendor fix is still absent, prepare a **local Ubuntu-source backport**
   in an isolated matching-Ubuntu builder. Use the authenticated exact installed
   source package, retain its distro patches/build flags, and apply only the
   reviewed 1.1-branch fix plus a local changelog version such as
   `1.1.0-2build2+security20261023.1`. Do not overwrite `.so` files manually,
   install untracked copies under `/usr/local/lib`, change linker search paths,
   or introduce a cross-distribution repository.
3. Determine whether the distro Python extension links system `libbrotlidec`
   or embeds its own decoder **before choosing the binary package set**.
   Upstream Python builds embed the C sources; distro packaging may differ.
   Rebuild the installed Python binding too if it embeds vulnerable code.
   Replacing only the system shared library must not be claimed to fix an
   embedded decoder. Match any installed CLI/dev package version constraints.
4. After proving compatibility, install only the exact reviewed local `.deb`
   closure through apt, with no unrelated removals/upgrades. Keep package
   ownership and a recorded local version so future vendor updates are visible.
   Prove version ordering against the eventual fixed Ubuntu revision: the local
   build must not hide a vendor security update or require an indefinite hold.
   No generic maintenance framework or automatic downgrade is needed.
5. For project Node consumers, reuse the existing verified 24.22.1 bootstrap
   and set an explicit executable/PATH in only the identified project launcher.
   Verify the actual child runtime, not just an interactive shell or `.nvmrc`.
   Native addon compatibility and the full affected tool's tests remain gates.
   Do not repoint a global symlink or replace an IDE's embedded Node binary.

## Required postboot admission facts

Read only bounded public metadata; do not inspect process environments, full
command lines, keys, sessions, application records or user data.

- Native host/distro/architecture and exact installed package versions/owners.
- Exact symlink-resolved ELF paths and dynamic dependencies for the apt Node,
  global/scoped Node, Python Brotli extension and known project consumers.
- Actual running executable/library mappings, reporting path/owner/service
  only; distinguish project services, CLI launchers and unrelated IDE/Electron.
- Exact affected systemd units/launchers, prior active/enabled intent, and
  whether any mapped decoder is embedded rather than dynamically linked.
  Include cron and long-running unmanaged consumers; a units-only inventory is
  not complete evidence that every active consumer will receive the fix.
- Original signed-source metadata, patch digest, builder identity, output
  package digests and simulation proving the exact transaction closure.

No consumer inventory means **no admission to a system-library apply**. An
unused installed package is not silently removed. Updating only mapped
project launchers also does not close vulnerable unrelated installed software.

## Required proof and negative tests before apply

- Compare old/new SONAME, exported ABI, architecture and required GLIBC levels;
  retain all prior exports. Reject unexpected patch files or build dependencies.
- Run upstream tests in the isolated builder, then synthetic C/Python/Node
  round-trip and streaming tests, with bounded benign invalid-input rejection.
  These smoke tests alone do not prove this CVE: bind the compiled artifact to
  the exact security source patch. Never send a crash/exploit vector to a live
  application or use production data as a test fixture.
- Exercise actual identified consumers against candidate libraries/toolchains
  before changing host paths. Check error behavior as well as successful input.
- A narrow future apply must refuse a wrong host/distro, stale source/package
  baseline, changed/symlink/unsafe-owner payload, unexpected apt transaction,
  incompatible ABI, missing rollback payload, unknown active consumer, absent
  backup/restore receipt, concurrent maintenance or expired window **before
  any write or stop**. Cover each refusal with hermetic synthetic tests.
- Retain and hash the original exact `.deb` payloads, metadata-only consumer
  inventory and needed service configuration; use existing private backup,
  off-host and restore-proof mechanisms. Do not put private contents in public
  evidence. Retention follows the project policy, not indefinite backup growth.
- After installation, restart only the captured affected consumers in reviewed
  order, prove no old decoder mapping remains in that scope, verify consumer
  health/actual Node executable, then restore original service intent.

## Explicit unresolved boundary

There is no authenticated fixed Ubuntu package or tested local backport in this
proposal. A package revision or scoped Node path is not host-wide closure.
IDE/Electron or another unrelated application's bundled decoder needs its own
vendor-supported update and coordinated restart; it cannot safely be repaired
by substituting the project's Node or purging its package dependencies.
Until these proofs exist, record the native library / old runtime finding as
**open**, not waived, mitigated-as-fixed, or resolved by the kernel reboot.

## Concrete next step: prepare the Noble backport, without installing it

The subsequent read-only native inventory found no process mapping system
`libbrotli` / `libnode`. Four old global Node 20 processes belonged to the
OpenAI developer plugin session, not the project. IDE runtimes are separate;
neither finding authorizes replacing their binaries. The installed Brotli
package remains an open latent vulnerability despite no observed live mapping.
This snapshot is not a guarantee about future, cron or short-lived consumers.

Perform the commands below **only inside a disposable Ubuntu Noble amd64
builder**, with a bounded public-artifact workspace and the builder's own apt
configuration. They are a build recipe, not commands to run on the native host.
The matching arm64 recipe needs separately built/verified arm64 outputs.

### 1. Authenticate and freeze the exact source inputs

Enable `deb-src` only in that disposable builder, update its authenticated Ubuntu
indices, then obtain the original package and declared build dependencies:

```sh
apt-get update
apt-get source --download-only brotli=1.1.0-2build2
apt-get build-dep --yes --no-install-recommends brotli=1.1.0-2build2
```

Do not use unauthenticated/trusted=yes/insecure-source options. Retain the
Ubuntu InRelease/Sources authentication evidence, plus the three original files.
The [official Ubuntu source record](https://launchpad.net/ubuntu/+source/brotli/1.1.0-2build2)
publishes these independent SHA256 values:

| File | SHA256 |
| --- | --- |
| `brotli_1.1.0.orig.tar.gz` | `10973f4b4199eafa1d5735ef661ddb2ec2f97319ee9fd1824d4aabe08cff5265` |
| `brotli_1.1.0-2build2.debian.tar.xz` | `96603222d7b6edb3253a846852972b6bde5b86814907d5540249e6950980a943` |
| `brotli_1.1.0-2build2.dsc` | `c58421959677a80d942924685007e8d5b5d1160fd7f61a1f0a9062bf08835856` |

Check all three hashes before extraction. Archive authentication and independent
recorded hashes bind the source; do not claim a maintainer `.dsc` signature was
verified unless its signing key and signature were actually verified too.

Retrieve the official Google backport as a Git object in a separate empty public
checkout. Prevent inherited Git configuration or external diff helpers:

```sh
env -i PATH=/usr/bin:/bin LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 git init upstream-brotli
env -i PATH=/usr/bin:/bin LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 git -C upstream-brotli fetch --depth=2 https://github.com/google/brotli.git da9806362874b9bd4b552a3af360b5d6347beb12
env -i PATH=/usr/bin:/bin LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C upstream-brotli fsck --strict
env -i PATH=/usr/bin:/bin LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C upstream-brotli diff-tree --no-commit-id --name-only -r da9806362874b9bd4b552a3af360b5d6347beb12
env -i PATH=/usr/bin:/bin LC_ALL=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C upstream-brotli format-patch --no-ext-diff --no-stat --no-signature --no-numbered -1 --output-directory ../reviewed-patch da9806362874b9bd4b552a3af360b5d6347beb12
```

Require the exact commit ID and **exactly** `c/dec/decode.c` plus
`c/include/brotli/decode.h`, with no rename, delete, symlink, binary payload or
extra path. Record/review the resulting patch SHA256 before application. HTTPS
plus the published commit/release is the provenance here, not an invented Google
GPG-signature claim. Reject patch drift even if a filename mentions the CVE.

### 2. Build the smallest distro-preserving package change

Use `dpkg-source -x brotli_1.1.0-2build2.dsc` to unpack and apply the original
distro patch series. Compare each target file's Git blob ID with the backport
commit's parent version; refuse unexpected Ubuntu/local changes to either file
until separately reviewed. Apply the recorded patch only after a successful
zero-fuzz dry run. Capture the modification as a normal Debian quilt patch with
`dpkg-source --commit`, preserving existing packaging and patches.

The precise commands after the verified patch path and unpacked source root are
known are:

```sh
patch --dry-run --batch --forward --fuzz=0 -p1 -i /work/reviewed-patch/0001-Detect-and-report-invalid-stream-earlier.patch
patch --batch --forward --fuzz=0 -p1 -i /work/reviewed-patch/0001-Detect-and-report-invalid-stream-earlier.patch
EDITOR=true dpkg-source --commit . CVE-2026-111497.patch
DEBFULLNAME='Project local security build' DEBEMAIL='local-build@invalid' dch --newversion 1.1.0-2build2+security20261023.1 --distribution noble 'Backport Google Brotli CVE-2026-111497, upstream da9806362874b9bd4b552a3af360b5d6347beb12; no unrelated source changes.'
DEB_BUILD_OPTIONS=parallel=2 DEB_BUILD_PROFILES= dpkg-buildpackage -us -uc -b
```

These commands are **not executed or build-validated yet**. The actual Debian
patch series/build rules must be inspected after source authentication; a
preimage mismatch is a stop, not permission to refresh/fuzz through it. Retain
the original source version, deterministic build timestamp, compiler/dependency
versions, build log and `.buildinfo`; do not use `nocheck` or strip out tests.
Only the two upstream files, the added distro patch/series entry and local
changelog may differ. Rebuild every actually installed binary from this source
whose dependencies require a matching exact version; do not guess the installed
package closure from its source-package name.

### 3. Prove the staged runtime before any host install

Extract candidate `.deb` files into an owned builder-only root with `dpkg-deb
--extract`; this is not installation and runs no maintainer scripts. Extract the
original retained packages separately for comparison.

- For `libbrotlidec`, `libbrotlienc` and `libbrotlicommon`, inspect `readelf -d`,
  version requirements and exported symbols. Require unchanged SONAMEs ending
  `.so.1`, no missing old exports, no unexpected native dependencies and no newer
  GLIBC requirement than the actual Noble host. Review any ABI difference.
- For `python3-brotli`, inspect the staged extension's ELF dependencies. If it
  embeds the decoder, its own rebuilt object must be bound to the patched source;
  if dynamic, prove it loads the staged patched shared objects. A bare import
  of the host's existing module cannot serve as candidate proof.
- In bounded isolated children only, point the loader at staged libraries and
  explicitly insert the staged Python module directory. Assert `brotli` and
  `_brotli` resolve beneath that candidate root, then test Persian/ASCII/binary
  round trips and chunked decompression. Test a benign malformed stream rejects
  normally. Use fixed small inputs, CPU/wall-time and memory/output bounds.
  Never export a loader-path override globally or run these probes in production.
- Check the CLI, if installed, and the actual identified dynamic consumers with
  the same staged libraries. Include both success and expected error semantics.
  Prove old library mappings are absent in the isolated test processes.

The two-file backport intentionally need not change the public upstream version
reported by `BrotliDecoderVersion()` / `brotli.__version__`. Source-to-build
provenance, the local package revision and the staged object identity establish
the fix; a semver-only check would misclassify a correct backport.

No apply entrypoint is part of this preparation. Even a passing staged build
still requires the earlier exact native package/consumer baseline, rollback and
backup admission, transaction review and controlled post-install consumer proof.

## Executable build-only packet — 2026-10-24, not executed

`deploy/maintenance/brotli_native_build.yml` and the four public files under
`deploy/maintenance/brotli_build/` implement the bounded build described above.
Their source contracts are synthetic; they do **not** establish a successful
Docker build, actual candidate ABI, package installation or remediation.

The read-only native inspection found GCC 13.3, make, dpkg-dev and Python 3.12
headers, but no CMake/debhelper/dh-python/all-Python-dev closure. The new recipe
therefore does not install build tools globally. Its official Ubuntu 24.04
amd64 base is pinned to manifest
`sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc`,
resolved from the official public registry. The exact upstream patch is 9821
bytes, SHA256 `3c1e4587947e4c61dd9c7c4619b1ae12d6bedbfd3c33c23981bf31269e18d52c`.
All three Ubuntu source hashes above are checked again inside the builder;
the two full pre/postimage SHA256 values are also pinned. No fuzzy refresh of
the patch is accepted. The original distro patch series and pybuild/CMake test
rules remain intact; the local package version is
`1.1.0-2build2+security20261024.1`, with deterministic build timestamp.

Actual installed `_brotli.cpython-312-x86_64-linux-gnu.so` declares only libc
in its ELF NEEDED entries, not shared libbrotli: the Python decoder is embedded.
Both `libbrotli1` and `python3-brotli` therefore need the eventual reviewed
native package transaction. CLI/dev packages are not currently installed;
the builder still produces all four original binary packages for complete
build/test evidence, without adding any to the host.

Default/check mode performs only bounded public identity/Docker/disk checks.
It refuses a preexisting workspace/image tag, an unpatched daemon, unknown
host, redirected/writable ancestry or insufficient space. It requires free
space on both the output and Docker-store filesystems of at least:

- Native: the unchanged 12 GiB off-host floor **plus 2 GiB build reserve**.
- Web: the unchanged 44 GiB preparation floor **plus 2 GiB build reserve**.

The 2 GiB reserve is a conservative unmeasured build allowance, not a filesystem
quota or an assertion that an actual build fits. Do not start a build during the
Web transition/off-host copy or waive its unchanged floor. Re-evaluate actual
headroom and the authenticated apt dependency closure before execution.

Example commands, only after final source review and appropriate headroom:

```sh
# Read-only admission on native Bot; does not stage or build.
ANSIBLE_CONFIG=deploy/maintenance/ansible.cfg .local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/boot_mount_inventory.yml deploy/maintenance/brotli_native_build.yml
# Build-only: still no host package install, application mount or service action.
ANSIBLE_CONFIG=deploy/maintenance/ansible.cfg .local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/boot_mount_inventory.yml deploy/maintenance/brotli_native_build.yml -e brotli_build_apply=true
```

For Web, use the existing strict `web_engine_inventory.yml` lane and its verified
identity; the source's 26.04 host gate still builds a 24.04 candidate container.
Do not copy a private key or use a new trust-on-first-use host pin.

Toolchain/source acquisition uses authenticated Ubuntu apt **inside the image**
with a 600-second step deadline; the Docker client has a 900-second timeout and
bounded private logs. This daemon-side preparation is **not claimed to have the
compilation container's cgroup CPU/memory limits**. Actual compilation/tests
run in an immutable-ID-selected, offline, read-only-root container with CPU=2,
memory/swap=2 GiB, PID limit256 and a 1500-second total helper deadline. The only
host bind is its newly owned private build-output directory; no application,
Docker socket, env file, key, session or database is mounted. A private empty
Docker client configuration prevents inheritance of host registry credentials.

`output/artifacts/` contains four `.deb` files, `.buildinfo`, `.changes`, actual
candidate test output and a receipt binding package sizes/SHA, exact source and
patch SHA, all recipe hashes and the immutable builder image ID. ABI tests
require unchanged `.so.1` SONAMEs, retained exports/dependencies and GLIBC not
newer than Noble 2.39. Candidate-only C API/Python tests cover round trips,
streaming and bounded malformed/truncated inputs. The harmless C API -17 error
name checks compiled patch presence without an exploit payload. None of this
replaces an actual successful build or authorizes installation/restart.

Failed/partial outputs are not automatically adopted or retried. Keep the exact
owned workspace for diagnosis; no broad Docker/image/volume prune is part of
this playbook. After acceptance/rejection retain only required provenance and
rollback artifacts; schedule removal of redundant build workspace/cache within
10 days under the existing retention policy, never indefinite backups.

## Hosted build-only packet — source ready, not an executed build

The manual **Noble Brotli build-only evidence** workflow provides an ephemeral
GitHub-hosted runner when the native disks cannot preserve their admission
floors. It does not weaken or retarget the native Ansible playbook. It runs one
job, only on published `main`, with read-only repository permission and no SSH,
production configuration, registry credentials, deployment or service action.
Concurrent dispatches queue instead of cancelling a build already running.
The finite outer job budget is 80 minutes so the unchanged individual build,
compile and scanner deadlines can complete without an artificial early retry.

The four recipe files remain byte-for-byte those reviewed in
`9b38f2e1970204bde863d7ad60023e4ec8d48768`; the workflow verifies their four
independent SHA-256 values before copying only those files into a fresh private
context. The official Noble base digest, authenticated exact Ubuntu source,
two-file Google patch/preimages/postimages, complete distro package build/tests
and actual C/Python/ABI regression checks are unchanged. No `nocheck`, refreshed
patch, alternative source or relaxed ABI check is introduced.

The ephemeral runner needs 6 GiB free on its scratch and Docker-store filesystems.
Public toolchain acquisition retains the existing 600-second Dockerfile and
900-second client bounds; actual compilation/tests retain the offline,
read-only-root, two-CPU/2-GiB/PID256 container and 1500-second helper deadline.
The output bind alone is created root-owned 0700: UID 0 without DAC capabilities
cannot enter a foreign-owned 0700 bind. No extra capability repairs that problem.
Only the recorded compile/scanner containers may be removed by this workflow.

A second disposable, network-isolated container installs all four newly built
local `.deb` files into its own writable image overlay, never into the runner or
project hosts. Its actual zero exit status is required before committing that
stopped container as an immutable scanner image. This preserves a genuine dpkg
database for Trivy; scanning a folder of `.deb` files with no detected OS
packages would not be proof. The resulting SBOM must contain all four exact
candidate versions. Raw findings include every severity and unfixed issues with
no ignore file. The scanner's exit code for vulnerability findings is zero
because this job collects evidence, not release acceptance; scanner execution,
missing inventory or artifact-validation errors still fail the job. Existing
release security gates are unchanged.

Trivy's Ubuntu advisories cannot certify a custom source backport. Its report
must be assessed together with the exact compiled patch, package hashes and
actual ABI/C/Python proof, rather than treating an empty report or a version
string as remediation. The native CVE remains open until separately admitted
host installation, affected-consumer restart/health/mapping checks and actual
post-install security review complete. No host install is authorized by a
successful hosted build or artifact download.

A finite privileged copier reads only the four exact candidate packages,
one `.buildinfo`, one `.changes`, actual candidate-test JSON and build receipt.
It checks ownership, ordinary-file/no-link metadata, source/recipe/builder
identity, lengths, hashes and successful nonempty proof. Packages are at most
16 MiB each, metadata at most 1 MiB each and the aggregate at most 96 MiB; the
copier has a 60-second deadline. Only caller-owned 0600 copies and a ninth
`ci-export.json` enter the public package artifact. The three separately
allowlisted scanner reports are at most 8 MiB each. No source tree, cache,
private build workspace or unrelated file is uploaded. Both artifacts expire
after seven days. Status is always `candidate_built_not_installed`, never
`READY`, host-fixed or permission to activate.

After a failure, CI prints only the tails of eight literal public build-log
paths, including the actual root-owned package build log. Stable regular-file,
no-link, allowed-owner and safe-parent checks apply; rejected paths reveal no
file contents. Each JSON-escaped record is at most 8 KiB and all records together
at most 64 KiB, including framing. The copier reads tails with seek/bounded reads,
never whole logs or arbitrary host files. This diagnostic step preserves the
failed result and introduces no acceptance exception.

Source tests use synthetic package bytes and mocked external commands only.
They are not an actual `.deb` build, container-install, ABI, scanner or native
remediation result. Final review and a real manual workflow dispatch are still
required.

### First hosted run: packages passed, report admission remained incomplete

Run `40073262326`, job `120811086785`, successfully built all four patched
packages, passed offline package/ABI/C/Python tests, installed them only in the
disposable scanner image and ran both Trivy reports. The package artifact
`12551834335` is 1,098,270 bytes; its existence is build evidence, not host
remediation. The later scanner-report metadata assertion failed and therefore
the raw-report artifact was withheld. This was not a package-build failure.
The old log did not record the individual owner/mode/link/size fields, so that
particular failed predicate cannot be recovered honestly from its evidence.

The wrapper now creates only the two known scanner output files exclusively
with mode 0600 before scanning, inside its private 0700 report directory.
[Trivy v0.76.1's actual output writer](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/flag/options.go)
uses `os.Create`, which truncates an existing file while preserving its mode;
the workflow never repairs or adopts an unsafe report after production. The
candidate-image record is also created exclusively as 0600.

Report admission logs only each fixed report name, numeric UID/GID, octal mode,
link count, byte size, expected UID and static failed-check names, at most 1 KiB
per record. It never dumps report content or arbitrary file paths. Regular-file,
same-owner, no-follow, single-link, stable-file and 8 MiB per-report checks are
unchanged. A real subsequent run must confirm these fields, all four SBOM
versions and final admission; this source amendment is not a passing run or an
exception to the gate.
