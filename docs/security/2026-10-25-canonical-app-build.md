# Canonical App builds with the admitted OS packages

## Purpose and boundary

The historical ed59 candidate passed its defined32 hosted checks, but its
historical revision/tree and missing ordinary release input signature cannot
be relabelled as current main. This integration puts the same authenticated
vendor packages into an actual ordinary-main build. Existing source, tree,
input-signature, prebuilt, backup and release gates remain mandatory.

Scope is the existing App, Bot and sync-worker entrypoints only. A source audit
of main639d9d48 against payloadba1f7616 found22 changed copied paths, including
14 Market modules. Their changed compute/ingestion functions were not called
from the inspected serving entrypoints. API/Bot snapshot inference and price
guards read published snapshots; their imported constants/private-gold filter
and dependencies are unchanged. These findings do not authorize using this
image for Market processors, ingestion, estimator or publisher roles, where
actual business changes would become active.

The read-only effective-source observation covered896 approved public files in
all five serving containers, excluding operational trading_settings.json and
rejecting overlapping configuration/data mounts before any content read.
Each role matched879 historical-payload files and857 current-main files.
The17 payload differences are security-source changes/missing new security
helpers; the additional22 are the source-audited differences above. Unknown
directory entries were counted, not opened.31 new main paths were outside that
content allowlist. This is scoped evidence, not complete current-main parity.

## One authenticated public cache

The tracked
[`app_security_os_packages.lock.json`](../../scripts/app_security_os_packages.lock.json)
binds the existing composer source, its exact3467-byte installer, three official
artifact ZIPs and all15 package names, versions, sizes and hashes. Historical
composer CLIs and their old parent/payload/test constraints are unchanged.

| Family | Run / artifact | ZIP bytes | SHA256 |
| --- | --- | ---: | --- |
| util-linux,9 packages | 40112666640 /12567076825 | 2362423 | a53a204c2ad0b04d2526811eaeb2fa40e0799d720c3d57a9650db9ada4279e8b |
| ncurses,4 packages | 40112668891 /12567123180 | 1319510 | 5d4f181112b2965d177c10e3923b2ba2ff4d2b0ee6716818acc9b7f784bfee08 |
| ACL/tar,2 packages | 40115339187 /12567979094 | 933904 | d367fb3c1b92f069538a6e90a77ea110efec6dccd9286a8f0e1359b285bc1765 |

Normal preparation is:

```bash
python3 -I -B scripts/prepare_app_security_os_cache.py \
  --cache-dir pip_packages/security-os-capsule
```

An offline preparation may instead provide all three explicit public ZIP paths
with `--util-zip`, `--ncurses-zip` and `--acl-tar-zip`. Partial source sets refuse.
The network path uses only the bounded official artifact GET with normal gh
authentication; no code, runtime configuration or user data is uploaded.

The cache contains exactly15 DEBs and manifest.json, with0755 directory and0644
public files. The manifest is3266 bytes, SHA256
`11f0b90693a7349db9ce977ebd1eebcf5ce690166960c6e9fba2ff1e25984b81`.
Source/lock/artifact/cache FDs and identities remain bound through publication.
A retained empty flock inode serializes preparation; atomic no-clobber rename
publishes one complete cache. Existing invalid namespaces are not overwritten.
`--check` performs offline validation without creating or modifying the cache.

## Ordinary builder integration

Both ordinary Dockerfiles run the installer after runtime Python dependencies
and scripts COPY, before trust/native verification. BuildKit mounts only the
cache read-only at/capsule and provides volatile/tmp with network disabled.
The existing setpriv contract drops capabilities to0xd9, clears inherited and
ambient sets and enables no-new-privileges. Installation requires actual root
and amd64/all packages. A public authenticated readonly input may retain hosted
UID1001 ownership; execution/source/lock requirements are not relaxed.

The unchanged installer plans and upgrades exactly the fifteen admitted
packages, permits no removals, preserves the complete unrelated package set
and libc6 version, verifies every resulting target version and checks dpkg
audit. A refreshed incompatible base must fail and be reassessed; no downgrade
or version waiver is authorized. Cache archives/installers are not copied into
the final filesystem. Both Dockerfiles already create the three immutable
runtime directories, so no historical mountpoint child or COW adaptation is
needed.

