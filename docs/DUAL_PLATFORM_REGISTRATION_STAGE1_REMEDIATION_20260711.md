# Dual-Platform Registration - Stage 1 Independent Review Remediation

- Date: 2026-07-11
- Branch: `candidate/bot-webapp-integration`
- Review input: `tmp/chatgpt/dual-platform-registration-stage1-independent-review.md`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`
- Review verdict received: `NO-GO`
- Deployment scope: source only; no application release or feature enablement

## Status And Gate

Every accepted blocking finding from the independent Stage 1 review has an implemented remediation
and focused evidence. Stage 1 remains **awaiting independent re-review**; this document does not
self-approve the Stage 3 gate. Stage 2 was already implemented before the delayed Stage 1 review was
received. It has therefore been revalidated against these changes but remains provisional until the
reviewer returns `GO` for this remediation. Stage 3 must not start before that decision.

No staging/production application deployment or runtime feature enablement was performed. During
final evidence collection, one `docker compose run` accidentally started its declared migration
dependency and briefly upgraded the active local runtime database from `f7c8d9e0a1b2` to
`a8d9e0f1b2c3`. This was detected immediately, reversed with the official Alembic downgrade, and
then verified by revision, schema-absence, sync-health, and zero-backlog checks. Every subsequent
scratch command used `--no-deps`. All registration and Sync-v2 flags remain disabled by default.

## Accepted Finding Disposition

| Review finding | Remediation | Primary evidence |
|---|---|---|
| `C-1` User counter writes conflict with the mandatory outbox guard | Added a dedicated `user_counter_event_v1` contract. Counter-only User writes now create a transactional `change_log` row, increments are delta events, reset advances an Iran-owned epoch, and a local no-sync receipt applies each event exactly once. Every direct counter writer uses the shared mutation helpers. | `tests/test_user_counter_sync.py`, `tests/test_registration_sync_apply.py`, real PostgreSQL counter ordering/replay test |
| `H-1` legacy Invitation completion backfill can attach a reused identity | Removed natural-key-only completion inference. Only an active/expired relation linked to the same User, with non-deleted state and activation inside Invitation lifetime, may backfill completion. Standard used rows without durable relation evidence stay ambiguous. | `tests/test_registration_stage1_migration_postgres.py` five-case real migration matrix |
| `H-2` Telegram address is trimmed | Declared the command address as a field-level non-stripping Pydantic string while retaining strict trimming for other command strings. The exact minimum-10 rule and Persian message are unchanged. | exact spaces, 9/10 boundary, and canonical hash tests |
| `H-3` invitation surface availability diverges from bot access policy | Added one pure role/kind/tier evaluator in `bot_access_policy` and reused it from contract-v2 generation and authoritative completion. Unknown kind/tier and Watch fail closed; Standard/admin/police and tier-1 rules remain owner-approved. | exhaustive role/kind/tier matrix |
| `H-4` raw `/register` and `/i/` credentials can enter Nginx logs | Added no-log locations to active staging, production, recovery, and setup templates; disabled redirect-server logging; reduced the legacy root error log from debug to warn. Sensitive Iran SPA locations serve `/index.html` without an internal redirect, which otherwise re-enabled logging; `Referrer-Policy: no-referrer` prevents a credential-bearing URL from leaking through later asset requests; the foreign setup rejects both frontend routes at Nginx instead of forwarding their paths to application logs. Recovery invitation APIs also fail closed without access logging. | real Nginx request/header/access-log scan plus syntax and template assertions |
| `H-5` versioned User sync trusts cross-server integer IDs | Every new versioned User event carries current and changed previous natural identities. UPDATE resolves and locks the unique local User, rejects split identity, and defers missing identity. INSERT updates an existing natural match and rejects an unrelated numeric-ID collision. Versioned payloads without identity fail closed, while an explicitly accepted unversioned Iran INSERT retains the pre-v2 compatibility upsert. | differing-ID, split-identity, missing-identity, legacy-INSERT, and INSERT collision tests |
| `H-6` foreign onboarding advances a shared clock that rejects delayed Iran truth | Foreign patches preserve row `updated_at`; Iran source time is data only and is no longer a compatibility acceptance predicate. Same-source ordering remains enforced by existing source-sequence watermarks. | SQL compilation tests plus existing stale/duplicate/conflict watermark suite |
| `M-1` source filtering alone allows unauthorized local foreign mutation | A Sync-v2 foreign ORM write guard rejects Iran-owned User field changes before flush. Counter delta and foreign onboarding fields remain allowed; counter reset/epoch remains Iran-only. | foreign identity/reset authority tests |
| `M-2` `peer_server_url` is always classified as foreign | Peer classification now depends on local server role: Iran sees peer as foreign; foreign sees peer as Iran. Explicit foreign host fields remain rejected on both roles. | role-aware URL tests |
| `M-4` command receipt terminal tuples lack DB invariants | Added atomic pending/terminal and outcome/User check constraints. Success requires an authoritative User; non-success forbids one. | model/migration assertions and real PostgreSQL valid/invalid matrix |
| `M-5` advisory lock text exposes mobile/account/Telegram identity | All identity advisory lock values are SHA-256 digests with a non-sensitive namespace before PostgreSQL hashing. | deterministic ordering and raw-value absence tests |

`M-3` remains an operational migration gate, not a source defect: Stage 3 must connect every
Invitation writer before applying the Stage 1 migration to a writing environment. `M-6` exact
invitation-create retry validation belongs to Stage 3. `M-7` alias telemetry belongs to Stage 3/7. Intent state-tuple
constraints remain a Stage 4 state-machine deliverable rather than being guessed before that worker
exists. The review's low-severity Stage 6/9 evidence items remain assigned to their roadmap stages.

## Counter Contract Invariants

1. Generic User identity/profile patches never carry the three counters.
2. A local increment changes the User aggregate and emits one UUID delta event in the same
   transaction; the outbox guard therefore cannot commit one without the other.
3. A reset is Iran-authoritative, increments `counter_epoch` exactly once, and zeroes all counters.
4. The receiver locks the natural-identity-resolved User and stores a local receipt before applying
   the event. Receipt and counter update commit or roll back together.
5. Replaying the same UUID and canonical event hash is ignored. Reusing a UUID with a different
   source, target User, epoch, kind, or delta fails closed.
6. An event from an older epoch is recorded and ignored. A new-epoch increment arriving before its
   reset clears the old epoch and applies the increment; the later same-epoch reset is a no-op so it
   cannot erase post-reset work.
7. During a network partition, Iran's reset epoch is authoritative. Foreign events produced against
   an older epoch are ignored after reconnection; this is the explicit fail-safe behavior rather
   than silently reintroducing pre-reset usage.

## Migration And Runtime Safety Evidence

- A dedicated `stage1_migration_*` PostgreSQL database upgraded to the prior revision, seeded five
  historical completion cases, upgraded to `a8d9e0f1b2c3`, verified every result, and downgraded to
  `f7c8d9e0a1b2` successfully.
- A dedicated `stage2_registration_*` PostgreSQL database passed the authoritative race/rollback,
  receipt constraint, and counter replay/epoch suite.
- A dedicated `stage1_counter_*` PostgreSQL database proved that a real ORM counter-only commit and
  a Trade-plus-counter transaction both satisfy the mandatory outbox guard and retain separate
  durable counter events through the existing sync worker.
- A real temporary Nginx process logged a normal route while omitting the raw token and short code
  from `/register?token=...` and `/i/...`; the root config also passed `nginx -t`.
- Scratch databases are removed after final evidence collection. The active database is back on
  `f7c8d9e0a1b2`; `counter_epoch` and `user_counter_event_receipts` are absent, `/api/sync/health`
  reports `ok`, Redis is healthy, and the unsynced backlog is zero.

## Re-Review Requirements

The independent reviewer must verify the exact remediation commit against `bc2d4fcc`, validate this
finding matrix, inspect raw test logs and package hashes, and return an explicit `GO` or `NO-GO` for
starting Stage 3. A `GO` does not authorize staging migration, production release, or feature-flag
enablement; those remain later roadmap gates.

## Final Verification

- Full backend regression outside the sandbox: `2730` passed, `8` opt-in tests skipped.
  This run includes Dockerfile build checks and Nginx syntax/runtime surfaces.
- Focused remediation and affected-domain suite: `182` passed.
- Real Stage 1 PostgreSQL migration/backfill/round-trip module: `2` passed.
- Real Stage 2 PostgreSQL race/rollback/receipt/counter module: `8` passed.
- Real sender-side PostgreSQL counter/outbox/Trade module: `3` passed.
- Dedicated Nginx template, runtime credential-log scan, and syntax module: `3` passed.
- Python compile, shell syntax, Alembic single-head, diff whitespace, changed-file secret scan,
  active revision, scratch cleanup, and flags-off checks are included in the review package.
