# Three-site continuity matrix v1

## Status and authority

**Status: `blocked/unresolved`.**  This is a versioned, static architecture
contract for the FI writer, IR standby, and Iran-reachable Witness topology.
It does not enable a receiver, database replication, worker, deployment,
traffic route, or promotion.

The matrix freezes the continuity classification that is required before a
Full Matrix can claim an exact active/standby pair.  It deliberately supersedes
the *scope* interpretation of the legacy `SyncPolicy` labels:

- `SYNC`, `NO_SYNC`, and `INTERNAL_BOOKKEEPING` describe the old logical
  direct-sync path; they do **not** say that a table may be absent from a
  physical standby.
- Every listed database table requires the same transaction-consistent
  physical PostgreSQL baseline and ordered-WAL generation/replay proof.
- There is exactly one application writer: the holder of the current witnessed
  term.  Legacy labels such as `iran shared admin authority`,
  `offer_home_server`, and `foreign Telegram delivery owner` are business or
  execution-routing labels, never a second write authority.
- A row reaching the replay point is necessary but not always sufficient.  A
  blob, session/key, or external-effect dependency may impose a further
  promotion gate below.

Changing an entry, a P0 status, or the interpretation of a profile requires a
new matrix version and matching static-test review.  This makes the inventory
immutable for a given release rather than silently widening a Full Matrix
claim.

The governing design is
[THREE_SITE_DATA_PLANE_DECISION.md](THREE_SITE_DATA_PLANE_DECISION.md):
physical database state is baseline + ordered WAL; uploads additionally need
immutable Object Storage evidence; Redis is reconstructed; and a witnessed,
term-fenced promotion must not replay an external side effect.

## P0 decisions that remain blocked

| ID | Status | Affected matrix profiles | Required explicit choice before promotion |
| --- | --- | --- | --- |
| `P0_UPLOAD_IN_FLIGHT` | `blocked/unresolved` | `messenger_upload` | Either store every resumable upload chunk under immutable, generation-bound Object Storage evidence, or atomically cancel/expire all in-flight uploads at promotion. A database `temp_storage_path` alone is not portable. |
| `P0_SESSION_AUTH_CONTINUITY` | `blocked/unresolved` | `auth_session` | Either preserve sessions/login/recovery state with proven signing/verification-key continuity, or deliberately invalidate it at promotion with a safe, observable re-authentication/recovery flow. |
| `P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION` | `blocked/unresolved` | `effect_state`, `external_runtime`, and marked business/receipt entries | Define whether each Telegram/SMS/Web Push/provider action is deferred or executed by an eligible term-fenced executor. Durable receipts, provider idempotency, lease/epoch reset, and an explicit no-resend rule are required. FI must not keep an effectful worker after it loses Witness quorum. |

## Selected safe defaults and current local boundary

The emergency path must not leave these choices implicit.  The following
defaults are selected because they preserve the single-writer/no-duplicate
invariant.  Their P0 status remains `blocked/unresolved`: local primitives
are useful only when a future promotion coordinator invokes them under a live
Witness term, physical-recovery evidence, traffic fence, and destructive
acceptance test.

| P0 | Selected policy | Local boundary now present / remaining runtime proof |
| --- | --- | --- |
| `P0_UPLOAD_IN_FLIGHT` | `cancel_and_expire_unfinalized_uploads` | A transaction participant locks upload rows and cancels only unfinalized work; finalized/READY/committing state fails closed. It still needs the real promotion coordinator, traffic fence, and verified blob frontier. |
| `P0_SESSION_AUTH_CONTINUITY` | `invalidate_sessions_on_promotion` | A durable auth epoch and append-only operation ledger revoke session/login/recovery state and old access JWTs. It still needs migration deployment and one real promotion transaction before traffic is admitted. |
| `P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION` | `defer_external_effects_until_term_fenced_executor` | A default-off term-bound no-resend gate now covers named worker and concrete provider boundaries. Deployment, a complete scope inventory, reconciliation decisions, executor ownership, and destructive no-duplicate proof remain mandatory. |

## Continuity profiles

| Profile | Count | Database continuity | Promotion action / additional oracle |
| --- | ---: | --- | --- |
| `shared_control_db` | 10 | physical baseline + ordered WAL | Verify matching generation/replay position; admit writes only under the new witnessed term; rebuild caches from DB. `market_runtime_state` additionally carries the external-effect P0. |
| `writer_business_db` | 8 | physical baseline + ordered WAL | Current term is the only write authority. Rows that enqueue a delivery/publication carry the external-effect P0. |
| `effect_state` | 5 | physical baseline + ordered WAL | Preserve receipt/state, then only an eligible term-fenced worker may reclaim work under a new epoch; never infer permission to resend from a restart. |
| `messenger_upload` | 7 | physical baseline + ordered WAL | Rebuild Redis/WebSocket state. All database-visible blobs must exist through the IR Object Storage route; in-flight upload handling is P0. |
| `auth_session` | 4 | physical baseline + ordered WAL | Block until the session/key policy is selected and independently tested. |
| `concurrency_receipt` | 4 | physical baseline + ordered WAL | Preserve uniqueness, idempotency, and terminal receipts; expiry/reconciliation resumes only under the new term. |
| `external_runtime` | 4 | physical baseline + ordered WAL | Preserve durable evidence, but defer or execute only under the explicit external-effect P0 policy. |
| `legacy_internal` | 3 | physical baseline + ordered WAL | Legacy bookkeeping will be present physically, but a promoted runtime must rebuild or retire legacy-worker semantics instead of treating an old local cursor as authority. |

## Immutable machine-checked inventory

The JSON below is the authoritative v1 inventory used by the static test.  The
profile explains the additional continuity oracle; `db_replay` is intentionally
`physical_base_wal_required` for all 45 entries.

