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

## Actual c8fe run: isolated coordinator was namespace PID1

Run40167371764 on c8fe662f completed8051 hosted tests with no failures/errors
(350 existing skips); the frontend and changed-code coverage gates also passed.
The required root lane ran359 tests with one error and no skips. Its public
artifact12589381443 is17169791bytes, SHA256
`a5c4b813f0a4c3a2523cc9b4017389709cd6fd0ece1a8c6c25d741659e7b1184`.
The job remains failed.

The sole error was the real-flock legacy coordinator fixture. Python was the
container's namespace init, PID1, while the unchanged production owner contract
correctly requires an ordinary process with PID greater than1. The workflow now
uses Docker `--init`; a separate init/reaper owns PID1 and Python remains its
ordinary child. The fixture runner also checks this prerequisite before loading
fixtures. A negative regression rejects PID1 before any fixture inspection, and
the workflow contract requires `--init`. Production PID, ownership and lock
checks are unchanged; no PID spoofing, skip or acceptance exception was added.

Root's source-only hermetic run passed60 fixture-lane/legacy tests, and the
independent reviewer passed all15 fixture-lane tests. The next actual CI run
must verify the isolated integration; these local results are not an official
CI pass or runtime security acceptance.

## Actual corrected run3641: identified CI causes closed

Run40168702739 on3641d39fd073b07626d7c97a6659d750dad6cdec completed successfully.
Artifact12589739251 is17170259bytes, SHA256
`5ba06748e55f339ec33d6bd32630ac5ba2fb687ccec93a8cba5911752315f743`.
Its actual exit files are all zero: hosted8051tests/0failures/0errors/350existing
skips; required-root360tests/0failures/0errors/0skips/UID0; frontend171files and
2037tests passed. Both changed-code coverage gates and all13 mutation targets
passed. No production PID, ownership, mode, coverage threshold or test-skip
exception was introduced. This closes the identified CI failures, not the
remaining image findings, serving activation or global security acceptance.
