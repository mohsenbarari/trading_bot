# Exact Alpine datastore security candidates

This is a source packet for ordinary GitHub-hosted CI. It has not built, installed,
accepted or deployed a datastore image. The existing App composition job remains
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
| PostgreSQL | `postgres@sha256:7bf293f93568d967c8dfcf6d95c2ff522289dc11c558989a9c6f926a644d6dfa` | `sha256:aad6289ca337b3ce76896f2e7e61480490152886c7828120371fb28e6b779e1d` | PostgreSQL 15.19, SHA256 `1e08a0e796ae0461ec40031e9bd7a76831d453e723812b9c3e8757bc411a38c7` |
| Redis | `redis@sha256:8231d3786e10c470840c27963f3d19672dc7ef3e5d5adc512c01d44faada5672` | `sha256:5509c0097c6064aa8a3b1df58f1d950e67090fffa6678ae8f3f1dc2385f12deb` | Redis 7.4.11, SHA256 `67adb510c4c9db9585ad63404950694241c748ad6f34c3e1cc8fdc095268f1b6` |

The source contains all eleven/seven ordered baseline diffIDs and exact server and
entrypoint hashes. Native aad/Web 7bf/Web fe0737 observations share the eleven
diffIDs and public software bytes. That does not prove equal registry manifest
identities. The independently observed fe0737 index selects a2c20749… then config
aad; the recipe does not invent a fe0737→7bf graph. It hashes the public reference's
actual raw registry index/manifest, selects exactly one Linux/amd64 child when
needed, checks that child's raw digest and config descriptor, and preserves all
three identity types in its receipt. Docker daemon identities may refer to the
proved index, platform manifest or config. Every local RootFS and server byte
check remains strict. Buildx's documented `imagetools inspect --raw` preserves
manifest bytes without an appended newline ([upstream implementation](https://github.com/docker/buildx/blob/v0.29.1/util/imagetools/printers.go)).

The distinct running Web image 994e5b… is PostgreSQL 15.18, server SHA256
`bb5d3bd97463136573859b6b9db854289f6872f89ddf390f4e89e444d8bb8d67`,
with fourteen different diffIDs and no RepoDigest. It is unsupported by this
packet. Resolving its public immutable lineage or separately approving its exact
image transfer/retirement remains required. It must not be replaced by silently
upgrading it to 15.19.

## Closed security changes

PostgreSQL requires Alpine 3.24.x and upgrades only `libssl3`/`libcrypto3` to
3.5.9-r0 and `libuuid` to 2.42.3-r1. OpenSSL's current same-branch source is 3.5.9;
its security history includes the 3.5.8 fixes. Redis requires Alpine 3.21.x and
upgrades only `libssl3`/`libcrypto3` to 3.3.7-r2. These are vendor versions, with no
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

PostgreSQL additionally rebuilds exactly gosu 1.19 source commit
`6456aaa0f3c854d199d0f037f068eb97515b7513`. All five build source files have
size/Git-blob pins; `go.mod` and `go.sum` stay unchanged. The official
Go 1.26.10 Linux/amd64 SDK is 66,936,659 bytes, SHA256
`ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5`
([official download metadata](https://go.dev/dl/?mode=json&include=all)). The first
isolated build stage acquires the locked Go modules with normal checksum
verification; compilation then uses `CGO_ENABLED=0`, `GOTOOLCHAIN=local`,
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
updates, with no package addition/removal, musl change or server upgrade. World,
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
