# Dual-Platform Registration - Combined Review Follow-Up

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Review baseline: `c8bb4a1a` (the stale commit reviewed by ChatGPT)
- Remediation chain: `1fa269ee`, `db407910`, and the final-audit commit containing this document
- Review input: `tmp/chatgpt/dual-platform-registration-stage1-stage2-combined-independent-review.md`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`
- Deployment scope: source and isolated evidence only

## Gate Status

The combined independent review targeted stale commit `c8bb4a1a`, before the accepted Stage 2
remediation at `1fa269ee` and follow-up at `db407910`. Every numbered finding was re-evaluated
against the current checkout and executable evidence. Most findings were already closed by
`1fa269ee`; `db407910` closed `C-2`, `H-6`, and `L-4`. The final audit strengthens direct evidence
for canonical identity, concurrent command outcomes, foreign write enforcement, relation
projection, counter restart replay, and Invitation credential protection.

This is not a self-approval. Stage 3 remains blocked until ChatGPT independently reviews the exact
final-audit commit and returns `GO`. No staging or production deployment, runtime migration, route
enablement, worker enablement, feature enablement, push, or production action was performed. Every
new registration flag remains disabled by default.

## Finding Disposition

| Finding | Final disposition | Implementation and direct evidence |
|---|---|---|
| `C-1` canonical identity mismatch | Closed by `1fa269ee`; evidence strengthened | Database-computed canonical mobile/account columns, unique constraints, and one canonical service path now agree. Real PostgreSQL covers Arabic digits, split mobile/account ownership, soft-deleted collisions, and ordinary canonical conflicts. |
| `C-2` partitioned post-reset counter loss | Closed by `db407910`; evidence strengthened | `user_counter_event_v2` persists producer/receiver ledgers with immutable UTC occurrence time. The Iran reset timestamp is the period boundary and rebuild source. Real PostgreSQL covers both delivery orders, stale-epoch post-boundary acceptance, pre-boundary exclusion, mixed sources, replay/UUID conflict, and a fresh-session post-restart replay. |
| `H-1` competing terminal writers | Closed by `1fa269ee` | Completion, revocation, relation cancellation, and expiry use one Invitation-first lock hierarchy. Two-session PostgreSQL races prove deterministic terminal winners. |
| `H-2` uniqueness rollback loses receipt | Closed by `1fa269ee`; evidence strengthened | Recognized canonical/Telegram uniqueness conflicts are resolved into a clean receipt-only transaction. PostgreSQL proves durable outcomes for canonical and Telegram conflicts and for two different commands racing on one Invitation. |
| `H-3` notification poisons adapter session | Closed by `1fa269ee` | Best-effort Web notification uses a dedicated session after the authoritative commit. Tests prove notification rollback cannot prevent Web session/token issuance. |
| `H-4` stale delayed-link eligibility | Closed by `1fa269ee`; policy boundary documented | Linking reloads current Iran User and Relation truth and rejects inactive/deleted/Watch/accountant/Tier-2 states. A Standard user without a Relation remains eligible by the explicitly locked product rule that ordinary/admin/police users must not wait for Relation. |
| `H-5` unsafe historical completion backfill | Already closed at reviewed baseline | Backfill requires relation, temporal, and active-User evidence; ambiguous Standard history remains terminally ambiguous. |
| `H-6` Invitation credential logging | Closed by `db407910`; final edge protection strengthened | All Nginx templates/setup paths protect the bounded `/register` family and invitation lookup/validate APIs. Runtime tests cover slash normalization, path and query tokens, `access_log off`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and raw-log scans. |
| `H-7` ID/recency-based User sync | Already closed, with `C-1` now closed | Versioned events resolve natural identity and use source watermark ordering; computed canonical columns are excluded from repair payloads. |
| `H-8` Telegram address trimming | Already closed | The exact non-trimmed field contract and canonical hash tests remain intact. |
| `H-9` scratch Alembic execution safety | Closed by `1fa269ee` | `scripts/run_guarded_scratch_alembic.py` validates mode, matching DB URLs, checkout, and allowlisted scratch names before Alembic runs. |
| `M-1` Relation structural consistency | Closed by `1fa269ee`; evidence strengthened | Shared validation checks invitation/user/relation identity, owner/creator, token, expiry, kind, role, and customer tier. The unit matrix now mutates each accountant/customer field independently. |
| `M-2` adapter and post-commit recovery | Closed by `1fa269ee` plus existing adapter tests | Real adapter tests prove session issuance and retry after a lost response without duplicate User creation. Redis invitation cleanup is post-commit best effort and has explicit failure coverage; it cannot roll back product state or poison the session transaction. |
| `M-3` receipt invariants | Already closed | Database constraints and invalid-state PostgreSQL tests remain authoritative. |
| `M-4` caller-injectable receive time | Intentionally deferred to Stage 4 | No Telegram completion route exists yet. Stage 4 must inject trusted Iran receive time in the production adapter; flags remain off. |
| `M-5` raw advisory-lock parameters | Closed by `1fa269ee` | Identity, Telegram, command UUID, and idempotency inputs are reduced to opaque deterministic lock keys. |
| `M-6` role-agnostic WebApp host classification | Already closed | Role-aware peer classification remains covered. |
| `M-7` contract-v2 role/tier omission | Already closed | One shared fail-closed role/kind/tier policy is reused. |
| `M-8` legacy writers with additive schema | Intentionally deferred hard gate to Stage 3/rollout | No writing environment may receive the schema until Stage 3 routes every canonical Invitation writer through the reservation service. This is a migration/deployment blocker, not missing Stage 2 runtime code. |
| `L-1` same-command two-session race | Closed by `1fa269ee` | Real PostgreSQL proves receipt replay under simultaneous sessions. |
| `L-2` concurrent notification outbox dedupe | Closed by `1fa269ee` | Real PostgreSQL proves one logical outbox row under concurrent completion. |
| `L-3` UTC deprecation warnings | Intentionally deferred maintenance | The changed registration path follows the approved timezone-aware UTC standard. A repository-wide legacy warning cleanup is nonblocking and outside this stage. |
| `L-4` outbox wake-up undercount | Closed by `db407910` | The guard counts inserted `change_log` rows, not dirty objects; commit publishes the exact count and durable polling remains the fallback. |

## Counter Period Contract

1. The wire protocol is `user_counter_event_v2`; v1 payloads cannot be mistaken for timestamped
   events.
2. Each local event inserts its producer ledger, mutates the User aggregate, and inserts its
   `change_log` row in one transaction. A Sync-v2 listener failure aborts the whole transaction.
3. The receiver locks the natural-identity User, persists the immutable receipt, and applies or
   rebuilds the aggregate in one transaction.
4. An Iran-only reset advances the epoch; its synchronized UTC occurrence time defines the period.
5. Pre-boundary increments remain auditable but do not enter the new period. Post-boundary
   increments remain valid even when a disconnected foreign producer used the previous epoch.
6. Arrival order does not determine the result: reset rebuild handles increment-before-reset, and
   direct application handles reset-before-increment.
7. Event UUID plus canonical source/kind/epoch/deltas/time content is replay-safe. Conflicting UUID
   reuse fails closed and a receipt cannot move to another User.
8. This follows the approved synchronized UTC server-clock standard; clock health remains an
   operational prerequisite.

## Final-Audit Verification

- Full backend regression: `2756` passed, `21` opt-in scratch tests skipped.
- Focused registration/sync/outbox/persistence suite: `143` passed.
- Real Stage 2 PostgreSQL matrix: `20` passed.
- Real sender-side counter/outbox PostgreSQL matrix: `3` passed.
- Real migration/backfill/collision PostgreSQL matrix: `3` passed.
- Real Nginx source/syntax/runtime credential scan: `3` passed.
- Python compile, Alembic single head, diff whitespace, disabled feature-default inventory, and
  package credential scans pass in the handoff evidence.
- Scratch databases were downgraded and removed. The active database remained
  `trading_bot_db|f7c8d9e0a1b2`, has zero Stage 1 User columns, zero unsynced `change_log` rows, and
  zero registration scratch databases.

## Independent Re-Review Requirement

The reviewer must assess the exact final-audit commit, not stale `c8bb4a1a` line numbers. It must
independently verify every row above, especially the distinction between a closed Stage 1/2 defect
and the explicit `M-4`/`M-8`/`L-3` later-stage boundary. It must challenge canonical equivalence,
timestamp boundaries, restart/replay behavior, transaction rollback, current eligibility, foreign
write rejection, mixed-version handling, Nginx normalization/API referrers, migration safety, and
the claimed test state space. Any remaining Critical or High correctness, identity, sync,
migration, or credential-exposure issue returns `NO-GO` and keeps Stage 3 blocked.
