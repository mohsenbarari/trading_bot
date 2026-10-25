# Nine historical Market products on the tested E46 security stack

This is a source-only CI packet. No nine-profile candidate has yet been built,
exported, activated or accepted. Global security/deployment acceptance remains
false, autopilot/refactor STOP and clean streak 0. It changes neither the existing
E46 composition lane nor App/datastore controllers or production configuration.

The fixed parent is E46 OS candidate
`sha256:2c6ddd332f0aac17613208e66980e0154933bcb23cf2b94bd3d228c3ad5fd4e7`,
18 layers, exported by run `40145507410` at recipe commit
`e2948eefc6b4e186b13895d298bca38eb6a1a53b`. That run completed all 85 historical
tests, native checks, inventories, scanners, export and owned alias cleanup.
Its unchanged raw gate still failed: 0 CRITICAL, 44 HIGH, 69 MEDIUM, 83 LOW and
1 UNKNOWN. Reusing its OS/Python stack grants no finding waiver or runtime proof.

The new manual workflow is
[market-security-profile-compose.yml](../../.github/workflows/market-security-profile-compose.yml).
`profile=none` is the default and runs no profile build. A selected fixed profile
or `all` uses the closed nine-element matrix, at most three independent hosted CI
jobs at once, with fail-fast disabled. No deployment permission or private input
exists in this lane.

## Evidence that permits stack reuse

The existing nine-profile dossier is pinned at SHA256
`20c08b20e5797891b5c262a4b4310bc2a7ba75f64850fd3a08c786eb78018f6a`.
It individually pins each exact runtime public-source inventory and standard
CPython cache classification. Every historical requirements lock is
`37c5dd760d0816ad0b4546aca8bc5e18875478cdacfb1620bae3d9f5ff29ae29`.
Cached code was never deserialized or executed. The proofs assess normal source
loader selection/header rejection; custom bytecode loaders remain unassessed.

The additional public observation is
[market-profile-stack-parity.json](evidence/2026-10-25-market-profile-stack-parity.json),
40,907 bytes, SHA256
`ec7c9455e1e780b525b96095b1c62eeee66bf17b57c4196c2f8c9557de85d7e4`.
It records the four exact Native and five exact Web profile CIDs plus Web E46.
Each was running and its narrow identity remained stable before/after collection.
No parked QA container was started or changed. The installed public distribution
metadata was read only at `/opt/market-python` using `python -I -S -B` and
`importlib.metadata.distributions(path=[...])`, without site/.pth startup,
application imports, package entry point loading or service changes.

All ten had exactly these nine application distributions:

| Distribution | Observed historical version |
| --- | --- |
| Telethon | 1.44.0 |
| annotated-types | 0.7.0 |
| psycopg2-binary | 2.9.9 |
| pyaes | 1.6.1 |
| pyasn1 | 0.6.4 |
| pydantic | 2.8.2 |
| pydantic_core | 2.20.1 |
| rsa | 4.9.1 |
| typing_extensions | 4.15.0 |

Their Python version was 3.11.16. Their complete explicit non-Env Docker Config
projection differed only in public labels: User `10001:10001`, launch argv,
working directory, ArgsEscaped and health/ports/volumes matched E46. Deprecated
absent Docker fields are represented as null in this observation. This is an
explicit field projection, not a claim that arbitrary unknown API fields or
historical dependency file bytes were compared.

Separately, the single approved immutable image default
`MARKET_PIPELINE_IMAGE_REVISION` was compared in RAM to the sealed public source
commit. Each image contained exactly one matching key; F79 matched its observed
4ef base, not the capture override commit. No private container Env, other image
default values, credential, key, session or database contents were disclosed,
copied or diagnostically hashed. An earlier collection of global site-packages
contained only four bootstrap tools and is not the application dependency proof.

