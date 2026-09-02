# Section 1 Stage Ledger

Last updated: 2026-09-02 UTC
Plan branch: `plan/two-server-refactor-v1`

| Stage | Status | Gate | Primary output |
| --- | --- | --- | --- |
| `P1-00` | `IN_PROGRESS` | owner review approved; evidence and rehearsal gates open | current-state dossier and blocker register |
| `P1-01` | `PROPOSED` | policy approved; execution blocked by `P1-00` and exact batch receipts | cleanup/retention execution |
| `P1-02` | `PROPOSED` | scenario contract approved; blocked by `P1-00` and `P4-00` | topology-neutral configuration |
| `P1-03` | `PROPOSED` | scenario/ownership contract approved; dependencies not complete | target runtime topology |
| `P1-04` | `PROPOSED` | scenario/data contract approved; dependencies not complete | shared Finland data plane |
| `P1-05` | `PROPOSED` | scenario/merge contract approved; dependencies and execution authorization open | deterministic data-merge rehearsal |
| `P1-06` | `PROPOSED` | scenario/acceptance contract approved; dependencies and execution evidence open | differential staging acceptance |
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
- unique behavior-family seeds for 217 FastAPI decorators, 203 Bot handlers, 30
  Web routes and 15 registered authority jobs, plus runtime-task ownership seeds;
- focused characterization suite: 81 tests passed with the required unit-test
  environment file and no external side effects.

## Why `P1-00` is not `COMPLETE`

The owner approved the dossier, drift register, twelve-family behavior baseline,
Web/Bot contracts and runtime-ownership seed on 2026-09-02. The Stage contract
still requires more than owner approval. These technical gates remain open:

1. Every discovered route/handler/job has a unique family seed and none is
   unclassified, but 6 items need direct evidence resolution and concrete
   persona/tier/time/failure scenario records.
2. Baseline deploy/restart/backup/restore duration needs an observed rehearsal;
   production operations were intentionally outside this audit.

Until those gates close, `P1-01` may be reviewed but no cleanup is authorized and
`P1-02..P1-07` must not change application behavior.
