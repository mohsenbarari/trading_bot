# Psycopg2 source-built security refresh

Status: source candidate, not a production activation or completed image scan.
The import/API/DSN family stays `psycopg2`; distribution name remains
`psycopg2-binary`, pinned to2.9.13. No Psycopg3 migration or database/config
policy change is included.

## Why source compilation is necessary

Upgrading the OS does not patch the `libpq`/OpenSSL bundled inside a vendor
Psycopg binary wheel. The upstream installation guide recommends source builds
for production where system-library updates must remain effective:
<https://www.psycopg.org/docs/install.html>.

The actual official `psycopg2_binary-2.9.13.tar.gz` was independently read with
SHA/size verification:378112bytes,
`e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373`.
Its actual `setup.cfg` sets `static_libpq=0`; its setuptools backend dynamically
links `pq` from `pg_config`. It needs setuptools>=70.1; existing pinned82.0.1
satisfies that. Source metadata:
<https://pypi.org/project/psycopg2-binary/2.9.13/>.

Do not infer source provenance from the distribution name, wheel filename or
`--no-binary` alone. Pip may reuse an already-built same-version cached wheel:
<https://pip.pypa.io/en/stable/topics/caching/>.

## Finite build/cache behavior

`scripts/prepare_psycopg2_source.py` admits only that exact source archive. It
rejects **every** cached `psycopg2*.whl`, including a same-version renamed or
apparently locally built wheel. The bounded official HTTPS download has an
exact maximum file size, child file-size limit and60/65second wall bounds; no
redirects or TLS bypass. `check` cannot download or write.

Only the one Psycopg pin is removed from a derived public requirements file;
other dependencies keep the existing wheel workflow. Both generation and
prepared-cache verification include the helper in their existing freshness
signature. The hidden derived requirements file travels with the complete
existing wheel-cache rsync and is not excluded by `.dockerignore`.

Application builds use the existing pinned Python3.11.17 Bookworm base. Builder
`libpq-dev` must exactly match that patched base's `libpq5` package version.
Psycopg is compiled directly from the admitted local archive into a fresh
isolated wheel directory with no pip cache, index, dependency resolution or
build isolation. Thus a vendored wheel cannot win a same-name resolution race.
No host Python3.12 native compilation occurs.

The App Dockerfile retains the existing empty-wheel-cache online fallback:
fetch only the exact bounded Psycopg source, compile it directly, and resolve
other dependencies normally. The Iran Dockerfile remains strictly offline for
Python dependencies after the prepared wheel/source cache is supplied.

The Market lock binds the exact official sdist URL and SHA, with pip cache
disabled and explicit source selection. Compilation occurs in its matching
patched Bookworm assembly stage; its runtime receives system `libpq5` but no
compiler/development packages. Existing Market build networking for other
hash-locked dependencies is unchanged; this is not a claim that the entire
APT/Market build can run without network.

The bootstrap pin is pip26.2.2. New shared verification/bootstrap inputs are in
the Market input signature and transferred public control payload. No live
capture/Market role is activated by this source change.

## Required actual evidence before activation

Every built application/Market runtime invokes
`scripts/verify_psycopg2_runtime.py` (Market uses explicit `/opt/market-python`).
It requires distribution2.9.13, pip>=26.2.2, matching compiled/runtime libpq,
and loaded system `libpq.so.5`, `libssl.so.3`, `libcrypto.so.3`. Vendored `.libs`,
deleted/unexpected library paths, and missing dynamic families refuse.
This gate is not a replacement for an actual OS/application CVE scan.

Source/hermetic tests do not prove a successful C build. Root must still:

1. Build all intended images with the matching architecture and retain exact
   image/revision/tree identities; run actual linkage gates and `pip check`.
2. Re-run the supported-Python app/Bot/security subset using those images.
3. Exercise the upstream malformed interval/bytea/int64 regressions in a
   short-lived resource-bounded new-image process. The old driver must not be
   used for an unbounded historical infinite-loop trigger. Upstream fixes:
   <https://www.psycopg.org/docs/news.html>.
4. Run synthetic scratch PostgreSQL connection/type/adaptation and TLS smoke
   with no production DSNs/rows/sessions or changed SSL policy.
5. Run actual rebuilt-image SCA and then separately accept the finite consumer
   activation/rollback evidence. Old deployed/legacy images remain open until
   independently patched or safely retired.

No live package installation, image build, database connection, service change,
Git operation or production activation was performed by the source author.