The inherited, already-tested security stack has Python 3.11.17 and the reviewed
psycopg2-binary 2.9.13 source build, plus the fifteen existing util-linux/ncurses/
ACL/tar DEBs. New profile jobs install or rebuild none of those dependencies.
Exact candidate package versions and the untouched stack bytes must still pass
the new CI checks. Historical Name/Version parity alone is not byte equivalence.

## Closed products and historical tests

| Profile | Exact historical product base | Non-cache product files |
| --- | --- | ---: |
| 2239469ab3dd | 4ef8e6dcb2d361b763bd8b72dc730c1f978f564a | 93 |
| 2c3731f573e1 | 7aa96bdd55d90113daf2712ec39185c2a02bd3af | 95 |
| 2e325778cb28 | 532a980404ed10f88e4c713abadb3554568a7419 | 95 |
| 414f6bc8f9bf | 75db7dc7635709d72c93a4e21a0dedc32c52ae65 | 95 |
| 678aaf5cd8e9 | aeaa3f0c2be3fab3ce440575b5ce2e2ed22fb0d1 | 95 |
| a37ec78ec097 | f5b98fee4b7916f0aba4f4676c78a2228714957f | 94 |
| e31aed440f25 | 59e2461d06ffaa81d874d81ca7a4a1a8dd00c751 | 95 |
| f365061d2e76 | 4ef8e6dcb2d361b763bd8b72dc730c1f978f564a | 93 |
| f79cdda21c19 | 4ef base plus the two exact observed overrides below | 93 |

The preparer checks each complete historical Git tree, all copied public bytes,
modes and source/cache controls. Unknown profile, lock, configuration or source
drift refuses. It never substitutes current main product code.

F79 preserves `private_capture.py` from
`adca507b261d3e139befc67b4a937c48c96bb949` and the observed 37,187-byte foundation
file SHA256 `f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da`.
The foundation is reproduced only by the sealed zero-context public patch
`40d0bcaf33d5ef5b5ec714b333274f57af1057973ad757f330c71f6eb09cd292`, after
requiring exact base SHA256
`2b9bcc1b92626cbc1378003bad75f68fb55826cacc45c46e31aadf07be92965f` and checking
the exact final size/hash. No fuzzy application occurs. The override record is
`03bd91fd5606dad574e4bc165b63a8dab2440658a15f8a3cfab6e50fc5b5f85c`.
It is not a claim that the whole F79 product equals plain 4ef or plain adca.

Each product receives the exact five historical test modules and their sixteen
original public test/helper/fixture inputs. Test IDs are derived from those
authenticated source blobs and bound in the capsule; counts vary across
80/85/86/87 rather than assuming 85 universally. The original Bookworm Dockerfile
assertion alone receives the reviewed security-base assertion adaptation. Its
Dockerfile input is the exact underlying E46 security recipe from
`88299eb42bfa05361f8b8ed82358873336a6480e`; the new overlay recipe is checked
separately. No other historical test body changes.

The historical suite runs as UID10001, except the one unchanged synthetic
root-owned metadata fixture, which runs separately as UID0 with no capabilities.
Selections are disjoint, every exact ID is required and skips fail. F79 keeps its
unchanged 4ef historical suite and separately selects only newly added adca stage4
replay test IDs. Changed shared test methods, including decorators, are reported
and are not substituted for old coverage. Changed shared class-support methods
are reported separately because the added replay tests may execute that bound
support code. The complete adca capsule/support blobs
are independently bound; unused adca tests are not counted as executed. Absolute
host/network/native/signal fences precede test/product imports, and the suite
runs in one owned non-main test thread to preserve the signal fence.

## Construction, admission and honest limits

