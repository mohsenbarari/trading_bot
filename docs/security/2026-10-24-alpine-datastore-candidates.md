# Exact Alpine datastore security candidates

The first canonical profiles actually built and passed ordinary GitHub-hosted CI
as recorded below; no datastore image has been installed, accepted or deployed.
The approved lower-finding follow-up remains source-only. The App composition job remains
the default. `security-os-candidate-compose.yml` adds the explicit, default-off
`datastore_profile=postgres|redis` choices and uses the existing composition
controller only for bounded command/file I/O, image comparison and export.

Read-only public software/image facts collected on Native and Web are recorded in
Native `.local/security-datastore-image-baselines-20261024-01.json` (0600), SHA256
`70889f7b6856039fa11a44eb3c208a52f9a1a13f1ea7b49b9360653e625d52f7`.
No private Env/session, database/configuration contents, data, SQL or image export
was read in that collection. Public image defaults inspected later in isolated CI
are not production container environment values.

| Profile | Immutable public reference | Config identity | Protected server |
| --- | --- | --- | --- |
| PostgreSQL | `postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b` | `sha256:aad6289ca337b3ce76896f2e7e61480490152886c7828120371fb28e6b779e1d` | PostgreSQL 15.19, SHA256 `1e08a0e796ae0461ec40031e9bd7a76831d453e723812b9c3e8757bc411a38c7` |
| Redis | `redis@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf` | `sha256:5509c0097c6064aa8a3b1df58f1d950e67090fffa6678ae8f3f1dc2385f12deb` | Redis 7.4.11, SHA256 `67adb510c4c9db9585ad63404950694241c748ad6f34c3e1cc8fdc095268f1b6` |

