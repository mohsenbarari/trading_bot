# Dual-Platform Registration - Stage 1 Contracts, Schema, And Registry

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Stage 0 base commit: `4a36574f`
- Migration: `a8d9e0f1b2c3` (down revision `f7c8d9e0a1b2`)
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`

## Status

Stage 1 is complete as a source-code milestone.

No staging or production deployment was run. The active staging database remains at
`f7c8d9e0a1b2`; the Stage 1 migration was exercised only in automatically cleaned scratch
databases. All direct Telegram registration, reconciliation, Telegram-first login OTP, automatic
SMS fallback, contract-v2, and registration-sync-v2 entry flags remain disabled by default.

Stage 1 is not an independent rollout unit. Stage 3 must connect every invitation writer to the
global reservation service before this migration is applied to an environment that accepts new
invitations. This prevents invitations created between stages from bypassing the reservation table.

## Implemented Foundation

### Authoritative product schema

- `Invitation.kind` is explicit: `standard`, `accountant`, `customer`, or `legacy_unknown`.
- Completion uses the compatible atomic tuple `is_used`, `registered_user_id`, `completed_at`, and
  `completed_via`; revocation uses `revoked_at`; runtime state remains derived rather than stored.
- `users`, `invitations`, `customer_relations`, and `accountant_relations` have positive,
  non-null `sync_version` columns.
- The four versioned models use SQLAlchemy optimistic version checks with application-controlled
  version generation. Iran-owned ORM changes increment versions only while registration Sync v2 is
  enabled; receiver bulk applies do not recursively publish events.
- Pending standard deletion and customer/accountant cancellation preserve Invitation history by
  soft revocation and transactionally release any identity reservation.
- New invitation expiry snapshots come only from `get_trading_settings_async()` and preserve the
  accepted two-day setting; relation expiry copies the same Invitation timestamp.

### Local operational schema

| Table | Location | Sync policy | Purpose |
|---|---|---|---|
| `invitation_identity_reservations` | Iran | `no-sync` | Unique pending mobile/account/invitation reservation with deterministic advisory locks |
| `telegram_registration_intents` | Foreign | `no-sync` | Durable post-collection Telegram registration evidence and retry state |
| `telegram_registration_command_receipts` | Iran | `no-sync` | Atomic command/idempotency/hash receipt for replay-safe authoritative completion |

The registry, field policy, parity, seed/reset, worker-priority, full-matrix, migration import, and
backup/restore lists explicitly include or exclude these tables according to that policy. Sensitive
mobile, Telegram identity, invitation credential, address, and hash fields have explicit policy
entries.

### Contracts and security boundary

- Strict Pydantic command/response/OTP/invitation-v2 contracts reject extra fields, invalid source
  surfaces, wrong proof types, naive timestamps, invalid proof timelines, and malformed identity.
- Canonical JSON and SHA-256 request hashing make changed-payload replay distinguishable from a
  safe retry. Command and idempotency advisory locks are acquired in deterministic order.
- The exact existing Web registration address rule is authoritative: minimum 10 characters with
  the existing Persian message and no new trimming, maximum, or profile-edit policy.
- Public invitation validation masks mobile numbers, sets `no-store`, is protected by an atomic
  Redis fixed-window rate limit, and fails closed if rate-limit state is unavailable.
- Application request paths redact lookup/validation credentials. Nginx access logging is disabled
  for those secret-bearing paths and responses carry no-cache headers.
- `PUBLIC_WEBAPP_URL` must be an origin, use HTTPS outside local/test, match an explicit Iran
  host/alias, and must not match a foreign host. WebApp and sync/API hostnames can therefore remain
  separate without permitting links to point at the foreign server.

### Source-aware sync contract

- User events are patch-only while Sync v2 is enabled. Iran emits account/identity/admin/access
  fields; foreign emits only monotonic bot-onboarding fields and approved `last_seen_at`.
- User inserts are accepted only from Iran. Foreign writes to Invitation or customer/accountant
  Relation records are rejected by the registration sync policy.
- Counters are omitted from generic User patches. Unauthorized fields are dropped with field names
  only, without raw values.
- Iran User and all Invitation/Relation changes apply only when `sync_version` is newer. The main
  upsert and natural-key fallback both enforce the version guard.
- `last_seen_at` uses a maximum merge in direct patch, replay, and natural-key fallback paths;
  foreign onboarding steps and completion timestamps cannot regress.
- Mixed-version acceptance is controlled independently by
  `REGISTRATION_SYNC_ACCEPT_UNVERSIONED`; sync health advertises versioned-event support and both
  gate states.

## Decision Records

| Decision | Stage 1 record |
|---|---|
| `DT-01` | Explicit kind, no stored status, conservative legacy backfill, atomic completion checks, and soft revocation are implemented. Ambiguous legacy rows cannot enter a new completion path. |
| `DT-02`, `DT-13` | One Iran-local reservation row owns all three unique keys. Locks use sorted mobile/account lock names; split reservations and any differing collision fail closed. Exact creator/kind/payload retry authorization remains part of the canonical Stage 3 writer. |
| `DT-05` | Field ownership is source-aware, patch-only, version-guarded, counter-free, and monotonic for shared/onboarding fields. |
| `DT-08` | Existing token and short-code links are retained; public responses are masked, rate-limited, non-cacheable, and removed from normal access logs. No token-exchange redesign was introduced. |
| `DT-11` | The strict additive v2 shape includes explicit bot/Web links, availability, derived state, expiry, SMS status, and temporary `link`/`short_link` aliases. Route wiring and alias-consumption telemetry wait for Stage 3/7 because v2 is disabled. |
| `DT-12` | Existing UTC storage/display conventions are unchanged. Only Invitation lifecycle metadata and exact boundary helpers were added. User deletion behavior is untouched. |
| `DT-14` | Existing HMAC transport remains the required transport. Stable command/idempotency identity, canonical hash, deterministic locks, source guard, and transaction-owned receipt primitives are ready for the Stage 4 route. |
| `DT-16` | The Web minimum-10 address rule is shared by current Web completion and the future Telegram command boundary without adding normalization. |
| `DT-18` | Proof must complete no later than stored expiry; receive is accepted through the inclusive `expires_at + 86400` boundary; explicit revocation always wins. |

No `manual_review` enum, state, route, queue, or override was added.

## Migration Evidence

All destructive migration operations targeted generated `stage1_*` scratch databases and used an
exit trap to terminate sessions and drop those databases.

1. Empty database: upgrade to head, second upgrade, downgrade to `f7c8d9e0a1b2`, and re-upgrade all
   passed; tables, non-null version columns, constraints, indexes, enums, and head were verified.
2. Production-shaped staging clone: row counts across User, Invitation, CustomerRelation, and
   AccountantRelation were unchanged through upgrade/downgrade/re-upgrade. The inspected clone had
   zero `legacy_unknown` rows and zero active reservations.
3. Synthetic matrix: relation-first and prefix fallback, conflicting/missing evidence,
   conservative completion backfill, five pending reservations, every partial completion
   constraint, positive versions, intent/receipt checks, Persian/Arabic-digit collision, and active
   User collision all behaved as specified.
4. Collision upgrades rolled back transactionally to `f7c8d9e0a1b2`; no Stage 1 table remained and
   no row-order winner was selected.
5. Cleanup verification found zero `stage1_*` databases. A read-only query confirmed active staging
   still reports `f7c8d9e0a1b2`.

## Verification Evidence

- Focused registration/invitation/relation/event/logging tests: `184` passed.
- Complete sync test family: `312` passed.
- Runtime env, backup, release-artifact, resource, observability, and full-matrix tooling: `55`
  passed.
- Local deploy/Nginx surface smoke: `27` passed.
- Web registration and short-link frontend unit tests: `6` passed; production frontend build passed.
- Full backend regression suite after fixture repair: `2678` passed.
- `compileall`, shell syntax checks, `git diff --check`, and Alembic single-head check passed;
  Alembic head is `a8d9e0f1b2c3`.

The counts above overlap and are evidence of the executed gates, not a claim of mathematical 100%
coverage. Roadmap Stage 9 remains responsible for coverage, mutation, property/fuzz, checkpoint,
and traceability gates.

## Flags-Off Record

The following entry paths are disabled by default and in both environment examples:

- `TELEGRAM_DIRECT_REGISTRATION_ENABLED=0`
- `TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED=0`
- `TELEGRAM_LOGIN_OTP_ENABLED=0`
- `OTP_SMS_AUTO_FALLBACK_ENABLED=0`
- `INVITATION_CONTRACT_V2_ENABLED=0`
- `REGISTRATION_SYNC_V2_ENABLED=0`

`REGISTRATION_SYNC_ACCEPT_UNVERSIONED=1` is only a compatibility gate; it does not enable Sync v2.
Invitation SMS category defaults remain independent: Standard/admin and Tier 1 are disabled;
accountant and Tier 2 are enabled. Stage 3 performs the actual centralized policy wiring.

## Preconditions For Stage 2

1. Keep every new feature and Sync v2 flag off.
2. Extract current Web completion into one Iran-owned transaction without changing its observable
   Web behavior.
3. Lock the Invitation row, use natural-key conflict reads, and integrate the command receipt in
   the same transaction without adding a second worker or outbox.
4. Preserve relation activation, mandatory membership, limits/defaults, sessions, existing
   notification outbox behavior, and rollback semantics.
5. Write literal `home_server="iran"` in the shared service; do not derive it from request host or
   the model default.
6. Do not enable Telegram completion, deploy staging, or touch production in Stage 2.
