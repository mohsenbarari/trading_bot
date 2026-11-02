# E46 historical Market security candidate

This is a default-off `market_security_payload` lane in the existing pre-release
workflow. The first actual CI build passed native checks but failed its test
fixture contexts as recorded below. The corrected source has not rerun; no
candidate has been scanned, activated or deployed. Other Market profiles,
including Capture2/F79, refuse admission.
The App candidate and its 44 HIGH findings have separate evidence and disposition.

The product source is exactly commit
`a6dcd636a1525a3a3d47e2a6083ca887b16080db`, tree
`259c9c4964864b7acfd92f94c9856e664130d082`, from observed image
`sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd`.
All 93 Git-tracked files in the Dockerfile's complete five COPY scopes match the
actual public runtime files in bytes, mode and UID/GID 10001. Configuration release
f3 is not this image's source identity. Current-main product modules are never
copied. Whole mixed-tree and actual context-input signatures are separate from
the historical product and current recipe identities.

The original 198-file public inventory remains byte-for-byte unchanged at SHA256
`2e7dda021162d9800eef7a3705fb479a9fc9d8433c4f3ed4a65429fc7364d4a3`.
Its 105 extra files are individually bound to supplemental receipt
`a8b4c4d0358b17a0024306d3cdcc60466c126f4aad36ea0270e996b1cd9a2070`.
The bounded collector ran inside the existing E46 container using CPython 3.11.16:
77 files have nonselected `cpython-312` tags; the remaining 28 `cpython-311` files
have stale timestamp/size headers. Every cache SHA, 16-byte header, matching public
source size/mtime/SHA and version-specific source hash was independently checked.
No marshal data was decoded, application imported or cached code executed.

Admission retains all 198 rows and requires the complete 105-row supplement. It
recomputes the exact nonselection/staleness decision, rejects unknown/valid/
unchecked caches and admits only the 93 product files into the candidate. There
is no extension-wide ignore. The historical Dockerignore excludes bytecode, and
its Dockerfile disables bytecode writes and normalizes source mtimes. All 80
Python files were examined for explicit bytecode loading. The sole importlib
loader in `single_owner_parity.py` loads the fixed source path
`scripts/ingest_capture_event_spools.py`; no direct cache loader was found in this
declared COPY source scope. This does not assess arbitrary third-party loaders,
external code or past in-memory code. It grants no whole-image security proof.

The closed security overlay has seven reviewed files: the Market Dockerfile,
Market lock, pip bootstrap lock and four trust/psycopg2/SQLite/pip-removal helpers.
It selects pinned Python 3.11.17 Trixie and ordinary authenticated vendor updates.
The only Market-lock entry changed from a6 is psycopg2-binary 2.9.9 to the pinned
2.9.13 source archive linked against system libpq. Pydantic 2.8.2, its core 2.20.1
and every other locked entry remain unchanged. The 15 separately built vendor
DEBs are not part of this first packet. Actual package versions, findings and
compatibility remain contingent on the real build and scan.

The test-only capsule contains the exact a6 scripts, fixtures and five historical
test modules, outside the runtime product image. All 85 historical tests must run
with zero skips against the same candidate: 84 under UID/GID 10001 and exactly
one unchanged host-metadata fixture under UID/GID 0 in its separate container.
Exact test IDs must cover all 85 historical IDs with no overlap, missing or
duplicate case. The image Config and native probe remain UID/GID 10001. The sole
controlled test edit replaces the
old Bookworm Dockerfile assertion with the reviewed Trixie digest and explicit
security-helper/hash checks; original/effective test hashes and Git blobs are
recorded, and the inverse edit must reproduce the original bytes. No missing
historical replay test is substituted from main. Host/network/native/process
operations are denied before application/test imports; named unit-test mocks
remain usable. Candidate product bytes are checked against all 93 expected files.

Only hard-SHA-sealed bounded command, isolated-container/owned-cleanup and normal
image-export primitives are reused from the App composition helper. App admission,
package inventories, provenance and constants are never called. Test containers
use network-none, read-only root, no capabilities, no-new-privileges, 1GiB memory,
2 CPUs, 128 PIDs, 128MiB private tmpfs and 180-second work limits. BuildKit resource
cgroup limits are not asserted; the job and build command have finite deadlines.