The fixed parent image ZIP is artifact `12580172031`, 63,183,253 bytes, SHA256
`88f4a81f387f357d7e7dc4c4812cbce324ec209606e565d153dd261af13c1a9d`.
Its separately pinned evidence ZIP is artifact `12580311665`, 241,567 bytes,
SHA256 `04e051e29ad84c34180317033f7ab3210c8629b9c6bc14d6b037a14544121280`.
The parent export receipt is
`c241819a46f6a8cbebda4f60937655b90544312e0e7a1f31ff6a99d22da2a812`.
Downloads happen once per selected ephemeral CI job, never as a Native image
file, and are size/hash bounded before accumulation. Full fixed ZIP/gzip/TAR,
single config identity, ordered layer hashes, OCI graph and exact Moby legacy
compatibility chain are admitted before Docker load. Repeated layer references
are allowed in order; duplicate physical TAR entries and undeclared blobs fail.

One normal network-none build layer removes only the five known public product
COPY scopes, then copies the exact prepared files. Public directories are 0755,
files 0644/0755, final ownership UID/GID10001, under a private outer CI directory.
The parent product is checked before replacement. No broad `/app` deletion,
package acquisition, dependency build or service execution occurs. A temporary
build User0 is restored to exact `10001:10001`.

All parent Config fields and Env ordering remain exact except the single
explicit source-revision default and reviewed public provenance labels. Existing
F79 functional/diagnostic labels survive; the new parent/proof/recipe/override
labels identify the actual overlay. Historical revision/tree labels describe the
observed product base, including explicit F79 overrides; they are not a whole-Git
equality claim. The old input signature is retained separately as
`baseline_input_signature` in the public receipts.

`io.gold-trade.release.input-signature` is replaced with a truthful new digest.
Its explicit schema is `market_security_profile_overlay_inputs/1`: SHA256 of the
sorted JSON document with a terminal newline, binding the fixed parent and its
artifact/export pins, every target path/size/hash/mode, exact recipe/helper source
identity, source/cache/stack/dossier/override proofs, historical/replay capsule
hashes and exact effective Dockerfile/replacement-program bytes. The Dockerfile
uses ordinary `SECURITY_PARENT_ALIAS` and `IMAGE_INPUT_SIGNATURE` build arguments
to avoid a self-referential content hash. The closure binds the former to the
fixed immutable parent with a separately verified current-run alias owner receipt;
its ephemeral alias spelling is transport metadata. The latter is explicitly the
digest of that document. All actual Dockerfile bytes are hashed, including those
argument declarations. Root must assess any downstream contract that assumes
the new input digest equals the historical one; it is never restored merely to
pass an old assumption. External composition/export receipts bind the new image
and complete closure. No activation is authorized by a label alone.

The complete 18-layer parent RootFS remains the prefix and exactly one new layer
is allowed. Full dpkg inventory, libc hash and non-product `/app`/`usr/local`/`opt`
stack hashes remain identical. Final export validation additionally checks every
physical entry of that new layer: only the known product paths, narrowly scoped
whiteouts and unchanged-mode/owner directory ancestors are permitted. Candidate
product hashes are rechecked by every test execution. The normal directory mtime
changes involved in copying files are not represented as unrelated behavior
changes. Native, ACL-consumer and component-presence probes retain their original
limited scopes; no callback/static/dlopen or full role-runtime proof is invented.

The alias stays owned and present through final parent/candidate checks and
export, and only its exact current-run tag is removed with `--no-prune` afterward.
Unknown daemon create outcomes remain failures even after bounded best-effort
owned cleanup; absence is not cancellation proof. BuildKit resource cgroup limits
are not claimed. Hosted jobs, child processes, archives and reports remain finite.
Raw/SBOM files are exclusively precreated 0600 so an ordinary scanner with umask
022 can truncate them without relaxing the strict reader. Every severity and
unfixed row is retained; evidence/export/owned cleanup precede the unchanged raw
HIGH/CRITICAL gate. A gate failure can coexist with a complete unaccepted export.

Before any activation, Root must review actual CI results, all profile-specific
source/test/configuration/export identities and the complete role matrix, then
use separately authorized current runtime configuration, data, rollback and
single-owner controls. This workflow does not adopt an expired engine/reboot
window, grant a production lease or close the remaining ACL/other findings.
