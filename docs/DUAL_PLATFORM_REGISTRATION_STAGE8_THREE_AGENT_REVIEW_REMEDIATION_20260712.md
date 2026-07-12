# Stage 8 Three-Agent Review Remediation

Date: 2026-07-12

## Reviewed Inputs

The following independent reports reviewed parent commit `71a48083`:

- `tmp/gemini/dual-platform-registration-full-branch-independent-review-71a48083.md`
- `tmp/claude/dual-platform-registration-full-branch-independent-review-71a48083.md`
- `tmp/chatgpt/7-dual-platform-registration-stage8-final-remediation-independent-review.md`

Their conclusions were not accepted mechanically. Every material finding below was checked against
the final source, surrounding callers, tests, and the stated roadmap contract.

## Reviewer Assessment

### Gemini

Gemini returned source GO and Stage 9 GO, with merge NO-GO. Its merge-conflict observation is valid,
but its branch-wide assurance is not sufficient evidence: the report reduces most complex surfaces
to one-line conclusions and incorrectly describes source-line volume as thousands of new PostgreSQL
tests. The changed-file ledger proves enumeration, not deep behavioral review. Gemini's blanket GO is
therefore corroborating input only.

### Claude

Claude returned source GO and Stage 9 GO, with merge NO-GO. It correctly identified the semantic and
textual conflict with current main's WebApp-link hotfix and a defense-in-depth `/metrics` hydration
gap. It also correctly retained real provider/Loki/two-server/secret-rotation work as later gates.
However, it did not reproduce corruption of the audit trail's final line, so its Stage 8 GO was not
sufficient after the ChatGPT reproduction was independently confirmed.

### ChatGPT

ChatGPT returned Stage 6/7 GO, Stage 8 NO-GO, and Stage 9 NO-GO. Its audit-tail and Redis-outage
findings were reproduced and accepted. Its alert-timing observation was accepted in modified form;
the age threshold is not itself a guarantee of zero sampling/evaluation delay. Its evidence-package
finding is an evidence gate, not a product source defect, but the next package must close it.

## Finding Dispositions

### Accepted and fixed: audit partial-tail durability

The previous sink could parse-fail the last line, silently use `previous_hash=None`, append directly
after corrupt bytes, and return `audit_durable=true`. This was reproduced against `71a48083`.

The sink now:

- requires a newline-terminated final record;
- validates final JSON shape, event-hash format, and event-hash content;
- refuses to append after partial, invalid, or tampered tails;
- performs complete low-level writes and file `fsync`;
- synchronizes the parent directory on first-file creation;
- rolls back newly appended bytes after write/fsync failure where the filesystem permits;
- returns `audit_trail_integrity_failed` or `audit_trail_write_failed` without a false durable claim;
- retains the existing in-process lock and cross-process `flock` owner.

Automatic truncation/quarantine was deliberately rejected because it can hide tampering. Existing
corruption is preserved for incident handling and durable appends stop until operators restore or
rotate the artifact.

### Accepted and fixed: Redis outage false-green health

Redis failure previously produced empty job dictionaries, which flattened both heartbeat-unhealthy
fields to zero. Health now preserves server role and feature expectation. An enabled job on its
expected server becomes `unavailable`, emits heartbeat-unhealthy `1`, and suppresses healthy pending
age. Disabled and not-expected jobs remain non-alerting. A bounded
`registration_observability_unavailable` field records the shared-state outage without identity.

### Accepted and fixed: metrics hydration defense

The `/metrics` endpoint now contains any ordinary exception from optional registration snapshot
hydration, not only timeout. Core metrics remain available while hydration fails. Cancellation and
process-level exceptions remain unaffected.

### Accepted with modified remediation: alert timing

The locked values are health/lag thresholds: heartbeat age 60 seconds, registration pending age 300
seconds under healthy connectivity, and OTP fallback lag two seconds. They are not a claim that the
external sampler and Grafana have zero evaluation delay.

All four Stage 8 rules now have `for: 0s`, removing the previous additional one-minute hold and
firing on the first evaluated unhealthy sample. The one-minute sampler and Grafana/Loki evaluation
cadence remain transport delay. Real healthy-to-stale, outage, no-data, recovery, and engine-version
timing must be measured in Stage 10. This operational evidence is not falsely claimed by source tests.

### Accepted as evidence remediation

The previous ZIP had an external checksum but no internal content manifest, omitted sanitized raw
full-suite output, and did not include independently verifiable Git objects. The replacement package
must include an internal SHA-256 manifest, external ZIP checksum, exact Git bundle, changed source,
patch, raw-log redactions, complete test summary, and explicit skip inventory.

### Accepted operational gates, not current source defects

- A repository-owned OTP state-secret drain/traffic control is still required before rotation or
  production rollout.
- Real Loki/Grafana temporal evaluation remains Stage 10.
- Real Redis/PostgreSQL restart and two-server/provider acceptance remain Stage 10.
- Passive natural OTP expiry remains an explicitly approved unobservable terminal transition unless
  a separate race-safe design is approved.
- Process-local event counters remain diagnostic samples and must not be interpreted as global totals.

### Valid merge blocker, deliberately not changed here

Current `main` contains a newer WebApp-link hotfix and has a real conflict with
`bot/handlers/link_account.py` plus its tests. Choosing flags-off URL behavior is an owner-visible
merge decision. This remediation does not merge main or silently change that policy. Merge readiness
remains NO-GO until the conflict is resolved and both suites pass.

### Rejected as current blockers

- POSIX `fcntl` portability is not a defect for the supported Linux deployment; support boundaries
  and corruption semantics are now documented.
- Automatic audit-tail truncation or quarantine is not required and would weaken evidence safety.
- Gemini's statement that no test boundary is missing is not accepted; integration skips and Stage 10
  gates remain explicit.
- Full market pressure, saturation, and soak testing is not reintroduced. The roadmap requires the
  controlled correctness matrix and bounded semantic races, with pressure testing outside this gate.

## Verification Performed

- Focused final Stage 8/audit/health/metrics/config suite: 54 tests passed.
- Wider Stage 4/6/8/auth/health/audit suite: 123 tests passed.
- Full backend discovery: 2,956 tests ran in 86.242 seconds; 63 were skipped; three sandbox-only
  smoke assertions failed because Docker/Nginx access was denied. The two affected test methods were
  rerun outside the sandbox and both passed.
- Full frontend unit suite: 128 files and 1,119 tests passed in 272.28 seconds.
- Frontend production/PWA build passed.

The 63 backend skips are not counted as passes. Real scratch PostgreSQL/Redis, provider, two-server,
Loki/Grafana, rotation, deployment, and production evidence is not claimed.

## Gate Status

This remediation performs no deploy, migration, feature enablement, provider call, secret rotation,
push, merge, or production action. Stage 9 remains NO-GO until an independent reviewer evaluates the
exact remediation commit and returns GO. Merge readiness remains separately NO-GO pending resolution
of the current-main WebApp-link conflict.
