# SQLite vendor-base candidate — not activated

The current Bookworm image reports `libsqlite3-0=3.40.1-2+deb12u2`.
Debian identifies it as affected by CVE-2025-7458, CVE-2026-11822 and
CVE-2026-11824. The supported Trixie package `3.46.1-7+deb13u2` fixes those
three findings. This packet changes the container OS base, **not Python's
3.11.17 version or application/Market behavior**. It does not mix Trixie
packages into Bookworm or modify a host OS.

Primary references:

- [Debian CVE-2025-7458](https://security-tracker.debian.org/tracker/CVE-2025-7458)
- [Debian CVE-2026-11822](https://security-tracker.debian.org/tracker/CVE-2026-11822)
- [Debian CVE-2026-11824](https://security-tracker.debian.org/tracker/CVE-2026-11824)
- [Official Python recipe at the exact source revision](https://raw.githubusercontent.com/docker-library/python/cede844ace77284e32c03b61ebc35cdfc945e862/3.11/slim-trixie/Dockerfile)

## Verified public identity, no layer pull

On 24 October 2026, Docker Hub's public tag API and independently fetched
registry manifest/config bytes agreed on `library/python:3.11.17-slim-trixie`:

- Index: `sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377`.
- Pinned Linux/amd64 manifest: `sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3`.
- Config: `sha256:b4403f3b7acf5e0700a5dd4e7ab01456d62b557a208a04f8a42d3470d93b5730`.

Each raw manifest/config SHA was verified before interpreting it. The config
declares Python 3.11.17, Linux/amd64. The manifest annotations bind the official
Docker-library source revision `cede844ace77284e32c03b61ebc35cdfc945e862`,
`3.11/slim-trixie`, and `debian:trixie-slim`. No image layer was downloaded,
built or activated for this metadata verification. This pin is intentionally
amd64, matching the current audited production build platform.

## Candidate source and safety boundaries

All three App/Iran/Market recipes share this exact base and continue authenticated
APT security refresh. The App dependency changes from `libmagic1` to Trixie's
`libmagic1t64`; its shared-library SONAME remains subject to the actual runtime
gate. Both assembly/runtime stages share one base. Psycopg2 remains source-built
against the exact installed `libpq-dev`/`libpq5` pair, with the existing dynamic
libpq/OpenSSL, compiler-exclusion and trust-store gates unchanged.

The normal release wheel-cache verifier and image signature use the new base.
The new SQLite gate is included in Market's signed image-input inventory and
transferred source payload. The finite security App overlay must additionally
include this one helper; it must not activate unrelated main/Market source.

`scripts/verify_sqlite_runtime.py` fails closed unless the actual runtime is
Python 3.11.17 on Debian 13/Trixie, the Debian package meets the patched revision,
the loaded SQLite upstream version agrees with that package, and its mapped
library is the system library. App builds additionally exercise Python-magic
and require its mapped system library. This does not connect to PostgreSQL,
Telegram or any live application.

The probe creates only a fresh private temporary directory and synthetic data.
It checks transaction rollback, UPSERT, integer prices, deterministic ordered
queries and index use, JSON, Persian FTS5, pinned WAL reader snapshots, refreshed
reads, actual backup integrity, readonly write refusal, and reopening both
original and backup. A 20-second CLI deadline bounds the probe. The query-result
digest is synthetic data, **not a raw schema-dump hash**. The query-plan detail is
retained for old/new candidate comparison; exact planner wording is not claimed
to be stable between SQLite versions.

## Acceptance still required

Local synthetic compatibility/source-contract tests validate the probe, not the
candidate image's actual SQLite package, ABI or CVE status. Before acceptance:

1. Build the exact sealed security payload through the reviewed main recipe;
   require the new actual runtime, CA, psycopg2 and libmagic gates.
2. Run the complete existing Market/parser/estimator and App security regression
   suites against that payload. Compare real synthetic baseline/candidate query
   results and query-plan behavior; prove no production data or model drift.
3. Verify actual dynamic library linkage, database/TLS connectivity and actual
   image SBOM/scans. Do not reuse the old base's scan or assert a clean image
   merely because these three Debian entries are fixed.
4. Bind authentic evidence to the candidate image ID/payload tree and retain
   original images, mounts, configuration and tested restorable backups before
   any separately guarded activation. Source publication is not activation.

The Debian tracker also lists other SQLite findings, including Session-extension
CVE-2026-50812/50813, with separate dispositions. This packet grants **no VEX,
ignore rule, waiver or all-SQLite-CVE-free claim** for those or any other finding.
