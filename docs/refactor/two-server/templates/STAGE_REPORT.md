# Stage Report — `<STAGE_ID>`

- Status: `<IN_PROGRESS|BLOCKED|FAILED|COMPLETE_CANDIDATE|FINAL_REVIEW|COMPLETE>`
- Assignment ID / execution role:
- Branch / base SHA / head SHA:
- Worktree registry ID / expiry:
- Locks / parallel peer:
- Scope requested:
- Connectivity / Writer / release / schema state:
- Standing authorization or Codex external-action receipt:

## Before

- Existing behavior and evidence:
- Dependencies verified `COMPLETE`:
- Open gaps/decisions: `NONE` or IDs

## Changes

| File/component | Why in scope | Behavior impact |
| --- | --- | --- |

## Verification

| Scenario/test | Expected | Actual | Evidence URI |
| --- | --- | --- | --- |

Include success, failure and rollback. High/Critical acceptance cannot rely only on fixtures.

## Rollback / recovery

- Trigger:
- Command or controller step:
- Data consequence:
- Verified result:

## Residual risk and final review

- New gaps/decisions:
- Deferred tests with authority/deadline:
- Recommended next state:
- Codex Final Review receipt / exact reviewed SHA:

Cursor may recommend `COMPLETE`, but only `CODEX_FINAL_REVIEWER` may approve the
exact commit and change the authoritative Stage state to `COMPLETE`.
