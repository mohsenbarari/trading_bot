# Dual-Platform Registration - Combined Review Follow-Up

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Baseline: `1fa269ee` (Stage 2 review remediation)
- Review input: `tmp/chatgpt/dual-platform-registration-stage1-stage2-combined-independent-review.md`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`
- Deployment scope: source and isolated evidence only

## Gate Status

The combined independent review targeted stale commit `c8bb4a1a`, before the accepted Stage 2
remediation at `1fa269ee`. Its Stage 2 correctness findings were reconciled against current code.
Three findings remained actionable: critical counter partition loss (`C-2`), high-severity trailing
slash Invitation logging (`H-6`), and low-severity outbox wake-up undercount (`L-4`). All three are
implemented and covered below.

This is not a self-approval. Stage 3 remains blocked until ChatGPT independently reviews the exact
follow-up commit and returns `GO`. No staging or production deployment, migration, route enablement,
worker enablement, feature enablement, or push was performed. Every new registration flag remains
disabled by default.

## Finding Disposition

| Finding | Disposition | Implementation and evidence |
|---|---|---|
| `C-2` valid foreign increment is lost after an Iran reset during a partition | Fixed for the locked UTC clock contract | `user_counter_event_v2` adds a timezone-aware occurrence timestamp. Producer and receiver persist immutable event metadata locally in the same transaction. Iran reset time is the authoritative period boundary. Reset rebuilds the aggregate from all local increment receipts at or after that boundary; an increment received later is classified by that same boundary even when its producer still has the prior epoch. Real PostgreSQL covers both delivery orders, pre-boundary rejection, post-boundary stale-epoch acceptance, replay, UUID conflict, mixed Iran/foreign increments, sender persistence, reset persistence, and limit enforcement after convergence. |
| `H-6` `/register/` and normalized variants escape the exact no-log location | Fixed | Every active staging, production, recovery, root, Iran setup, and foreign setup surface uses the bounded Nginx location `^/register(?:/|$)`. Runtime tests cover exact, trailing slash, repeated slash, and encoded slash, require `Referrer-Policy: no-referrer`, and scan the access log for every raw token. Foreign source remains fail closed. |
| `L-4` one User flush emits two outbox rows but one wake-up | Fixed | The outbox guard counts each successfully inserted `change_log` row for the current flush token. Commit publishes that exact count; rollback behavior and durable polling fallback are unchanged. A unit test proves one dirty object with two logical outbox rows emits two wake-ups. |

## Counter Period Contract

1. The wire protocol is `user_counter_event_v2`; v1 payloads cannot be mistaken for events with a
   period timestamp.
2. Each local counter event inserts its producer-side ledger row, mutates the User aggregate, and
   inserts its `change_log` row in one database transaction. A Sync-v2 listener failure is re-raised
   so a simultaneous profile outbox row cannot mask counter-ledger failure.
3. The receiver resolves and locks the natural-identity User, stores the immutable receipt, and
   applies or rebuilds the aggregate inside one transaction.
4. A reset is Iran-only and advances the epoch. Its UTC occurrence time defines the new period.
5. A pre-boundary increment is retained for audit/idempotency but excluded from the current period.
   A post-boundary increment remains valid even when a disconnected foreign producer stamped the
   previous epoch.
6. If the increment arrives before the reset, the later reset reconstructs the new aggregate from
   the ledger. If reset arrives first, later qualifying increments apply normally. Arrival order
   therefore does not determine business truth.
7. Event UUID plus canonical source/kind/epoch/deltas/time content is replay-safe. Conflicting UUID
   reuse fails closed and a receipt cannot silently move to another User.
8. This solution deliberately uses the already approved project-wide synchronized UTC server-clock
   standard. NTP/clock health is an operational prerequisite because an arbitrary admin reset is a
   time boundary rather than a globally preannounced calendar period.

## Verification

- Full backend regression: `2754` passed, `19` opt-in tests skipped.
- Focused counter/sync/outbox/persistence suite: `112` passed.
- Real Stage 2 PostgreSQL matrix: `18` passed.
- Real sender-side counter/outbox PostgreSQL matrix: `3` passed.
- Real migration/backfill/collision PostgreSQL matrix: `3` passed.
- Real Nginx source/syntax/runtime credential scan: `3` passed.
- Python compile, Alembic single head, diff whitespace, feature-default inventory, and package
  credential scan pass in the handoff evidence.
- Scratch databases were downgraded and removed. The active database remained
  `trading_bot_db|f7c8d9e0a1b2`, has zero Stage 1 User columns, zero unsynced `change_log` rows, and
  zero registration scratch databases.

## Independent Re-Review Requirement

The reviewer must assess the exact current commit, not the stale `c8bb4a1a` line numbers. It must
verify that all confirmed findings in the old combined report are either closed by `1fa269ee` or
this follow-up, and independently challenge timestamp-boundary behavior, transaction rollback,
mixed-version rejection, Nginx normalization, migration safety, and the claimed test state space.
Any remaining Critical or High correctness, identity, sync, migration, or credential-exposure issue
returns `NO-GO` and keeps Stage 3 blocked.
