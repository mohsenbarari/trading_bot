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

No ordinary image build or serving activation is claimed by these source tests.
The next gates are an actual current-main image build, fresh native/ABI/App and
scan evidence, truthful normal release provenance and verified scoped activation
with backups/rollback. Historical ed59 evidence cannot substitute for those
results. Raw findings and conditional assessments remain visible; global
security acceptance stays false, refactor STOP and its clean-unit streak0.
