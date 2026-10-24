# E46 historical Market security candidate

This is a source-only, default-off `market_security_payload` lane in the existing
pre-release workflow. No candidate has been built, scanned, activated or deployed
by this packet. Other Market profiles, including Capture2/F79, refuse admission.
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
with zero skips in the candidate. The sole controlled test edit replaces the
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
