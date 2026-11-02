This workflow builds one fresh SQLite 3.54.1 shared-library candidate for Debian
13, Ubuntu 24.04 and Ubuntu 26.04. It does not install anything on a host, edit
an existing package lock, replace f88 rollback, activate an application or grant
security acceptance. Root reviews and dispatches the workflow from Main.

Each disposable matrix VM uses the existing pinned distro image and first tries
the signed, hash-checked 2026-10-25 repository snapshot. When that Ubuntu snapshot
is unavailable, a fresh build may use the official TLS-verified, signed Ubuntu
archive for the same distro. Actual signed indexes, installed dependency versions
and downloaded dependency hashes are recorded in the exported build inputs. The
old baseline SQLite DEB remains pinned by both version and authenticated prior
package hash; if that exact package is unavailable or differs, the build fails. The canonical upstream source ZIP is
bounded at 14,899,528 bytes and verified with the official SHA3-256. VERSION and
manifest.uuid must identify 3.54.1 and the reviewed upstream source ID. Existing
source/build producers are not repointed.

The baseline Python process records actual distro compile options and limits.
The builder preserves those feature and limit macros, explicitly generates the
UPDATE/DELETE LIMIT parser from canonical sources, keeps libsqlite3.so.0 and all
old public exports, and allows compatible new exports. Public C and session API
names are checked independently of accidental private exports in the old distro
build; the full raw export lists and removed private-symbol delta stay in evidence. Each native DEB contains
only the shared library, its SONAME symlink and documentation. The CLI is never
included. Native runtime dependencies come from dpkg-shlibdeps.

SQLite changed default REAL-to-text precision from 15 to 17 digits. There is no
compile-time FP_DIGITS default in this canonical source. A small static extension
uses upstream SQLITE_EXTRA_AUTOEXT to run the official
`sqlite3_db_config(SQLITE_DBCONFIG_FP_DIGITS, 15)` API for every new connection,
before that connection is returned. A failed API call refuses the connection.
The per-connection callback does not call global initialization or register an
automatic extension while initialization is in progress. Concurrent first opens
and repeated shutdown/reinitialization are exercised before Python probes.

The unmodified upstream `make test` suite runs in a separate default-17 build.
Its result is explicitly not claimed as a production-15 test result. Production
probes compare 2,048 deterministic CAST samples with the actual old library and
require exact quote/JSON numeric roundtrip, exercise multiple fresh Python processes, and test eight simultaneous
first connections in each of four shutdown/reinitialize cycles (32 total). They also exercise
WAL snapshots, upsert, rollback/savepoints, JSON/FTS/RTree, the lookup index and
authorizer, backup/integrity and old→new→old→new database reopen. All databases
are new synthetic files confined to the disposable builder.

Any numeric, ABI, feature, source, test or database compatibility failure blocks
the candidate. Raw bounded logs survive in the CI build log, including failures;
successful exports retain the exact upstream test output and package provenance.
No vendor expected results are rewritten or skipped by this candidate. Actual
App32, Market81, image scans and release/rollback checks remain required on the
resulting actual images; package-only tests cannot claim those outcomes.

Official references:

- https://www.sqlite.org/capi3ref.html (supported API compatibility)

- https://www.sqlite.org/download.html
- https://www.sqlite.org/releaselog/3_54_1.html
- https://www.sqlite.org/releaselog/3_53_0.html (floating-point change, item 9)
- https://www.sqlite.org/c3ref/c_dbconfig_defensive.html#sqlitedbconfigfpdigits
- https://www.sqlite.org/c3ref/auto_extension.html
- https://www.sqlite.org/formatchng.html

Numeric acceptance is scoped to actual frozen business call sites: all 2048
finite-double CAST strings remain byte-identical; quote/JSON constructors must
round-trip each finite numeric value exactly. Their per-function text hashes are
retained as observations, without claiming old quote/JSON spelling is unchanged.
The exact frozen App and ten Market role sources, Golden and known host consumers
use Python JSON serialization for events/hashes and no SQLite quote/JSON
constructors. SQLite JSON extraction predicates and all actual App32/Market81,
ABI, feature, lifecycle and database-reopen gates remain required.