The isolated installer child has hard resource limits, fixed actual-command
allowlists and finite capture memfds sealed against growth/shrink. Captured
output is admitted before reading into Python; it is never emitted. Child-only
subprocess adapters preserve normal byte/text/check behavior without unbounded
PIPE accumulation. Parent stdlib/limits stay unchanged; owned descendants are
killed/reaped on timeout/failure, cleanup-only uncertainty refuses, and retained
cache/source checks also run on failure. The exact installer SHA remains
`1e01e6c524e95ed626a57cf87b51d75730fc70444f9419fc2cf3a93d01928215`.
The public package inspection found816 data members,12563637 bytes in total,
and a maximum member506608 bytes; package writes have a separate hard file limit
from the2MiB capture admission.

Preparation precedes foreign/Iran signatures, context copies, transfers and
builds in deploy.sh and production_deploy_online.sh. CI backend and staging
builders also prepare first. Foreign required-prebuilt and online prepared
cache verification use check-only mode; cold preparation creates the exact
signed cache parent. Existing fingerprints recursively include pip_packages
and scripts, so no unsigned build argument or signature-format exception is
introduced. Browser jobs grant only contents/actions read and expose their
normal ephemeral token only to the bootstrap step.

## Verification and remaining work

Root independently passed94 combined core/composition/builder-contract tests
in7.568s without skips, using cleared public test environment and only owned
synthetic Python children. An independent reviewer passed the77 core/composition
tests and confirmed the prior output-bound issue is closed. A separate actual
offline exercise admitted the three exact public ZIPs, prepared/check-verified/
reused the16-file cache, with no network or package commands. Shell syntax and
focused ordering/prebuilt/cold-parent/capability tests passed.

Actual CI40187533525 on700c40ead2 completed all12 browser backend bootstraps,
each preparing and installing the15 admitted packages with manifest11f0b906.
The three former-failing history shard4 jobs each passed48 browser tests.
These results prove those ordinary builds and scenarios, not a release export
or serving activation. The next gates are a provenance-bound image export,
fresh native/ABI/App and
scan evidence, truthful normal release provenance and verified scoped activation
with backups/rollback. Historical ed59 evidence cannot substitute for those
results. Raw findings and conditional assessments remain visible; global
security acceptance stays false, refactor STOP and its clean-unit streak0.

## Canonical hosted export

The separate manual main-only workflow `canonical-app-image.yml` builds the
ordinary Dockerfile from its actual dispatched Git commit. Its standalone
helper verifies the clean checkout, Git blobs and retained public source FDs,
then prepares the authenticated OS cache before normal frontend npm ci/build.
Only tracked COPY inputs, the exact15 authenticated DEBs/cache manifest, and
normal generated frontend output are admitted; unknown ignored input files
refuse before content read/hash. Source FD capacity is checked before opening.

The build computes the existing foreign-image signature byte format, including
empty-directory behavior. Actual commit/tree/signature labels are supplied to
that real build, with source and generated-input checks repeated afterward.
No guarded deployment wrapper is sourced or changed. This does not turn the
historical ed59 image into a main release or consume the dirty production tree.

The exact built image receives unfiltered Trivy0.76.1 JSON and CycloneDX SBOM.
Both report bindings, complete Config comparisons in RAM, archive Config digest,
whole RootFS association, bounded export/gzip hashes and input manifests enter
the unsigned public evidence. Native ABI receipts state only the completed
ordinary Dockerfile checks. Full physical archive admission remains a separate
receiving gate. No production configuration or host data enters this job.

The image artifact lasts7 days and evidence30 days; both upload before the final
unchanged strict HIGH/CRITICAL raw gate. A raw finding keeps that gate failed;
exported_not_accepted, signatures_verified:false and security_accepted:false
cannot authorize activation. Root and an independent reviewer each passed all
14 new hermetic tests, including real bounded owned Python children and literal
ordinary signature comparison; core/cache combined91 tests also passed.
No actual run/export of this new workflow is claimed yet. A separately reviewed
App-only immutable-artifact activation contract is still required; existing
C/S/R/COW guards remain untouched.

The first actual run40189220376 on a5af2137 passed Git/cache preparation and
the normal frontend build, then refused before Docker with input_inventory.
The admission alphabet omitted the tilde used by six of the15 authenticated
Debian package filenames. No image/export/scan exists for that attempt.
The narrow correction adds tilde to the basename alphabet without accepting
path separators or whitespace. A regression now consumes the actual Git-bound
15-name vendor lock and exercises those six names plus traversal/unsafe-name
negatives. Root and an independent reviewer each passed all15 focused tests.
The failed run remains failed; a fresh actual build is required.

