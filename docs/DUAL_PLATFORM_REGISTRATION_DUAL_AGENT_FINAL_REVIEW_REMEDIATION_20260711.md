# Dual-Platform Registration - Dual-Agent Final Review Remediation

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Reviewed baseline: `8181d9f6578b41215647ea57542766d3527cef81`
- Claude report: `tmp/claude/dual-platform-registration-stage1-stage2-final-independent-review.md`
- ChatGPT report: `tmp/chatgpt/dual-platform-registration-final-independent-review.md`
- Runtime scope: source and isolated scratch evidence only

## Gate Decision

Claude returned conditional `GO`; ChatGPT returned `NO-GO` with one Critical identity defect and
one High counter defect. The stricter verdict controls the gate. Stage 3 remains blocked until an
independent reviewer assesses the exact remediation commit and returns `GO`.

No deploy, push, active-database migration, feature enablement, worker enablement, or production
action is authorized by this remediation.

## Reproduced Findings

### Canonical identity whitespace mismatch - accepted

Python used `str.strip()` while PostgreSQL generated columns and migration collision audit used
one-argument `btrim()`. A read-only PostgreSQL reproduction proved tabs and NBSP survived SQL trim
while Python removed them. The current public Web Invitation model accepts an unrestricted account
string, so this was a reachable identity-foundation defect rather than a theoretical observation.

The fix introduces immutable `registration_identity_v1` semantics in
`core/registration_identity.py`:

- one explicit edge-whitespace set shared by Python and generated SQL;
- Persian/Arabic digit folding shared by both implementations;
- deterministic ASCII-only case folding, avoiding Python/PostgreSQL locale drift;
- the same SQL expressions in User generated columns, migration collision audit, and historical
  Invitation/Relation backfill;
- the same Python functions in reservations, locks, authoritative lookup/post-filtering, and sync
  target resolution.

Migration and service PostgreSQL tests cover tabs, CR/LF, NBSP, multiple Unicode spaces,
Persian/Arabic digits, ASCII case, non-ASCII case preservation, split-field collisions, and
soft-deleted Users.

### Counter reset/receipt defect - accepted

The receiver inserted an exactly-once receipt before validating reset progression or required
boundary state. An item-level error could therefore commit a receipt without applying the event;
the retry then returned `ignored`. Reset epochs could jump, same-epoch distinct resets could be
neutralized, and a backward boundary could rebuild a new period from old increments.

The corrected v2 contract is:

1. An exact event-ID replay is checked before new validation and remains idempotent.
2. A nonterminal `error` or `deferred` result writes no receipt.
3. Reset is Iran-only, exactly `current_epoch + 1`, and strictly later than the current reset
   boundary. Epoch jumps defer without a receipt; contradictory/backward/equal resets fail without
   a receipt.
4. Producer-side reset ledger insertion independently enforces sequential epoch history and a
   strictly increasing boundary in the same User transaction.
5. A partial unique index permits only one reset receipt per User/epoch.
6. Increment-at-boundary equality is explicitly included in the new period; a strictly earlier
   increment is durably recorded as `excluded_pre_boundary`.
7. Receipt insert and aggregate mutation run in one savepoint. Applied/excluded outcome is explicit.
8. Strict JSON integer types, epoch/delta bounds, aggregate bounds, unknown-field rejection, and a
   five-minute future-clock bound protect the signed counter payload.
9. Multi-reset, non-monotonic, equal-boundary, epoch-jump, same-epoch, missing-boundary repair,
   restart/replay, overflow, and final-limit cases have direct tests.

## Other Review Findings

| Finding | Decision and owner |
|---|---|
| ChatGPT `M-1`, response lost after successful Web session commit | Accepted as an open Stage 2 rollout defect. A secure idempotent session-recovery contract must be implemented and tested before any migrated Web completion route is deployed. Keeping OTP proof indefinitely or allowing an Invitation token to mint sessions is forbidden. It does not block isolated Stage 3 source work after re-review. |
| ChatGPT `M-2`, coercible/unbounded counter payload | Fixed in this remediation with strict types, metadata/delta/aggregate bounds, unknown-field rejection, and tests. |
| ChatGPT `M-3` / Claude `M-A`, existing foreign Iran-owned User writers | Accepted. Account-link, Telegram-link-token, bot-admin restriction, limit/unlimit, and reset callers must be inventoried and routed to Iran or removed before `REGISTRATION_SYNC_V2_ENABLED=true` on foreign. This is a hard Stage 4/5 implementation and Stage 10 enablement gate. |
| ChatGPT `M-4`, ledger retention and clock health | Accepted later operational gate. Stage 9/10/12 must define growth/query-plan evidence, retention preserving audit/rebuild, NTP offset threshold, alert, and soak evidence. |
| Claude `M-B`, deletion invalidation outside terminal lock hierarchy | Accepted Stage 3 writer work. `user_deletion_service` must use the shared Invitation lock, soft revocation semantics, and transactional reservation release, with a deletion-versus-completion race test. |
| Claude `L-A`, error receipt persistence | Fixed with the High counter remediation. |
| Claude `L-B` / ChatGPT identity property gap | Fixed for the named corpus and real PostgreSQL differential contract; generalized property infrastructure remains correctly owned by Stage 9. |
| Claude `L-C`, raw legacy Invitation prechecks | Confirmed as `M-8` Stage 3 canonical-writer work; schema deployment remains forbidden until replaced. |
| Legacy UTC warnings | Still nonblocking maintenance. Changed identity/counter timestamps follow the current project standard. |

## Evidence

- Full backend regression: `2760` tests pass; `24` opt-in scratch tests are skipped in the ordinary
  run and exercised by their dedicated PostgreSQL matrices.
- Focused registration/identity/sync/counter suite: `153` tests pass.
- Real migration matrix: `4` tests pass, including Unicode differential generated-column proof and
  collision rollback.
- Real Stage 2 PostgreSQL matrix: `22` tests pass, including whitespace identity, multi-reset,
  no-receipt-on-error, and repair/retry behavior.
- Real sender counter/outbox PostgreSQL matrix: `3` tests pass, including two sequential local reset
  events and foreign write rejection.
- Focused unit suites cover strict payload types/bounds, generated metadata, normalization, sync
  application, and local mutation bounds.
- Scratch databases were removed after guarded runs. The active database remains
  `trading_bot_db|f7c8d9e0a1b2`, with zero registration identity columns, zero registration scratch
  databases, and zero unsynced `change_log` rows.

## Remaining Gate

The remediation is not self-approved. Stage 3 can start only after the exact new commit is reviewed.
The reviewer must verify both blocker fixes and confirm that the accepted Medium/later findings are
visible hard gates rather than silently dismissed work.
