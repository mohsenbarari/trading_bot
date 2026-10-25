# App consumer recovery corrections

This is source and regression evidence, not an activation receipt. Global security
acceptance remains false; refactor automation remains paused and its consecutive
healthy-completion streak has not started.

## Observed failed attempt

The second finite window used published main
`9c2290b686526ae0dde5ae23faf1737be10582eb`, nonce
`a7de00ed3579b5e4faea4c8a4799b973`, window SHA256
`6e95ff4fc143534cea0ed302332368df65a3f7baed0441594eef0aec9ccd95dd`.
Only Web App was attempted. Both its activation and rollback intents recorded
`write_completed`; the candidate failed the existing public-code COW gate. Its
subsequent rollback recreated the original image as container
`56e7f325a76386ca7aed5da936f80c63c7e2f2bffb02ee142d5e892a5e0f8ae0`.
The original failure result and conservative coordinator journal remain intact.

Read-only investigation found the original image running and its API returning
200. Full canonical configuration/inventory recovery comparison passed with
only the recorded Web App replacement projected. The next recovery check failed
because the old image's default TLS trust store is empty; all five required
library imports succeed. An empty legacy trust store is a known security defect,
not evidence that the restored old service is a newly remediated candidate.

The removed candidate's COW details were not retained by the original controller.
Its precise cause remains unproven. No mount ancestor, code, library or cache
exemption is granted. Retained fences require a separately reviewed, complete
read-only reconciliation before any exact owned cleanup or another operation.

## Corrections

1. A candidate still requires nonempty default TLS trust and all five imports.
   Rollback requires the same imports and records its actual trust-store boolean
   without demanding the new candidate's security fix from the old image.
2. Before the COW gate or automatic rollback, persist a durable bounded receipt
   containing only sealed public names and aggregate categories. Unknown and data
   names are withheld. Raw diff bytes and private-name hashes are never saved.
   Non-UTF8 names remain rejectable while allowing bounded withheld diagnostics.
3. Preserve primary activation and secondary rollback failures separately.
   Unknown or failed recovery remains unaccepted; no status is made green.

## Verification

Root reviewed the complete four-file change and ran the five-module consumer
suite with ambient command/network/native/signal/resource effects denied before
imports, umask077 and warnings treated as errors: **123 passed**, zero skips.
An independent reviewer read the caller/error/write contexts and independently
ran the same123 tests: **123 passed**, zero skips. Tests cover empty legacy trust,
strict candidate trust, all five required imports, durable pre-rollback COW
evidence, privacy/non-UTF8 handling, unchanged COW refusal and both failure causes.

Separately, seven additional maintenance test modules now close their import
guards at import completion and reinstall them per test with cleanup registered
before setup. Their exact original denial inventories are preserved. Package
fixture import, genuine inode-replacement setup, and today's public unit-recipe
fixtures were corrected without changing production authority pins or UID guards.
The expanded suite exercises restoration after import/setup failures and audits
all test sources for unscoped patch installation: Root **319 passed**, zero skips.
These are test-isolation results, not full API/Bot/browser or runtime acceptance.

Future activation requires an explicit verified reconciled baseline and exact
retained rollback override provenance. This source correction alone authorizes
neither fence deletion, retry, altered historical evidence nor global acceptance.