Second run40190266477 on9e9eeaef passed ordinary Dockerfile build, native checks,
raw scan and SBOM. Its finalizer refused git_dirty before any export. The pinned
Trivy action defaults its persistent database cache to the Git workspace:
[action.yaml](https://github.com/aquasecurity/trivy-action/blob/ed142fd0673e97e23eac54620cfb913e5ce36c25/action.yaml).
The pinned setup installs its binary outside Git and removes its temporary
checkout on success; the action also removes its environment file. The cache is
therefore the source-proven persistent-workspace candidate, not a reconstructed
listing of the failed runner. Both scans now explicitly use the owned capsule's
trivy-cache outside Git. The strict clean-checkout/input guards remain intact;
no ignored-dirt exception or evidence deletion is introduced. Focused workflow
contracts require this exact external cache path. The previous build/scans are
real evidence for their exact SHA, not a successful export or new serving role.

Third run40191605131 on frozen main665220a2 completed the ordinary build,
native checks, raw scan, SBOM and both artifact uploads. Config is
`sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1`;
the ordered RootFS has33 layers. The strict raw gate alone failed on
0CRITICAL/44HIGH/68MEDIUM/82LOW/1UNKNOWN. It remains failed and unaccepted.

The actual image ZIP12598511333 is183170277 bytes, SHA256
`46b1e1f0d826493db7d18b7835acccdb75af4282ac9d504dc21d842e8608b8d6`.
It was transferred directly to Web, without a large Native archive. Actual
Web observationaed34797d49f45d1973e846cbe98d73c verified ZIP/export identity,
gzip/TAR hashes,71 physical TAR members and all33 ordered DiffIDs. The TAR is
452719104 bytes, SHA256
`e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67`.
Measurement does not certify legacy compatibility bytes, full Config or LOAD.

The separate canonical runtime workflow now binds those actual observations
before any disposable image LOAD. It must prove the strict complete archive
graph, fresh component/ACL inventories and the unchanged32 runtime/component
assertions. Its controller source advances on the security branch while main
stays665220a2; the controller commit must never be relabelled as the image source.
No serving replacement, runtime-smoke success or security acceptance is claimed
by this source packet.

## Actual smoke and new normal activation contract

Actual canonical smoke run40196208692 on controller200f5c71 succeeded:8 runtime
and24 component assertions, strict complete archive/Config association,
migration headff6c7d8e9f01, fresh component/ACL inventories and owned cleanup.
Artifact12599936628 is392398 bytes, SHA256
`5cd1ab49b517bcd04c7adc1e883ecf351020f96b16ccfec70278037c5c013e99`.
This does not change the failed raw44HIGH gate or the serving containers.

The new `canonical_app_activation_{contract,site,coordinator}.py` is an
independent normal-Compose entrypoint. It remains UNARMED until actual two-host
LOAD receipts, fresh backup/restore, schema and complete normal configuration
evidence exist. Historical C/S/R/COW tools are not imported or invoked.
The fixed order is Web App, Web sync, Native App, Native Bot, Native sync.
It retains the complete active Compose prefix, opaque environment references,
eight reader units, existing business configuration and single execution owners.
Each handoff proves the old cgroup empty before stopped-first creation, checks
the full candidate configuration before start and validates normal readiness.
Known measured failures permit only proved owned rollback; ambiguous RPCs retain
the guards and evidence. Writer and DNS changes are outside this authority.

Pre-stop admission now rejects unexplained StopTimeout values as well as
unsupported Compose features. Actual32 public fixtures passed inside a real
isolated read-only Root container: CAP_CHOWN only, no network/private mounts,
unchanged pre-import ambient fence and no skips. Result SHA256
`38ecb17f5932448b0210a6b147d467ee1bcf2728070ff5cbc7f0c4e40c256ef6`.
Two separate owned-child bound/timeout/cleanup tests passed as real UID1001
with no capabilities. Root ownership tests remain mandatory in the dedicated
CI lane; generic child-process tests stay in the ordinary hosted lane.
No serving activation or global security acceptance follows from these tests.
