# Section 1 Stage Ledger

Last updated: 2026-09-01 UTC
Plan branch: `plan/two-server-refactor-v1`

| Stage | Status | Gate | Primary output |
| --- | --- | --- | --- |
| `P1-00` | `IN_PROGRESS` | owner review pending | current-state dossier and blocker register |
| `P1-01` | `PROPOSED` | blocked by `P1-00` and cleanup approval | cleanup/retention execution |
| `P1-02` | `PROPOSED` | blocked by `P1-00` | topology-neutral configuration |
| `P1-03` | `PROPOSED` | dependencies not complete | target runtime topology |
| `P1-04` | `PROPOSED` | dependencies not complete | shared Finland data plane |
| `P1-05` | `PROPOSED` | dependencies not complete | deterministic data-merge rehearsal |
| `P1-06` | `PROPOSED` | dependencies and budgets not complete | differential staging acceptance |
| `P1-07` | `PROPOSED` | production authorization required | controlled cutover |
| `P1-08` | `PROPOSED` | cutover, Iran standby and retention required | closure/decommission |

## `P1-00` execution receipt

This execution was an observation-only architecture audit. It did not provision,
restart, deploy, change DNS, write runtime state, copy production data or delete
anything.

| Field | Value |
| --- | --- |
| Base commit at preflight | `b8333ea4` |
| Repository worktree | one canonical worktree; clean at preflight |
| Current production release | `e533d415f1fe085a251d8e6df2016fa775d86702` on both current Finland roles |
| Database migration head | `ff6c7d8e9f01` on both current application databases |
| Runtime access | both current Finland hosts and target observed read-only |
| Target SSH fingerprint | `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs` |
| External writes | none |
| Secret values collected | none |

Completed technical evidence:

- repository, branch, worktree, ignored artifact and host-side release/backup
  inventory;
- current Bot-Finland, current Web-Finland and new Finland target inventory;
- database schema/table classification and read-only shared-data parity audit;
- background-job and Telegram execution ownership review;
- Web/Bot provenance and policy-code review;
- focused characterization suite: 81 tests passed with the required unit-test
  environment file and no external side effects.

## Why `P1-00` is not `COMPLETE`

The technical dossier is ready for review, but the Stage contract deliberately
requires more than a host inventory. These gates remain open:

1. The owner has not yet approved the dossier, drift register and parity contract.
2. The 212 statically discovered API route decorators, 202 Bot handler decorators
   and frontend routes are inventoried only at aggregate/module level; every
   mutation and side effect does not yet have a reviewed `behavior_id`.
3. The immutable Offer-origin gap is unresolved. Current Offer rows use
   `home_server` as both placement/authority and an indirect proxy for Web/Bot
   origin. Co-location would make that ambiguous.
4. Numeric staging, cutover, rollback and retention thresholds are proposed but
   not approved.
5. Baseline deploy/restore duration needs an observed rehearsal; production
   operations were intentionally outside this audit.

Until those gates close, `P1-01` may be reviewed but no cleanup is authorized and
`P1-02..P1-07` must not change application behavior.
