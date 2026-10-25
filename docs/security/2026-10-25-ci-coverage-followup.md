# Coverage run 40157969706: remaining fixture failures

Source: `52808c923bd0717f672e6533b0bc2ebaef232f9f`. The official artifact
`12586045477` is 6,055,975 bytes, SHA-256
`717990922854beabb7a2b2a5f7fe6bc43611c540594cd4c0d37e73277f97f937`.
The job is failed. Success-colored steps use `continue-on-error`; their explicit
exit-code files and executed counts remain authoritative.

Observed hosted result: 8,036 tests, zero assertion failures, three errors,
350 existing skips. Required isolated root fixtures: 190 tests, one failure,
one error, zero skips. Frontend: 171 files and 2,037 tests passed; opt-in
PostgreSQL 55 and Redis 16 tests passed. Mutation verification passed all 13
targets. Stage 9 runtime evidence passed its selected scope, with 12 deferred
scenarios and `complete=false`; this is not a full-matrix claim.

The new source correction retains every production guard and threshold:

1. Current-controller tests that call the real root-owned public reader join
   the required isolated root fixture lane. Discovery remains disjoint and
   dynamically binds every exact test ID/count. Its existing YAML test dependency
   `Jinja2==3.1.6` is installed from the same test requirements pin. This removes the incorrect
   non-root fixture placement and its long watchdog timeout.
2. The backup CLI structural test follows `create_backup` into its actual
   `_create_backup_bound` implementation before checking the receipt predicate.
3. The hot-preflight synthetic fixture maps only its simulated `/` boundary
   onto its owned temporary filesystem. Real metadata and the same-device
   production assertion remain intact. A separate cross-device case refuses
   before CREATE, leaves all fences untouched, and publishes no attempt.
4. JSON and HTML coverage reporting use the same explicit versioned-source
   include filter as XML. Deleted synthetic copied test modules are not product
   source. There is no `ignore_errors`, coverage threshold change, or test skip.
5. The isolated entrypoint starts coverage before the runner executes. The root
   lane requires that exact active collector, never starts a nested one, and
   exports its saved bytes. `/reviewed` and `/work/source` aliases combine back
   to the same repository paths. Root boundary negative cases, actual owned
   fixture execution, identity mismatch, preexisting workspace and finite wall
   expiry are tested. This addresses the runner's actual 30 uncovered lines
   and branch; the next official run must still prove complete changed coverage.

The original repository summary's 90.4% is frontend-only because backend JSON
reporting failed on cleaned temporary source paths. Do not present it as total
coverage. Traceability failed with `backend_evidence_not_passed`, preserving the
changed-code failure instead of waiving it.

Source-focused tests and simulated public coverage path-alias checks are not
an official CI pass, a runtime activation, or global security acceptance.
Re-run the official required workflow after independent source review and
publication. Keep the original failed artifact and its sanitized audit.

## Actual follow-up81 and final ownership placement

Run40164468251 exercised source81d3d183: all221required root fixtures passed
with no skips, frontend passed, and changed-code coverage passed. The job still
failed at four hosted cases. Three nested fixture-lane orchestration tests
require actual root-owned copied sources; their complete module now joins the
required-root manifest. A regression constructs those three real TestCase
identities and proves that none enters the hosted lane. Each case also restores
its original process umask, preventing cross-suite state leakage.

The fourth failure was a distinct fixture-mode defect, confirmed by Root's
real-UID0 combined run: a new extra public file inherited mode0600 under umask077,
so the real mode guard refused before the intended inventory-drift assertion.
The fixture now explicitly uses0644, and a new regression verifies both guards
under umask022 and077. This module remains in the ordinary hosted lane; its
source-owner fixture already uses its real UID. No production ownership/mode
guard changes or UID spoofing are used.

The six new datastore/legacy modules also join the required genuine-root lane
when their sources are published. The owner-only fixtures remain isolated by
network-none, no capabilities, no host mounts/socket, read-only root and owned
public copied sources. Exact discovered IDs/counts are still required across
both lanes. Their source implementation tests cover1294/1294lines with124tests;
branch coverage is96.03%, and is not represented as100%. Real staging, image
LOAD, legacy parking and datastore activation remain separate unexecuted work.
