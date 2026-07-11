# Dual-Platform Registration - Stage 2 Authoritative Service

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Stage 2 base commit: `00aab349`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`
- Stage 1 migration head: `a8d9e0f1b2c3`

## Status

Stage 2 is implemented as a source-code milestone, but is provisional pending the reopened Stage 1
remediation review. The existing Web registration completion route now
uses one shared Iran-authoritative transaction. The same service contains the future strict
Telegram-command projection, but no Telegram route, reconciliation loop, job, worker, or feature
entry point is enabled.

No staging or production deployment was run. The active local runtime database remains at
`f7c8d9e0a1b2`; the Stage 1 schema and real concurrency tests were exercised only in isolated
`stage2_registration_*` scratch databases, all of which were removed after verification.

## Authoritative Transaction

`core/services/authoritative_registration_service.py` owns commit and rollback for registration
product state. Its ordered boundary is:

1. fail closed unless `current_server()` is Iran;
2. validate the strict surface/proof request;
3. for a future Telegram command, acquire command/idempotency locks and prepare the existing
   Iran-local command receipt;
4. lock the Invitation row with `SELECT FOR UPDATE`;
5. validate explicit Invitation kind, token-kind consistency, revocation, expiry, and the inclusive
   post-expiry reconciliation boundary;
6. acquire sorted mobile/account/Telegram-ID advisory locks and lock matching User rows;
7. lock and validate the matching customer or accountant Relation without a nested commit;
8. deterministically reject conflicts, or create/link exactly one User;
9. activate the Relation, complete the Invitation, release the identity reservation, and ensure
   mandatory-channel membership in the same transaction;
10. enqueue the existing `telegram_notification_outbox` announcement before commit and finalize
    the command receipt in that same transaction;
11. commit once, or roll back User, Invitation, Relation, reservation release, receipt, membership,
    and outbox changes together.

New users always receive literal `home_server="iran"`, `max_sessions=1`, and
`must_change_password=False`. This does not use the request host, `_login_home_server`, or the
User model's current `foreign` default. No access/refresh token or Web session is created by the
shared service.

## Preserved Web Adapter Behavior

`api/routers/auth.py:register_complete` remains responsible for the existing Web-only adapter work:

- resolve the Redis registration session or require the existing verified OTP marker;
- submit the locked Invitation token and validated address to the authoritative service;
- preserve existing Persian HTTP details for domain rejections;
- publish best-effort Web notifications only after the authoritative commit;
- clear the existing Redis registration keys;
- create the existing local Web login session and access/refresh tokens from the committed Iran
  User.

The adapter no longer constructs a User, activates Relations, completes an Invitation, releases a
reservation, or commits registration product state itself. It also no longer derives registration
ownership from the incoming request host.

## Reuse And Side Effects

- Customer and accountant services expose lock/activate primitives that do not own commit or
  rollback; their existing creation, expiry, capacity, and management behavior is unchanged.
- Mandatory membership still uses `ensure_mandatory_channel_membership`.
- Telegram announcements reuse `telegram_notification_outbox` and its existing foreign delivery,
  retry, and dedupe worker. No second outbox table or worker exists.
- Web notifications retain the existing message, route, recipient behavior, and best-effort
  post-commit failure boundary.
- Standard/admin/police do not wait for a nonexistent Relation. Tier-1 customer registration uses
  its required Relation. Accountant and Tier-2 customer invitations remain Web-only for the future
  Telegram adapter.
- A Web winner keeps its authoritative profile/address when a later valid Telegram command links
  the Telegram ID and bot-compatibility fields.

## Deterministic Outcomes

- Web/Web: the first transaction creates and commits one User; the locked loser receives
  `invitation_already_used`.
- Web/Telegram: a Web winner remains the profile owner and the Telegram command links that same
  User; a lost response replays the stored receipt.
- Telegram/Web: a Telegram winner creates one User and the Web loser receives
  `invitation_already_used`.
- Same Telegram command and canonical payload: return the prior terminal receipt without a second
  mutation or outbox insertion.
- Same command identity with changed payload: terminal `changed_payload_replay`, no mutation.
- Revoked, expired, malformed legacy, inactive/deleted, Relation-invalid, and identity-conflict
  states fail closed with bounded outcomes.

## Verification Evidence

- Complete backend regression, including deploy/Docker/Nginx smoke outside the sandbox: `2700`
  passed, `4` opt-in PostgreSQL tests skipped in that run.
- Real PostgreSQL Stage 2 module on a fresh migrated scratch database: `6` passed. This comprises
  four transaction/race tests and two scratch-database guards.
- PostgreSQL barriers prove Web/Web serialization, Web-first linking, Telegram-first Web rejection,
  stable receipt replay, and rollback after receipt/outbox flush with no User, receipt, or outbox
  residue and with the Invitation/reservation still pending.
- Focused service, Web adapter, notification, and Relation tests passed after the server guard.
- `compileall`, `git diff --check`, caller inventory, flags-off checks, worker/route inventory,
  session-boundary scan, and changed-file secret scan passed.
- Final database evidence reports active `trading_bot_db|f7c8d9e0a1b2` and
  `scratch_count=0`.

These results close Stage 2 exit criteria; they are not a mathematical 100% coverage claim. The
property/fuzz, mutation, diff-coverage, checkpoint, and full traceability gates remain assigned to
Roadmap Stage 9.

## Operational Safety Record

During scratch setup, an initial Alembic command redirected `DATABASE_URL` but not
`SYNC_DATABASE_URL`. `migrations/env.py` reads only `SYNC_DATABASE_URL`, so the Stage 1 migration
was briefly applied to the active local database. The mismatch was detected before registration
tests wrote any data. The active database was immediately downgraded to `f7c8d9e0a1b2`, the Stage 1
table absence and runtime database health were verified, and subsequent checks continued to report
that exact active head.

Corrective controls now require PostgreSQL test targets to match
`stage2_registration_[a-z0-9_]+`; the final migration command asserted that name and set both
database URL variables. Before cleanup, every scratch database reported `a8d9e0f1b2c3`; after
cleanup, the active database reported `f7c8d9e0a1b2` and the scratch count was zero. A stale bot
container mount was also rejected as a migration source after its Alembic head differed from the
checkout; final migration and tests used the app container mounted to this checkout.

## Flags And Rollout Boundary

The following entry paths remain disabled by default and were not changed in Stage 2:

- `TELEGRAM_DIRECT_REGISTRATION_ENABLED=0`
- `TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED=0`
- `TELEGRAM_LOGIN_OTP_ENABLED=0`
- `OTP_SMS_AUTO_FALLBACK_ENABLED=0`
- `INVITATION_CONTRACT_V2_ENABLED=0`
- `REGISTRATION_SYNC_V2_ENABLED=0`

In the original Stage 2 implementation, no environment, deploy manifest, Nginx route, staging
runtime, production runtime, bot handler, background leader, or worker definition changed. The
later Stage 1 remediation changed Nginx source templates only; it did not deploy them.

## Stage 1 Remediation Revalidation

The delayed independent Stage 1 review arrived after Stage 2 implementation and returned `NO-GO`.
Stage 2 was not rewritten or discarded. It was revalidated against the remediation's stricter
receipt tuples, shared role/kind/tier policy, opaque advisory locks, natural-identity User sync,
counter event contract, and migration schema. The real PostgreSQL module now also covers receipt
constraint failures and exact-once counter epoch/replay behavior (`8` passing tests after
remediation, replacing the original `6`-test evidence). Stage 3 remains blocked until the
independent reviewer accepts the remediation.

## Preconditions For Stage 3

1. Classify the mandatory ChatGPT Stage 2 review and resolve every accepted blocker before coding.
2. Keep all new registration and Sync v2 flags off.
3. Make Iran the only canonical invitation writer before applying Stage 1 schema in an environment
   that accepts new invitations.
4. Route bot-admin creation through the existing signed Iran transport; do not call a Web router
   function or write an Invitation on foreign.
5. Reuse the Stage 1 reservation/lifecycle/public-access services and the existing SMS templates;
   do not create parallel invitation logic.
6. Preserve the approved per-category SMS policy and return both canonical links only to an
   authenticated administrator.
7. Do not deploy staging or touch production without the later roadmap gates and owner approval.

## Independent Stage 2 Review Remediation

The independent Stage 2 report was reconciled against the later Stage 1 remediation baseline at
`c8bb4a1a`. The remaining accepted findings were implemented and are documented in
`docs/DUAL_PLATFORM_REGISTRATION_STAGE2_REMEDIATION_20260711.md`. The remediation adds canonical
User identity columns/indexes, shared terminal-writer locks, durable uniqueness-conflict receipts,
dedicated notification sessions, current eligibility reads, a guarded scratch migration entrypoint,
strict Relation contracts, opaque command locks, and expanded real PostgreSQL race/adapter tests.

The original counts and operational record above are historical evidence for `bc2d4fcc`; the new
handoff package contains the current combined results. Stage 3 remains blocked pending independent
acceptance. No runtime migration, deploy, route, worker, or feature flag was enabled by remediation.
