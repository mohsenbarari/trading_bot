# Codex Final Review — `<STAGE_ID>` / `<HEAD_SHA>`

- Reviewer authority: `CODEX_FINAL_REVIEWER`
- Assignment ID:
- Integration base SHA:
- Reviewed head SHA:
- Review date (UTC):
- Verdict: `<APPROVE|CHANGES_REQUIRED|REJECT|BLOCKED>`
- Receipt type: `<PLAN_GOVERNANCE_FINAL|STAGE_FINAL|PAIRING|BRANCH_BARRIER|EXTERNAL_ACTION>`
- Receipt expiry / one-time target, if applicable:
- Target branch/environment/action, if applicable:

## Scope and dependency verification

- Dependency receipts valid:
- Changed paths within Assignment:
- Locks and parallel peer valid:
- Unauthorized external mutation: `NONE` or finding

## Independent findings

| Severity | Finding | Evidence | Required action |
| --- | --- | --- | --- |

## Verification

| Success/failure/rollback/integration check | Result | Evidence URI |
| --- | --- | --- |

## Decision

- Accepted commit (only when `APPROVE`):
- Residual risk:
- Conditions attached to receipt:
- Next dependency-ready Stage(s):

فقط `APPROVE` روی همان integration commit دقیق اجازهٔ `COMPLETE` شدن Stage و شروع
dependency بعدی را می‌دهد. promotion به `main` receipt جداگانهٔ branch barrier
می‌خواهد. تغییر commit، target، preflight یا scope، receipt را باطل می‌کند.
