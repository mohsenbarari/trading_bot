# Stage 9 Automated Test Matrix

Date: 2026-07-12

## Scope

This stage implements and proves the automated-test infrastructure required by
`docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`.
It does not deploy either server, run a provider acceptance flow, enable a production feature,
merge `main`, or authorize production release.

The independent Stage 9 review was adjudicated against the source and reproduced locally. Its
false-pass findings were accepted; no finding was accepted or rejected only because an agent said
so.

## Remediation Summary

### Controlled market matrix

- Every Stage 9 market dimension is bound to a concrete scenario and exact observed test ID.
- Every named market special case has its own exact test result.
- Missing dimensions, missing tests, skips, zero discovered tests, count mismatches, unbounded
  contender profiles, and pressure/soak profiles fail closed.
- Registration and market lanes run concurrently only after process-local isolation preflight.
- The lanes forbid external mutable service I/O and receive distinct poison database/Redis URLs,
  namespaces, fixture prefixes, ports, artifact directories, temp directories, and cleanup owners.
- `MKT-004`, `MKT-009`, and `MKT-010` retain explicit Stage 10 real-topology blockers. They are not
  represented as completed by broad local module success.

### Runtime traceability

- Every automated registry ID has an exact binding to a test ID, integration source, matrix row, or
  explicit later-stage blocker.
- Final runtime-evidence construction rejected `MIG-002` because its PostgreSQL classification had
  only static/guard test bindings. A real scratch-PostgreSQL test now proves deterministic
  `base -> head -> head -> base -> head` migration behavior and exact schema restoration, and the
  registry binding requires that observed integration result.
- Test references are validated to the exact module, class, and method by AST.
- Runtime evidence requires exact commit equality across matrix, backend coverage, frontend
  coverage, and mutation evidence.
- Legal and illegal transitions require exact observed tests and expected outcomes.
- Static configuration validation cannot generate maintained runtime evidence.

### Changed-code coverage

- Backend changed-code coverage uses the branch point from `main`, the repository's coverage parser,
  and the repository's existing coverage exclusions.
- A clean final-evidence coverage run rejected previously accumulated coverage for the runtime
  evidence builder and diff checker. Dedicated failure-path, direct-script fallback, CLI, and
  non-overlapping frontend-function tests now reproduce 100% statement and branch coverage for
  both tools without relying on historical coverage data.
- Changed shell files are not parsed as Python, while every changed Python script remains in scope.
- Missing coverage files, missing executable-line mappings, uncovered lines, and partially covered
  branches fail closed, including a zero-denominator case.
- Frontend V8 statement, branch, function, and line mappings are checked against changed source.
- Only explicitly listed type-only, blank, template-only, and CSS lines may lack a V8 executable
  mapping; stale exclusions fail.

### Mutation, property, and malformed-input checks

- The mutation manifest covers thirteen reviewed critical-invariant classes instead of the prior
  five-function sample.
- Every generated critical mutant must be killed or match one exact, reviewed equivalent-mutant
  name with a concrete reason.
- A real Hypothesis `RuleBasedStateMachine` exercises durable registration-intent claim, retry,
  finalize, and replay behavior.
- Bounded malformed-field and unknown-field generation proves strict registration command
  rejection, and a shrink test proves a reproducible minimal failing fixture.

### Disposable integration runners

- PostgreSQL and Redis use uniquely named one-off containers and anonymous data volumes.
- Runtime named volumes are rejected after effective mount inspection.
- PostgreSQL creates only allowlisted scratch database names under explicit test/CI opt-in and
  restores signal handlers and active-database state.
- Coverage mode instruments the PostgreSQL orchestrator, guarded Alembic process, integration tests,
  Redis tests, and child processes into mounted temporary coverage files.
- SIGTERM interruption was exercised during migration; the exact PostgreSQL container, child
  container, and recorded anonymous volume were removed.

## Verified Worktree Results Before Final Commit

- Stage 9 infrastructure gate: 147 tests passed; isolation preflight passed.
- Parallel Stage 9 matrix:
  - registration lane: 392 declared and 392 observed tests, zero skips;
  - market lane: 483 declared and 483 observed tests, zero skips;
  - seven market special cases passed;
  - `MKT-004`, `MKT-009`, and `MKT-010` remained explicitly deferred to Stage 10.
- Backend changed-code gate: 6,699 of 6,699 executable changed lines mapped and covered; zero
  uncovered lines, zero uncovered branches, and zero missing mappings.
- Frontend changed-code gate: passed for statements, branches, functions, and lines with only the
  explicit non-executable mapping inventory.
- Mutation intermediate gate: 547 generated mutants, 521 evaluated/killed, 26 exact documented
  equivalents, and zero survivors.
- Disposable PostgreSQL suite: all migration and Stage 1-4 integration groups passed.
- Disposable Redis suite: 16 tests passed with an anonymous data volume.
- Python compilation and shell syntax checks passed.

These are pre-commit measurements used to stabilize the implementation. The review ZIP contains a
fresh rerun bound to the final immutable commit, raw logs, generated JSON, a per-file SHA-256
manifest, a complete Git bundle, skip inventory, and the ZIP SHA-256.

## Gate Status

Stage 9 can be accepted only from the final commit-bound evidence package. Stage 10 remains required
for the real two-server transport/race/coexistence rows, real browser timing, deployment/log checks,
final parity, and real cleanup. No Stage 10 result is claimed here.