```json
{
  "schema": "gold-trade-three-site-continuity-matrix-v1",
  "status": "blocked/unresolved",
  "db_replay_contract": "physical_base_wal_required",
  "p0_decision_slots": {
    "P0_UPLOAD_IN_FLIGHT": {
      "status": "blocked/unresolved",
      "selected_policy": "cancel_and_expire_unfinalized_uploads",
      "profiles": ["messenger_upload"]
    },
    "P0_SESSION_AUTH_CONTINUITY": {
      "status": "blocked/unresolved",
      "selected_policy": "invalidate_sessions_on_promotion",
      "profiles": ["auth_session"]
    },
    "P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION": {
      "status": "blocked/unresolved",
      "selected_policy": "defer_external_effects_until_term_fenced_executor",
      "profiles": ["effect_state", "external_runtime", "writer_business_db", "concurrency_receipt", "shared_control_db"]
    }
  },
  "entries": [
    {"table": "accountant_relations", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "admin_broadcast_messages", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "admin_market_messages", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "commodities", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "commodity_aliases", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "customer_relations", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "invitations", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "market_runtime_state", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "market_schedule_overrides", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "trading_settings", "legacy_policy": "SYNC", "profile": "shared_control_db", "db_replay": "physical_base_wal_required", "p0_slots": []},

    {"table": "users", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "user_blocks", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "user_notification_preferences", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "telegram_link_tokens", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "notifications", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "offers", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "offer_requests", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "trades", "legacy_policy": "SYNC", "profile": "writer_business_db", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},

    {"table": "offer_publication_states", "legacy_policy": "SYNC", "profile": "effect_state", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "trade_delivery_receipts", "legacy_policy": "SYNC", "profile": "effect_state", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "telegram_admin_broadcasts", "legacy_policy": "SYNC", "profile": "effect_state", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "telegram_admin_broadcast_receipts", "legacy_policy": "SYNC", "profile": "effect_state", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "telegram_notification_outbox", "legacy_policy": "SYNC", "profile": "effect_state", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},

    {"table": "chat_files", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_UPLOAD_IN_FLIGHT"]},
    {"table": "chat_members", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "chats", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "conversations", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "messages", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_UPLOAD_IN_FLIGHT"]},
    {"table": "upload_batches", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_UPLOAD_IN_FLIGHT"]},
    {"table": "upload_sessions", "legacy_policy": "NO_SYNC", "profile": "messenger_upload", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_UPLOAD_IN_FLIGHT"]},

    {"table": "session_login_requests", "legacy_policy": "NO_SYNC", "profile": "auth_session", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_SESSION_AUTH_CONTINUITY"]},
    {"table": "single_session_recovery_admin_targets", "legacy_policy": "NO_SYNC", "profile": "auth_session", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_SESSION_AUTH_CONTINUITY"]},
    {"table": "single_session_recovery_requests", "legacy_policy": "NO_SYNC", "profile": "auth_session", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_SESSION_AUTH_CONTINUITY"]},
    {"table": "user_sessions", "legacy_policy": "NO_SYNC", "profile": "auth_session", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_SESSION_AUTH_CONTINUITY"]},

    {"table": "invitation_identity_reservations", "legacy_policy": "NO_SYNC", "profile": "concurrency_receipt", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "offer_expiry_command_receipts", "legacy_policy": "NO_SYNC", "profile": "concurrency_receipt", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "telegram_registration_command_receipts", "legacy_policy": "NO_SYNC", "profile": "concurrency_receipt", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "user_counter_event_receipts", "legacy_policy": "NO_SYNC", "profile": "concurrency_receipt", "db_replay": "physical_base_wal_required", "p0_slots": []},

    {"table": "market_channel_notice_receipts", "legacy_policy": "NO_SYNC", "profile": "external_runtime", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "telegram_registration_intents", "legacy_policy": "NO_SYNC", "profile": "external_runtime", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "invitation_sms_deliveries", "legacy_policy": "NO_SYNC", "profile": "external_runtime", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},
    {"table": "push_subscriptions", "legacy_policy": "NO_SYNC", "profile": "external_runtime", "db_replay": "physical_base_wal_required", "p0_slots": ["P0_EXTERNAL_EFFECTS_DURING_IR_ISOLATION"]},

    {"table": "change_log", "legacy_policy": "INTERNAL_BOOKKEEPING", "profile": "legacy_internal", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "sync_apply_watermarks", "legacy_policy": "INTERNAL_BOOKKEEPING", "profile": "legacy_internal", "db_replay": "physical_base_wal_required", "p0_slots": []},
    {"table": "sync_blocks", "legacy_policy": "INTERNAL_BOOKKEEPING", "profile": "legacy_internal", "db_replay": "physical_base_wal_required", "p0_slots": []}
  ]
}
```

## Promotion proof required for every entry

Before the standby may take traffic as writer, the campaign must independently
prove the following, rather than treating this document or legacy sync health
as evidence:

1. The selected base generation, release, schema revision, source write fence,
   encrypted artifact hashes, and durable IR replay position match.
2. The Witness issued a strictly newer term after FI self-fenced; every write
   boundary and eligible worker uses that term before commit or external I/O.
3. Every database-visible blob at the eligible replay position is available by
   the IR pull route, and the selected P0 upload policy covers partial work.
4. Redis locks, queues, WebSocket fan-out, and rate limits are rebuilt from
   durable state; they are never used as authority copied from FI.
5. The selected session and external-effect P0 policies have independent
   acceptance evidence, including no duplicate Telegram/SMS/Web Push action.

Until all three P0 decisions have moved out of `blocked/unresolved` and their
oracles pass, this matrix is a blocker record, not Full Matrix readiness.
