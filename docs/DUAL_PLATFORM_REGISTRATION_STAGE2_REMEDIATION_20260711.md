# Dual-Platform Registration - Stage 2 Review Remediation

> Later review note: canonical whitespace equivalence and counter reset/error-receipt semantics were
> reopened after this record. See
> `docs/DUAL_PLATFORM_REGISTRATION_DUAL_AGENT_FINAL_REVIEW_REMEDIATION_20260711.md` for the current
> contract and gate.

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Stage 2 implementation: `bc2d4fcc`
- Stage 1 remediation baseline: `c8bb4a1a`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`
- Review input: `tmp/chatgpt/dual-platform-registration-stage2-independent-review.md`

## Status

The Stage 2 review was produced before the Stage 1 remediation at `c8bb4a1a`. Findings already
closed by that remediation were not reimplemented. The remaining accepted Stage 2 correctness,
locking, transaction, privacy, migration-safety, and test findings are remediated in this change.

This is not a self-approval of Stage 2. Stage 3 remains blocked until an independent reviewer
evaluates the combined Stage 1 and Stage 2 remediation and returns `GO`. All registration, direct
Telegram, reconciliation, synchronized OTP, and registration-sync v2 feature flags remain off. No
staging or production deployment, runtime migration, route enablement, or worker enablement was
performed.

## Finding Disposition

| Finding | Disposition | Remediation evidence |
|---|---|---|
| `C-1` canonical identity mismatch | Fixed | PostgreSQL-generated canonical User columns and unique indexes now use the same digit/case/trim equivalence as reservation and locks. User lookup, sync natural identity, migration audit, backfill, and unique-conflict mapping use that contract. Real PostgreSQL tests cover Persian digits, case/space variants, lookup, and database uniqueness. |
| `H-1` competing terminal writers | Fixed | One Invitation-first row/advisory lock hierarchy is shared by registration, direct revoke, relation cancellation, and expiry sweeps. Barrier tests prove both winners without mixed state or deadlock. |
| `H-2` lost receipt after User uniqueness failure | Fixed | After rollback, Telegram completion reacquires command and identity locks, rereads Iran truth, and commits one receipt-only terminal result without product mutation. Mobile, account, and Telegram unique races cover first result, stable replay, changed replay, one receipt, and no residual mutation. |
| `H-3` notification session poisoning | Fixed | Web fanout receives immutable User primitives and owns a dedicated session. Failure rolls back only that session. Redis proof cleanup now occurs only after session/token creation and is best effort. |
| `H-4` stale completed-User eligibility | Fixed | Delayed Telegram linking locks current active accountant/customer projections and applies the shared pure policy to current User state. Watch, accountant, Tier 2, inactive, deleted, conflicting, and incomplete states fail closed. |
| `H-9` unsafe scratch Alembic use | Fixed for roadmap scratch execution | `scripts/run_guarded_scratch_alembic.py` requires explicit scratch mode, matching URLs, an allowlisted scratch database name, connected-database proof, exact checkout, one expected Alembic head, and bounded commands. It refuses runtime database names and emits no credentials. Existing release migration is a separate workflow and was not changed. |
| `M-1` incomplete Relation contract | Fixed | Accountant/customer validation now checks kind, role, token, owner, creator, expiry, identity where represented, tier, lifecycle, and cross-kind contamination. Corrupt-row tests fail closed. |
| `M-2` real adapter/session boundary | Fixed | PostgreSQL integration now exercises the real Web adapter and authoritative service through notification transaction failure, real session creation, post-commit session failure, exact retry, proof retention, one User, and one final session. |
| `M-5` raw advisory parameters | Fixed | Invitation token, account, mobile, Telegram ID, command UUID, and idempotency lock inputs are SHA-256-derived before binding. Tests assert raw values are absent. |
| `L-1` same-command live race | Fixed | Two live PostgreSQL sessions execute one command behind barriers; exactly one terminal receipt and one logical outbox event remain. |
| `L-2` concurrent outbox dedupe | Fixed for Stage 2 transaction | The same live command race proves one transactional registration outbox event and replay bypasses enqueue. Existing unique outbox constraints remain reused; no second outbox or worker was added. |

The Stage 1 remediation at `c8bb4a1a` closed `H-5`, `H-6`, `H-7`, `H-8`, `M-3`, `M-6`, and `M-7`.
The later combined review correctly reopened counter finding `C-2`; the current follow-up replaces
the arrival/epoch-only rule with a persisted UTC-period ledger and verifies both partition delivery
orders. It also closes the newly reported bounded `/register/` logging gap and exact outbox wake-up
count issue. This document must be read together with the follow-up evidence package.

## Intentionally Deferred Findings

- `M-4`: trusted Iran receive-time ownership belongs to the disabled Stage 4 Telegram adapter. A
  production route still does not exist.
- `M-8`: every legacy Invitation writer must move to the canonical reservation path in Stage 3.
  This remains a hard migration/deployment gate, not a Stage 2 rewrite.
- `L-3`: the project-wide `datetime.utcnow()` cleanup remains outside this narrow transaction
  remediation. Existing timezone compatibility behavior is unchanged.

## Canonical Identity Contract

`users.normalized_account_name` and `users.normalized_mobile_number` are stored generated columns.
They trim surrounding whitespace, translate Persian and Arabic digits to ASCII, and lowercase the
account name. Unique indexes enforce the same equivalence that the reservation and advisory-lock
services use. Migration aborts transactionally with only a collision count if existing Users are
ambiguous; it never selects a historical winner or logs identity values.

The columns are derived database state. Registration sync payloads continue to carry the source
identity fields, while sync repair and field policy explicitly omit the generated columns so no
receiver attempts to insert or update them.

## Transaction And Lock Order

All pending Invitation terminal transitions now use this order:

1. resolve enough immutable identity to find the Invitation;
2. lock the Invitation row;
3. acquire sorted, opaque Invitation/identity/Telegram advisory locks;
4. lock and revalidate matching User rows;
5. lock accountant and customer Relation rows in deterministic order;
6. mutate and commit one complete terminal state, or fail without partial state.

Cancellation and expiry candidates are preselected in deterministic order, but every candidate is
reloaded and revalidated after the shared Invitation lock. Registration never waits on a Relation
while another terminal writer holds that Relation and waits on the Invitation.

## Adapter Recovery Contract

The authoritative registration transaction commits before best-effort Web notifications and Web
session issuance. Notification fanout cannot access or roll back the adapter session. If session
creation fails after registration commit, the OTP/registration proof remains available. An exact
Web retry reuses the completed Invitation/User only when the completion surface and address match,
then creates the missing session. Changed-address or non-Web reuse still fails closed.

## Verification

Final evidence is captured in the ignored ChatGPT handoff package. The required gates are:

- complete backend regression, including Dockerfile and Nginx smoke with required host access;
- focused registration, Relation, auth adapter, policy, sync-repair, and migration-guard tests;
- real Stage 2 PostgreSQL race/receipt/outbox/adapter matrix on a freshly migrated scratch database;
- real migration upgrade/backfill/collision matrix on a fresh `stage1_migration_*` database;
- real counter/outbox matrix on a fresh `stage1_counter_*` database;
- `compileall`, `git diff --check`, feature/route/worker inventory, and credential scan;
- active database revision/schema proof before and after scratch cleanup.

Final local results on 2026-07-11:

- complete backend: `2752` tests passed, with `19` opt-in tests skipped in that no-scratch run;
- focused affected-domain suite: `164` passed;
- real Stage 2 PostgreSQL module: `18` passed;
- real Stage 1 migration/backfill/collision module: `3` passed;
- real counter/outbox PostgreSQL module: `3` passed;
- static checks: `compileall`, `git diff --check`, single Alembic head, flags-off inventory, and
  no enabled registration route/worker symbol all passed;
- active database before and after: `trading_bot_db|f7c8d9e0a1b2`, zero canonical Stage 1 User
  columns; after cleanup, `registration_scratch_count|0`.

## Review Gate

The reviewer must verify the current combined code, not stale line numbers from `bc2d4fcc`. Any
remaining Critical/High correctness, identity-authority, transaction, or migration-safety defect is
`NO-GO`. Deferred `M-4`, `M-8`, and `L-3` are acceptable only while their documented stage and
deployment gates remain enforced.
