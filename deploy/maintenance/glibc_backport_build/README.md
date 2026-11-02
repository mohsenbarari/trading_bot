# Three closed glibc package candidates

Source only. No package build, dispatch, Host compile/install, image replacement,
or serving change has been performed by this packet's author.

The proposed upstream v7 CVE-2026-84243 hunk is appended after each original
distribution patch series, without fuzz, with its unchanged 83-line regression.
Versions are honest local candidates: Debian `2.41-12+deb13u4+gold1`, Native
Ubuntu `2.39-0ubuntu8.9+gold1`, and Web Ubuntu `2.43-2ubuntu2.4+gold1`. This is
neither an official fixed-version claim nor an assertion that v7 was merged.

Only `tree/.github/workflows/trixie-glibc-backport-build.yml` and the dedicated
`tree/deploy/maintenance/glibc_backport_build/` are proposed tracked changes.
The workflow defaults to unarmed and contains exactly three independent jobs.
Its checkout, action hashes, entry hashes and all fifteen recipe leaves are
pinned. Root reviews, publishes and dispatches it; this packet does not.

Each fresh hosted Ubuntu24 VM must genuinely satisfy 4 CPUs, 12 GiB total RAM
and 56 GiB free on both relevant filesystems. The builder is capped at 4 CPUs,
8 GiB RAM/no swap, 2048 PIDs, 48 GiB growth and an 8 GiB reserve. Public actual
capacity facts print before a capacity gate can refuse. No automatic SDK/cache
deletion is included. Insufficient capacity requires a separate reviewed route.
The original outer lifetime is 18000 seconds, including a reserved 180-second
failure-stop tail; the workflow cap is 330 minutes. No active clock is extended.

Base dpkg status and the public Docker cache hook are preserved before Ubuntu
Python bootstrap. Both original bootstrap and final signed APT indexes are
retained. Every final installed package is covered by its unchanged immutable
base OCI version/architecture or its exact cached DEB plus a selected record
from authenticated Packages. Decompressed Packages SHA/size must match the
original InRelease MetaKey; full indexes stream with bounds rather than being
accumulated. No package-count heuristic establishes closure.

The actual cross-UID child/timeout/group cleanup must qualify inside the private
builder before compilation. CAP_KILL is confined to that container's private
PID namespace, together with the finite packaging capabilities; no Host
capability is added. Source compilation runs as UID10001. Source preparation
uses official signed repositories; all network is disconnected before the
complete default binary build. No nocheck, test filter or flavour waiver is
introduced. FAIL/XPASS refuse independently of dpkg-buildpackage's status.
The new native regression must actually PASS. Its same compiled ELF must fail
only the four specified traversal assertions with the authenticated old loader
and libc, while using the same catalogs/locales and no candidate-library fallback.

Debian image runtime scope is exactly libc6/libc-bin, as independently observed
for the eleven images. Ubuntu scope is each Host's six genuinely installed
packages, with its own six official old DEBs. New outputs must belong to the
signed DSC's finite Binary family and exact local version/architecture. Selected
family file/symlink layout and every top-level ELF's dynamic ABI/loader contract
are compared. Static archive-member ABI is explicitly not separately qualified;
that remains a downstream condition before development-package Host coinstall.
Full raw vendor logs, test ledgers, selected dependency closure, signed APT
metadata and package hashes are retained. Other built families are not implicitly
production eligible.

All outputs remain unsigned local candidates with security acceptance false.
Host eligibility, controlled installation/revival, new Debian image builds and
fresh exact-image scans/probes/new-old restoration/rollout remain separate gates.
Old G12, scanner and restore observations retain their original identities and
clocks; none is relabeled as proof of these unbuilt patched candidates.

Original Source01 and Ubuntu unbound/bound predecessors are preserved. Shared
Source02 changes only the proven process-group/deadline prevention and public
pre-gate capacity telemetry over frozen Source01. Failure cleanup stops only the
exact nonce/CID/image-owned builder, independently of failed forward allocation
guards; Source finality remains HELD if unknown. No failed build is retried here.

Public inert QA: `check_source.py`, `check_dependency_closure.py`,
`check_actual_host_scope.py`, `check_process_groups.py`, `check_final_packet.py`.
The last checker uses existing system PyYAML only for workflow parsing; runtime
entries use stdlib and execute with Python `-I -S -B`.