The raw Trivy 0.76.1 scan retains all severities and unfixed OS/library findings,
uses an empty config and no ignore file, and is bound with the actual SBOM to
the same immutable image and complete RootFS. Evidence hashes include the source
payload, public controls, test capsule, native checks and actual historical tests.
The exported image remains unaccepted and ineligible for deployment. A separate
unchanged raw HIGH/CRITICAL gate fails when either count is nonzero; export and
report retention never waive that gate. Full runtime/rehearsal/Telegram/database
integration, other role profiles and activation remain NOT_TESTED.

Actual run [40137914375](https://github.com/mohsenbarari/trading_bot/actions/runs/40137914375)
at source `2332b0f6` built image
`sha256:7f54a75e9790bbbf853312ee3a26722694038533fc1f36687a3d47a8fb6fa279`.
All 93 product-file checks and native trust/SQLite/system-libpq/Pydantic checks
passed. All 85 tests ran with zero skips; 14 failed because the blanket socket
constructor fence blocked asyncio's owned local self-pipe. One unchanged
host-maintenance test expected its synthetic files and directory to be root
owned while the test process correctly ran as UID 10001. The eight-file failure ZIP
12577627587 is 28398 bytes with SHA256
`8e203ae9c6e7002d31dbd379e9a2104f8554d68c8541b6f857530232ad4e33bc`.
Its original logs and failed outcome remain immutable.

The correction preserves the actual socket class and owned AF_UNIX socket pairs,
denies connect/connect_ex/bind/listen/accept/sendto/sendmsg and resolver helpers,
and retains network-none and every process/native/resource/signal fence. The
sequential suite runs in one owned non-main Python thread with a 150-second join
bound, so asyncio need not install main-thread SIGINT handlers. An actual
asyncio.run/self-pipe test passes under that fence; attempted SIGINT installation
and IPv4/IPv6/Unix connection operations are refused before syscalls. The single
root-owned metadata fixture receives only a separate explicit user override;
all other isolation limits, public read-only test mount and exact owned cleanup
remain identical. Its test body and root/GID permission constants are unchanged.
Both 84-test and one-test receipts, real execution UIDs and distinct ID sets are
independently bound into the aggregate proof and exported evidence hashes.

Actual rerun [40139103589](https://github.com/mohsenbarari/trading_bot/actions/runs/40139103589)
at source `4f837a85` built the image, passed native checks and all 84 UID10001
cases with zero skips. Its root fixture stopped before its one test at the
public-source inventory assertion. The preparer had created nested public COPY
directories as 0700; Docker preserved those modes while assigning UID10001.
UID0 with no DAC capability cannot traverse them, and pathlib.rglob suppressed
the permission errors. The ten-file evidence ZIP12578187734 is 35043 bytes,
SHA256 `a4eaa62a7fc26682ee6aa2955b266e6619254ecd1ee178a61e8f492c0bf73db8`.
This failed boundary remains unchanged historical evidence.

The permanent correction explicitly sets only newly created, verified public
context/test directories to 0755, independent of umask 077. The enclosing host
payload/context stays 0700, and public files retain their exact Git bytes and
0644/0755 modes. Existing private directories or symlinks are refused rather
than widened or adopted. No DAC capability, extra mount, test-body change or
runtime-user change is introduced. Both candidate test profiles verify every
expected public directory's exact 0755 mode and UID/GID10001, enumerate every
directory in the three recursive COPY scopes with scandir, and require exact
file and directory inventories. Permission failures propagate; unexpected
empty directories and unreadable trees cannot disappear from the proof.
The directory contract joins both test receipts and build/export bindings.

Before another CI run, the exact a6 root-test body and metadata-validator buffer
were reconciled with the actual rerun's sealed source hashes and executed on
real fresh temporary files. This local process was UID0/GID0 with effective,
permitted and bounding capabilities all zero and NoNewPrivs1. The unchanged
test passed with zero skips, including its0440 positive,0444 negative and final
shared-GID constant restoration. Only the exact test method, validator and
original constants were compiled; no application modules or production data
were imported/read. Its required directory/file ownership is created naturally
by UID0; chmod/lstat/write need no chown or DAC capability. This metadata-only
proof does not substitute for the complete next candidate CI run.