The source contains all eleven/seven ordered baseline diffIDs and exact server and
entrypoint hashes. Native aad/Web 7bf/Web fe0737 observations share the eleven
diffIDs and public software bytes. That does not prove equal registry manifest
identities. The independently observed fe0737 index selects the exact 3,054-byte
manifest `sha256:a2c20749c564b4eb73a77bfda626f8a3cde1bbfae020fb97c616a00cdc1a2181`
then config aad (9,091 bytes). Redis ff02 selects the exact 2,288-byte manifest
`sha256:1db42ccef14898aa29bae778452d567534b59c107129cbc1163fb552de184d3c`
then config 5509 (5,531 bytes). Both public indexes contain sixteen children.
The recipe hashes the actual raw index/manifest, requires exactly one Linux/amd64
child with that pinned digest/size, and preserves all three identity types.
Docker daemon identities may refer to the proved index, platform manifest or
config. Every local RootFS and server byte check remains strict. Buildx's
documented `imagetools inspect --raw` preserves
manifest bytes without an appended newline ([upstream implementation](https://github.com/docker/buildx/blob/v0.29.1/util/imagetools/printers.go)).

These candidates preserve the **Native canonical image Config** exactly. A later
read-only typed archive proof also bound Web 7bf→config aad and Web 823→config5509,
with the complete eleven/seven ordered raw diffIDs. Its 3,904-byte public receipt
is Native `.local/security-runtime-audit-controller-20261024-web-04/public-datastore-image-config-proof.json`,
SHA256 `4af0d3e9d694e4869c0158bde2171b339713df4e4b2dcfc75d0b5e533a9ffbdb`.
This closes immutable image Config equivalence by identical config digests; it
does not compare/read/hash production container Env or per-role runtime overrides,
verify publisher attestations, or grant activation. Earlier evidence is unchanged.
No fe0737→7bf registry graph is invented: Web's local manifest and the canonical
public platform manifest remain distinct identities.

The distinct running Web image 994e5b… is PostgreSQL 15.18, server SHA256
`bb5d3bd97463136573859b6b9db854289f6872f89ddf390f4e89e444d8bb8d67`,
with fourteen different diffIDs and no RepoDigest. It is unsupported by this
packet. Resolving its public immutable lineage or separately approving its exact
image transfer/retirement remains required. It must not be replaced by silently
upgrading it to 15.19.

## Closed security changes

PostgreSQL requires Alpine 3.24.x and upgrades only `libssl3`/`libcrypto3` to
3.5.9-r0, `libuuid` to 2.42.3-r1, `musl`/`musl-utils` from 1.2.6-r2 to 1.2.6-r3,
and `zlib` from 1.3.2-r0 to 1.3.2-r1. OpenSSL's same-branch source is 3.5.9;
its security history includes the 3.5.8 fixes. Redis requires Alpine 3.21.x and
upgrades only `libssl3`/`libcrypto3` to 3.3.7-r2, `musl`/`musl-utils` from 1.2.5-r11
to 1.2.5-r12, and `zlib` from 1.3.2-r0 to 1.3.2-r1. These are vendor versions, with no
locally invented fixed version. See the exact branch
[OpenSSL 3.24 recipe](https://github.com/alpinelinux/aports/blob/3.24-stable/main/openssl/APKBUILD),
[util-linux 3.24 recipe](https://github.com/alpinelinux/aports/blob/3.24-stable/main/util-linux/APKBUILD),
and [OpenSSL 3.21 recipe](https://github.com/alpinelinux/aports/blob/3.21-stable/main/openssl/APKBUILD).

CI downloads the bounded original signed branch `APKINDEX.tar.gz` and only those
exact APKs. No signature/index is rewritten. The original immutable baseline's
Alpine key store verifies the packages and signed index in a normal offline APK
simulation. Final installation uses the same local repository and
`apk --no-network --no-cache … fix --upgrade <closed names>` inside a
`RUN --network=none`; there are no other package files for additional dependencies.
Unavailable versions, changing index contents, missing dependencies or signature
failures stop the build. No cross-branch repository, broad upgrade,
`--allow-untrusted` or world-file rewrite is permitted. `fix` takes package-name
patterns, not `name=version` constraints; exact selected versions are verified from
the complete installed inventory. The local mirror/signature behavior is specified
by [apk repositories](https://github.com/alpinelinux/apk-tools/blob/v2.14.4/doc/apk-repositories.5.scd)
and [apk fix implementation](https://github.com/alpinelinux/apk-tools/blob/v2.14.4/src/app_fix.c).
The equivalent [apk 3.0.8 repository contract](https://github.com/alpinelinux/apk-tools/blob/v3.0.8/doc/apk-repositories.5.scd)
defaults an untyped local repository to the v2 index layout and disables other
repository directories when `--repositories-file` is set. Its
[context preparation](https://github.com/alpinelinux/apk-tools/blob/v3.0.8/src/context.c)
also changes simulation database access to read-only. CI records the exact
installed apk-tools version; no production package-manager version was inferred.

The root/capability-zero signature-check container reads only an explicitly
published public capsule: the exact repository/architecture directories are
0755, the hash/inventory-checked APK/index files and repository list are 0644.
Publication uses retained descriptors, refuses unexpected paths/owners/links or
changed bytes, and explicitly sets modes under umask077. The enclosing context
and task root remain 0700. SDK/gosu compiler inputs remain 0600 inside that private
context; no new capability or DAC override is granted. Fixture certificates were
already published in their separate 0755/0644 disposable public directory.

PostgreSQL additionally rebuilds exactly gosu 1.19 source commit
`6456aaa0f3c854d199d0f037f068eb97515b7513`. All five original source files have
size/Git-blob pins. Its three Go code files and CLI/version1.19 stay unchanged;
only `go.mod`/`go.sum` receive the declared security lock delta from
`github.com/moby/sys/user` v0.1.0 to v0.4.1. Exact original/effective hashes and
the two official SumDB h1 records are preserved. `golang.org/x/sys` stays v0.1.0.
The official
Go 1.26.10 Linux/amd64 SDK is 66,936,659 bytes, SHA256
`ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5`
([official download metadata](https://go.dev/dl/?mode=json&include=all)). The first
isolated build stage acquires the locked Go modules with normal checksum
verification; an exact five-file effective-source checksum manifest must still
match before/after acquisition and compilation. Compilation then uses
`CGO_ENABLED=0`, `GOTOOLCHAIN=local`,
`GOPROXY=off`, `GOSUMDB=off`, `-mod=readonly`, and network none. No `-s` flag is
used, preserving binary build information/symbols as in the
[upstream recipe](https://github.com/tianon/gosu/blob/6456aaa0f3c854d199d0f037f068eb97515b7513/Dockerfile).
The source's truthful `1.19` version stays unchanged; external provenance records
the source commit, SDK and actual binary hash. The SDK/compiler/modules do not
enter the final image. Redis's observed image has no gosu and gains none.
The [official pinned Go-Alpine recipe](https://github.com/docker-library/golang/blob/650cf2f338d22da87df8c773cfc88afa26699502/1.26/alpine3.24/Dockerfile)
uses this exact Linux/amd64 SDK and digest directly on Alpine 3.24 and runs its
`go version` check. This supports the compiler-stage choice; the actual candidate
build remains untested.

Both final stages start FROM the exact original public reference. They add no
runtime `ENV`, label, user, entrypoint, command or volume instruction. Complete
before/after Docker `Config` equality and an exact ordered RootFS prefix are
required. Complete installed package inventories must show only the closed
updates, with no package addition/removal or server upgrade. The former blanket
musl-unchanged condition is replaced only by the exact per-profile revision above,
the same upstream1.2.6/1.2.5 family, an actually changed loader/library hash and
a separate receipt binding old/new library SHA256 to the signed vendor APK/index
hashes. No generic libc override exists. A complete exported-symbol ABI matrix
is NOT_TESTED; the required ordinary server/TLS/data/restore/restart matrix still
runs against the exact preserved server binaries. World,
original repositories and vendor key hashes remain unchanged. Every regular file
under `/usr/local` is hashed, and all node modes/owners/types and symlink targets
are compared; only PostgreSQL's exact gosu binary may change. This includes all
server/support/entrypoint bytes, not merely their reported versions.

## Evidence and limitations

Only GitHub-hosted CI in the fixed repository is admitted. Published source blobs
are sealed outside the build context. Download reads are bounded before
accumulation, use finite timeouts and retain file descriptors/identities through
the build. Each fixture has an exact owner/name/image/CID, no network, no host
ports, a read-only root, finite memory/CPU/PID/time limits, and explicit bounded
tmpfs overrides for **every** inherited volume. This prevents anonymous volumes
from the PostgreSQL/Redis image declarations. Compiler/network acquisition is
confined to CI build stages; builder cgroup resource limits are not asserted.

Fresh ordinary fixtures run on both the baseline and candidate: PostgreSQL
initialization, table/data, TLS loopback, dump/restore and restart; Redis PING,
SET/GET, TLS loopback, AOF and RDB restart; and PostgreSQL gosu named/numeric
UID/GID/supplementary-groups/exec behavior. TLS is an explicit build requirement
for these fixed baselines; unsupported baseline TLS stops the job. Certificates
and disposable fixture keys are public CI test material, never production keys.
No production data, host mounts, SQL endpoint or private configuration is used.
Unknown daemon-create outcomes remain failures with `UNKNOWN` evidence even if
bounded owner-checked cleanup finds/removes a container; absence never proves
cancellation.

The same actual image receives full unfiltered vulnerability and CycloneDX reports,
including all severities and unfixed findings. Finalization rechecks sealed source,
the registry/baseline, full Config/layers and every evidence hash, then exports that
same immutable image and binds raw report/archive digests. Docker API empty-default
omission is normalized only when comparing scanner Config JSON; exact whole
before/after Docker Config equality is never normalized. Unknown fields and
boolean/integer substitutions still refuse. `security_accepted`,
`scanner_gate_waived`, `production_installed` and `production_data_used` remain
false. No vulnerability count is predicted and no role/deployment admission is
granted. Actual CI, independent review, raw findings and the full role matrix are
still required.

Hermetic source validation:

```sh
python3 -I -B -W error -m unittest discover -s tests -p test_compose_alpine_datastore_security.py -v
```

Tests deny ambient commands, networking and native library loading before recipe
imports. Named inert command/download models exercise the complete real
compose/acquire/fixture/cleanup/report/finalize flow for both profiles, including a
server-byte-drift refusal. They are not substitutes for the actual ordinary CI
build and server fixtures.

## Actual first CI refusals and narrow correction

[PostgreSQL run40139191263](https://github.com/mohsenbarari/trading_bot/actions/runs/40139191263)
and [Redis run40139205274](https://github.com/mohsenbarari/trading_bot/actions/runs/40139205274)
at source `42c3cd898c7ccc8671da0f788cfb8fdf745ef2e1` sealed the published helpers,
then failed at `pull_baseline` with `command_failed`. Neither reached registry
inspection, any fixture/container creation, APK/SDK acquisition, build, scan or
export. Their public artifacts remain unchanged:

- PostgreSQL artifact12578074423: 641 bytes, SHA256
  `c5775097c98dc23219c2b03ecb2461063897391ea641d772341da75c0f03d033`.
- Redis artifact12577849560: 638 bytes, SHA256
  `ef09d0a8824473fed332bf14dc2c2f26fe40d208a3a74b596ea92c2f2194325e`.

The original pull stderr was not retained, so its exact historical error text
cannot be asserted. Independent correctly scoped public registry requests then
proved 404/`MANIFEST_UNKNOWN` for 7bf/823 and 200 for the canonical fe/ff02 indexes,
their selected platform manifests and exact aad/5509 config blobs. Config digest,
architecture, OS, all eleven/seven ordered diffIDs and volume declarations matched
the approved Native baselines. No layer or running-container data was downloaded.
The bounded public projection is
`/root/trading-bot/trading_bot/.local/security-datastore-registry-baselines-20261024-02.json`,
8,612 bytes, SHA256
`47484f3eafd4c0ad7453593ec72350eb9267445992bde88acf384a390b9fe661`.
The earlier invalid anonymous-scope probe01 is explicitly superseded and supplies
no registry-validity conclusion.

The correction selects those original canonical roots and pins their exact
Linux/amd64 manifests, preserving every server/Config/layer/package gate. It adds
a bounded `baseline-pull.log` including public stderr on success or refusal, with
no automatic retry. Review also caught the still-unreached root/cap0 public-input
permission seam; its scoped publication fix above grants no extra capability.
The changed source is not an actual successful rebuild or activation. Fresh CI
and its unchanged raw reports remain required.

## Actual canonical candidates and remaining rows

At source `ece8725125fdfdbbc376f9a764e14adf0dcd0255`, both ordinary CI jobs
completed successfully; neither candidate was installed or accepted:

- [PostgreSQL40140201428](https://github.com/mohsenbarari/trading_bot/actions/runs/40140201428):
  image `sha256:7ba5515e3d172586f8f3ee9c9ddbc902b3897d7aa3f4e75f83396aa185bed54c`,
  fourteen evidence hashes verified, full canonical Config equality, eleven-layer
  prefix, unchanged 15.19 server/support bytes, fresh TLS/dump/restore/restart and
  gosu checks. Public artifact12577334979 is 525,062 bytes, SHA256
  `f7585594527fb6b2c0bd9fa2cd96514a81d7c61b20427b7c2d98111539a0eb49`.
- [Redis40140214939](https://github.com/mohsenbarari/trading_bot/actions/runs/40140214939):
  image `sha256:1225116e02298392d7e8e39c2da47933d52f4bfb8cb667059626e4a12028d9b0`,
  twelve evidence hashes verified, full Config/seven-layer prefix and unchanged
  7.4.11 server/support bytes, fresh TLS/AOF/RDB/restart checks and no gosu.
  Public artifact12578701727 is 26,925 bytes, SHA256
  `90a1591889bad0495e12c8bb8232d4abcdbeb587fecfc69e5673b9b63fa9633b`.

Both unchanged raw reports contain zero CRITICAL/HIGH findings. PostgreSQL retains
two MEDIUM and three UNKNOWN rows; Redis retains one MEDIUM and two UNKNOWN rows.
UNKNOWN is not a disposition. The original reports and earlier failed runs remain
unchanged. Image artifacts12579005232/12578316964 were not downloaded to Native.
Those initial evidence ZIPs omitted the small `image-export.json`; this follow-up
also publishes that public receipt alongside reports, without duplicating image
bytes or changing the immutable export.

## Approved lower-finding follow-up, source-only

The new closed changes above address these remaining rows; a fresh build/scan
and independent review are still required:

| Finding | Actual scope and minimal disposition |
| --- | --- |
| CVE-2026-107891, musl and musl-utils (two rows per image) | Maintainer identifies libc `regcomp` integer overflow when compiling untrusted expressions, through 1.2.6. Exact complete-role reachability is not proved absent. Apply the vendor revisions above; never ignore the UNKNOWN severity. |
| CVE-2026-85091, zlib | Affects nonblocking gzwrite/gzprintf state in gz_vacate. No universal role-absence claim is made. Both Alpine branches publish 1.3.2-r1; use the signed vendor fix. |
| CVE-2026-61801, PostgreSQL's gosu dependency | `SetupUser` calls moby/sys/user's passwd/group parser; attacker-controlled file contents are the advisory's precondition. Rather than infer that condition absent, the exact lock-only delta selects the fixed 0.4.1 module. |
| CVE-2026-39824, PostgreSQL's x/sys module | Go's advisory identifies only x/sys/windows.NewNTUnicodeString on Windows. The Linux/amd64 gosu imports x/sys/unix. Keep 0.1.0 and the raw UNKNOWN row, and produce the specific affected-code-absence proof below; do not call the module version fixed. |

Primary sources: [musl maintainer advisory](https://www.openwall.com/lists/musl/2026/10/21/1),
[Alpine's21October security release](https://www.alpinelinux.org/posts/Alpine-3.21.9-3.22.7-3.23.7-3.24.3-released.html),
[Alpine zlib fixes](https://security.alpinelinux.org/vuln/CVE-2026-85091),
[moby/sys/user advisory](https://github.com/moby/sys/security/advisories/GHSA-mjcv-p78q-w5fw),
and [Go's platform-specific advisory](https://pkg.go.dev/vuln/GO-2026-5024).
All six exact musl/utils/zlib APK URLs returned 200 to bounded HEAD observations;
official SumDB/proxy records identify user 0.4.1 at source85a71bbe1faa36c552a960e6a5f3d0cfb632fbbe.
The small public observations are Native
`.local/security-datastore-followup-public-20261024-01.json`, SHA256
`846df3ae578ce7941d538fdc639409f5d523a4a7a7f66b73190748a53ac87d0e`.
HEAD/source metadata alone is not an APK signature, install or ABI result.

The normal Go SDK must authenticate the newly pinned module through SumDB during
acquisition, retain the exact effective mod/sum/code checksums, then compile
offline with readonly modules. The actual compile stage checks Linux/amd64,
module versions, its complete `go list -deps` result, and `go tool nm` absence of
all x/sys/windows symbols before printing one fixed proof marker. The controller
rejects a missing, duplicated, command-echo-only or wrong-platform marker and
binds the proof to the actual image/gosu/log hashes. SBOM must show user0.4.1 and
x/sys0.1.0. The receipt explicitly says module-version-fixed=false and
raw-finding-modified=false. This is a scoped code/platform fact for review,
not a scanner ignore, policy change or global security acceptance.
