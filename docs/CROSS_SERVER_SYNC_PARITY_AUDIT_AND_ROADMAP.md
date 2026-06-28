# Cross-Server Sync Parity Audit And Hardening Roadmap

Reviewed on: 2026-06-27
Reviewed commit: `dad6cd05`

This document audits the current database tables, sync policy, receiver behavior, and remaining risks after the `customer_relations` drift incident where the foreign server kept an older linked-relation state with `customer_user_id = NULL` while Iran had the final linked customer id.

The key conclusion is strict:

`sync health = ok` currently proves delivery/backlog health. It does not prove full database parity.

Real prevention of similar bugs requires both:

1. apply-time stale-event prevention for every synced table;
2. runtime parity verification between Iran and foreign databases.

## System Boundaries

- Telegram bot and Telegram channel are foreign-only.
- WebApp is Iran-only.
- Foreign must never connect to the WebApp.
- Iran must never connect to Telegram.
- Messenger tables are Iran/WebApp local and must not be part of cross-server sync, except the existing transitional mandatory-channel projection allowed by the receiver policy.
- Non-messenger product data must remain near-real-time synced.
- `Offer.home_server` is the platform where the offer is published, not the user's permanent server.

## Current Sync Tables

The current receiver table order includes:

`users`, `accountant_relations`, `customer_relations`, `telegram_link_tokens`, `chats`, `chat_members`, `invitations`, `admin_market_messages`, `admin_broadcast_messages`, `notifications`, `user_blocks`, `commodities`, `commodity_aliases`, `trading_settings`, `market_schedule_overrides`, `market_runtime_state`, `offers`, `offer_publication_states`, `offer_requests`, `trades`, `trade_delivery_receipts`.

Historical gap, fixed on `candidate/sync-parity-hardening`:

`user_notification_preferences` was marked as `SyncPolicy.SYNC` and had event
listeners, but was not in `TABLE_ORDER` or `get_model_class()`. That meant old
outgoing `change_log` rows for that table did not have a complete receiver
path. Current branch status: the table is receiver-enabled in `api/routers/sync.py`,
has a model mapping and natural identity by `user_id`, is included in parity
metadata, and is covered by `tests/test_sync_coverage.py`,
`tests/test_sync_router_parsing.py`, `tests/test_sync_router_apply_item_success.py`,
`tests/test_sync_router_fail_closed_policy.py`, and
`tests/test_sync_guarantee_matrix.py`.

## Existing Guarantees

- The sync receiver is signed with HMAC and rejects unauthenticated payloads.
- `change_log` is inserted in the same DB transaction as the changed row.
- The sync worker marks a change delivered only after peer `/api/sync/receive` returns a success/ok response with zero errors.
- Receiver sorts batches by dependency order.
- FK violations are deferred/retried inside the receiver batch.
- `offers` have `offer_public_id`, `version_id`, and terminal-state guards.
- `offer_requests` have `request_home_server + idempotency_key`, `version_id`, and terminal-result guards.
- `trades` have `trade_number` and completed-trade business-field protection.
- `trade_delivery_receipts` have `dedupe_key` and terminal-state protection.
- `accountant_relations` and `customer_relations` now protect a resolved linked user id from a stale non-terminal payload with a NULL linked id.
- `commodities`, `commodity_aliases`, `invitations`, `telegram_link_tokens`, `market_schedule_overrides`, and publication/delivery rows have unique natural keys or dedupe keys.

## Missing Real Guarantees

The system still lacks a general, table-independent guarantee that an older event from the same source cannot overwrite a newer event for the same logical row.

The system also lacks a general parity checker that proves both servers hold the same state for every synced table after queues are empty.

Therefore, the following are still possible in principle:

- stale `users` update overwrites newer profile/status fields;
- stale `invitations` update reopens or misrepresents a used invitation;
- stale `telegram_link_tokens` update reverts a used/revoked token;
- stale `notifications` update can overwrite read/delivery state;
- stale `user_blocks` insert can recreate a block after delete, or stale delete can remove a re-created block;
- stale admin/settings/market state can overwrite a newer admin/runtime state;
- market runtime state can be in parity while the foreign-only Telegram market
  open/close channel notice side effect is missing after an Iran-origin
  transition sync;
- stale publication-state update can overwrite terminal publication state;
- a table can be marked `sync` in registry/events while not being
  receiver-enabled, as historically seen with `user_notification_preferences`;
  the current branch fixes that table and adds coverage tests so the same class
  of mismatch is caught earlier;
- sync health can stay green while row parity is broken.

## Stage 0 Production Drift Snapshot - 2026-06-27

This audit was read-only. It compared the current foreign and Iran production databases after both sync queues reported no unsynced backlog.

Important result:

`delivery clean` was true, but `parity clean` was false.

### Clean Invariants

- `customer_relations`: both servers have one row with the same stable hash; no active relation has `customer_user_id IS NULL`.
- `accountant_relations`: both servers have zero rows; no active relation has `accountant_user_id IS NULL`.
- `notifications`: both servers have 144 rows with the same stable hash; no duplicate `dedupe_key` was found.
- `user_blocks`: both servers have zero rows; no duplicate blocker/blocked pair was found.
- `change_log`: both servers reported zero unsynced rows.

### Tables With Exact Stable Parity In This Snapshot

The following synced tables matched exactly in the spot-check hash:

`accountant_relations`, `admin_broadcast_messages`, `admin_market_messages`, `commodities`, `commodity_aliases`, `customer_relations`, `invitations`, `market_schedule_overrides`, `notifications`, `offer_requests`, `telegram_link_tokens`, `trade_delivery_receipts`, `trades`, `user_blocks`.

### Drift Or Local-Only Differences Found

| Table | Snapshot finding | Classification | Required follow-up |
| --- | --- | --- | --- |
| `user_notification_preferences` | Iran had 3 rows; foreign had 0 rows; no `change_log` rows existed for this table on either side at the historical production snapshot | Historical real sync coverage gap; receiver coverage fixed on `candidate/sync-parity-hardening` | Current branch has receiver coverage and NULL-safe `updated_at` guard tests. Any remaining historical production drift still needs a separate dry-run-first replay/backfill decision before strict parity is treated as production proof. |
| `offers.offer_public_id` | Both servers have 115 offers and identical created-at/status distribution, but all 115 `offer_public_id` values differ | Real legacy identity drift | Do not treat `id`/count equality as proof. Add repair tooling that maps legacy offers by deterministic fields and updates dependent public-id references safely, or explicitly archives/exempts historical inactive rows after product approval. Future migrations must not random-backfill shared public identities independently on both servers. |
| `offers.channel_message_id` | Foreign has 115 non-null values; Iran has 0 | Expected local Telegram runtime field | Keep excluded from sync and from stable parity hash. The parity checker must report it separately as a local-only projection, not as business drift. |
| `offers.exclude_from_competitive_price` and `offers.price_warning_type` | One offer differs between servers | Real payload gap | `core/offer_sync_payload.py` currently omits these fields. Add them to the canonical offer sync payload and tests. |
| `offers.expired_at` | 15 inactive offers differ by expiry timestamp while terminal status and quantities match | Real timestamp drift with lower business risk | Add `expired_at` to terminal-state invariant tests and decide whether offer-home-server timestamp wins, or whether stable parity hashes should compare terminal state separately from local expiry execution time. |
| `offer_publication_states` | Foreign has 97 Telegram publication rows; Iran has 0 | Policy/semantics gap, amplified by `offer_public_id` drift | Decide whether publication rows are shared state or foreign-local Telegram runtime. If shared, repair `offer_public_id` first and sync/replay rows. If local, change registry/parity policy so this table is not falsely treated as globally equal. |
| `trading_settings` | 15 rows on both servers; value hash matches; `updated_at` hash differs | Non-business timestamp drift | Stable parity should compare setting values first. If `updated_at` is used operationally, enforce Iran-authority timestamp sync. |
| `market_runtime_state` | Open/notice/count state matches; transition/update timestamps differ by about one second | Runtime timestamp drift | Guard by `last_transition_at` and classify stable business parity separately from local runtime timestamps. |
| `users` | Count and identity/Telegram/limit/counter/security hashes match; only volatile hash differs | Expected volatile runtime drift | Stable parity hash must exclude or separately classify `last_seen_at`, `updated_at`, and messenger runtime timestamps. |

### Root-Cause Notes From Code Review

- `migrations/versions/a6b7c8d9e0f1_add_offer_public_id.py` random-backfills `offer_public_id` with `random()` and `clock_timestamp()`. Running that migration independently on both servers creates different public identities for the same historical offer rows.
- `core/offer_sync_payload.py` includes `offer_public_id`, `expired_at`, and `channel_message_id`, but does not include `exclude_from_competitive_price` or `price_warning_type`.
- `api/routers/sync.py` intentionally removes `channel_message_id` from incoming offer sync data because Telegram publication is foreign-local.
- `user_notification_preferences` was registered as `SyncPolicy.SYNC` and had
  SQLAlchemy event listeners while missing from `TABLE_ORDER` and
  `get_model_class()`. Current branch status: receiver coverage is fixed; keep
  this note as the historical root cause.

## Table Inventory And Required Changes

### Core Shared Identity And Access Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `users` | sync | PK upsert, counters use greatest value | Old event can overwrite newer account/profile/admin fields; delete/deactivate may be reverted by stale active payload; field authority is documented but not fully enforced at receiver | Add per-field authority rules, monotonic/terminal guards for `is_deleted`, `account_status`, `deactivated_at`, `telegram_id`, `home_server`, limit fields, and profile fields. Add generic source-sequence watermark. |
| `accountant_relations` | sync | Link-null overwrite guard added for `accountant_user_id`; unique active accountant and owner display indexes | Terminal relation states can still be stale-overwritten if future events arrive out of order outside the guarded NULL-link case | Add relation lifecycle state machine: `PENDING -> ACTIVE -> REVOKED/DELETED/EXPIRED` must not move backward. Add source-sequence watermark keyed by relation id and invitation token. |
| `customer_relations` | sync | Link-null overwrite guard added for `customer_user_id`; unique active customer and owner management indexes | Same lifecycle risk as accountant relation; pending/active stale updates may overwrite terminal metadata | Add relation lifecycle state machine and source-sequence watermark keyed by relation id and invitation token. |
| `invitations` | sync | Natural key fallback by `token`; unique token and short code | `is_used=true` can be overwritten by stale `is_used=false`; expired/used invitation state can drift | Add terminal guard: used invitations never become unused. Add natural-key receiver upsert by `token` first. Add source-sequence watermark keyed by token. |
| `telegram_link_tokens` | sync | Natural key fallback by `token_hash`; unique token hash | Used/revoked/expired token can be stale-overwritten to pending; `used_telegram_id` can be cleared by stale payload | Add token lifecycle guard: `used` and `revoked` are terminal for that token. Add source-sequence watermark keyed by `token_hash`. |
| `user_blocks` | sync | Unique `(blocker_id, blocked_id)` exists | Delete/insert order can recreate or remove a block incorrectly; table has hard delete and no tombstone | Convert to soft-delete or add block tombstone/watermark keyed by `(blocker_id, blocked_id)`. Receiver must use the pair as logical identity, not id only. |
| `user_notification_preferences` | sync | Unique `user_id`; receiver-enabled on this branch; parity identity by `user_id`; NULL-safe `updated_at` guard | Historical rows created before receiver coverage may still need dry-run-first replay/backfill; future stale preference writes rely on the `updated_at` guard | Keep receiver coverage tests and decide any production backfill separately with parity evidence. |

### Market And Admin Configuration Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `trading_settings` | sync | Upsert by key | Stale setting can overwrite newer setting; authority says Iran single-writer but receiver does not enforce source authority strongly enough | Add source authority check and `updated_at`/watermark guard by key. |
| `market_schedule_overrides` | sync | Natural key fallback by `date`; unique date | Stale admin update can overwrite newer override | Add receiver upsert by `date` first and `updated_at`/watermark guard. Enforce Iran admin authority. |
| `market_runtime_state` | sync | PK upsert; `last_transition_at` exists | Older transition can overwrite newer open/closed state. A clean synced state also does not prove the foreign-only Telegram open/close notice was sent when the transition originated on Iran. | Guard by `last_transition_at` and source sequence. Older transition must be ignored. Add foreign-owned idempotent market notice reconciliation so synced Iran-origin transitions still publish the Telegram channel notice exactly once. |
| `admin_market_messages` | sync | PK upsert | Active/current market message can be overwritten by stale update; no general authority/source guard | Enforce Iran admin authority. Add source-sequence watermark and optional published/current-state guard. |
| `admin_broadcast_messages` | sync | PK upsert | Mostly append/audit, lower risk; stale update still possible if mutable fields are added | Enforce Iran admin authority. Add source-sequence watermark. |

### Commodity Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `commodities` | sync | Unique name; natural-key fallback by `name` | Stale rename/update can overwrite newer admin state; no `updated_at` column | Enforce Iran admin authority. Add source-sequence watermark. Consider adding `updated_at` if commodity edits remain possible. |
| `commodity_aliases` | sync | Unique alias; natural-key fallback by `alias` | Stale alias move can point alias to older commodity id | Enforce Iran admin authority. Use alias as logical identity and guard by source sequence. Prefer resolving `commodity_id` by synced commodity identity if IDs ever diverge. |

### Offer And Trading Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `offers` | sync | `offer_public_id`, `version_id`, terminal-state guard; `channel_message_id` is intentionally local-only | Historical production rows have divergent `offer_public_id` because a migration random-backfilled the same legacy rows independently on both servers. Current payload also omits `exclude_from_competitive_price` and `price_warning_type`, allowing competitive-warning drift. Residual risk remains for any payload without version. | Require `version_id` for all mutable synced offer payloads. Add missing competitive-warning fields to the canonical payload. Add parity checker. Add safe repair/backfill tooling for legacy public-id drift before relying on public-id parity for historical rows. Keep existing terminal guard and keep `channel_message_id` local-only. |
| `offer_requests` | sync | `request_home_server + idempotency_key`, `version_id`, terminal-result guard | Strong. Residual risk is payload without idempotency/version and parity proof | Fail closed for mutable payloads missing `idempotency_key` or `version_id`. Add parity checker. |
| `trades` | sync | `trade_number`, completed-trade business guard | Completed trade protected. Non-completed state still needs generic sequence/order guard | Add source-sequence watermark for all trade events. Keep completed-trade immutability. |
| `offer_publication_states` | sync | `dedupe_key`, unique offer/surface | Upsert by `dedupe_key` currently lacks version/terminal guard; stale failed/sent/disabled states can overwrite newer state | Add status precedence and `version_id` guard. Terminal/success states should not be overwritten by stale lower-precedence states. |
| `trade_delivery_receipts` | sync | `dedupe_key`, terminal-state guard | Worker-local fields `worker_id` and `lease_until` are cross-synced even though lease execution is local-authority; non-terminal ordering risk remains | Exclude or localize `worker_id` and `lease_until` from cross-server overwrite, or guard by `destination_server`. Keep terminal-state precedence. Add source-sequence watermark. |

### Notification Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `notifications` | sync | Unique partial `dedupe_key` exists in DB; receiver currently uses id upsert, not dedupe-first | Duplicate notification or stale read-state overwrite risk; `is_read=true` can be reverted to false | Receiver should upsert by `dedupe_key` when present. `is_read` should be monotonic true-wins, unless explicit unread feature is introduced with version. Add source-sequence watermark. |
| `push_subscriptions` | no-sync | Iran local browser runtime | Correct to exclude. Syncing would leak browser endpoints and create invalid foreign push runtime | No cross-server sync. Add CI assertion that no event listener or registry sync enables this table. |

### Messenger And Upload Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `chats` | no-sync with transitional mandatory-channel receiver exception | Policy rejects arbitrary messenger sync; mandatory projection is special-cased | Transitional path can become a loophole if not tested against arbitrary chat payloads | Keep no-sync. Add explicit tests that only mandatory system channel projection is accepted and arbitrary messenger rows are rejected. |
| `chat_members` | no-sync with transitional mandatory-channel receiver exception | Same as `chats` | Same loophole risk | Keep no-sync. Add explicit receiver-policy tests. |
| `messages` | no-sync | Iran local messenger runtime | Correct to exclude | No change except CI assertion. |
| `conversations` | no-sync | Iran local messenger runtime | Correct to exclude | No change except CI assertion. |
| `chat_files` | no-sync | Iran local upload/media runtime | Correct to exclude | No change except CI assertion. |
| `upload_batches` | no-sync | Iran local upload runtime | Correct to exclude | No change except CI assertion. |
| `upload_sessions` | no-sync | Iran local upload runtime | Correct to exclude | No change except CI assertion. |

### Auth And Session Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `user_sessions` | no-sync | Local auth runtime | Correct to exclude. Cross-syncing sessions would violate home-server-aware session authority | No cross-server sync. Keep home-server-aware reset/authority logic. |
| `session_login_requests` | no-sync | Local login runtime | Correct to exclude | No change. |
| `single_session_recovery_requests` | no-sync | Local recovery runtime | Correct to exclude | No change. |
| `single_session_recovery_admin_targets` | no-sync | Local recovery runtime | Correct to exclude | No change. |

### Bookkeeping Tables

| Table | Policy | Current protection | Remaining risk | Required change |
| --- | --- | --- | --- | --- |
| `change_log` | internal-bookkeeping | Local outbox only | `verified=true` name is misleading: currently means peer accepted, not row parity verified | Rename semantics in docs/UI or add real verification columns. Do not use current `verified` as parity proof. |
| `sync_blocks` | internal-bookkeeping | Local sync audit/blocking | Not cross-synced | No cross-server sync. Can be reused for parity snapshots if expanded. |
| `alembic_version` | migration bookkeeping | Not in registry; correct | Not a product sync table | No sync. Add inventory test that migration tables are explicitly exempt. |

## Cross-Cutting Required Changes

### 1. Receiver Coverage Must Be Enforced By Tests

Add CI tests that fail if:

- any `SyncPolicy.SYNC` table is missing from `get_model_class()`;
- any `SyncPolicy.SYNC` table is missing from `TABLE_ORDER`;
- any integer-id synced table is missing from sequence partition alignment if it can be locally inserted on both servers;
- any no-sync table has normal receiver acceptance except explicit mandatory-channel compatibility for `chats` and `chat_members`;
- any event listener emits `log_change()` for a table whose receiver is not enabled.

This would have caught `user_notification_preferences`.

### 2. Add Generic Source-Sequence Watermarks

Create a small internal table such as `sync_apply_watermarks`:

- `id`
- `source_server`
- `table_name`
- `aggregate_key`
- `last_outbox_id`
- `last_event_sequence`
- `last_payload_hash`
- `last_applied_at`

Receiver rule:

- build `aggregate_key` using existing `sync_meta.aggregate_id` or public identity;
- if incoming event sequence/outbox id is older than the watermark for `(source_server, table_name, aggregate_key)`, ignore it as stale;
- if equal with same hash, treat as idempotent success;
- if equal with different hash, raise conflict;
- after successful apply, update the watermark atomically in the same DB transaction.

This prevents the exact class of bug where an older event arrives after a newer event from the same source.

Required addition:

- sync payload must include an explicit `source_server` in `sync_meta`; do not infer it only from authority fields.

### 3. Add Table-Specific State Machines

Generic watermarks prevent older same-source events. They do not fully solve cross-source conflicts or bad state transitions. Each sensitive table needs invariants:

- relation terminal states cannot be overwritten by pending/active;
- resolved relation link ids cannot be cleared by stale non-terminal rows;
- used/revoked tokens and used invitations cannot become pending/unused;
- deleted/deactivated users cannot be resurrected by stale generic updates;
- notification read state should be monotonic true-wins;
- offer/publication/request/trade terminal states must remain terminal except explicit allowed replay;
- delivery receipt terminal states must not be reopened by retry state.

### 4. Add Field-Level Authority For `users`

`users` is the most dangerous shared table because many surfaces write to it.

Receiver must distinguish:

- admin authority fields: role, account status, max limits, deletion/deactivation, password policy;
- Telegram authority fields: `telegram_id`, bot link state;
- profile fields: account name, full name, address, avatar;
- counters: trades count, commodities count, channel messages count.

Each group needs its own merge rule. A single `set_=excluded` upsert is too broad for real parity guarantees.

### 5. Add Runtime Parity Checker

Add a parity checker that runs from one server and compares both databases through safe internal endpoints or signed SQL-report endpoints.

It must produce:

- row count per synced table;
- per-table stable checksum excluding local-only/volatile fields;
- focused invariant checks;
- sample mismatches with primary/public identity;
- severity classification.

Suggested modes:

- quick: every 1-5 minutes for critical tables and counts;
- deep: nightly or manual before/after production deploy;
- targeted: table/record repair verification.

Critical invariant checks:

- active `customer_relations` must not have `customer_user_id IS NULL`;
- active `accountant_relations` must not have `accountant_user_id IS NULL`;
- no active offer without required publication state on the relevant surface;
- no terminal offer reactivated on one server;
- completed trades must match by `trade_number`;
- delivery receipt terminal counts must match by `dedupe_key`;
- user deletion/deactivation must match across servers for synced users;
- user block pairs must match.

### 6. Change Health Semantics

Current `/api/sync/health` should keep reporting backlog health, but must not be treated as parity health.

Add fields:

- `delivery_backlog_status`;
- `parity_status`;
- `last_parity_check_at`;
- `parity_findings_by_table`;
- `critical_drift_count`;
- `stale_event_ignored_count`.

Only when both delivery and parity are clean should operators call cross-server sync healthy.

### 7. Add Repair Tooling

Add signed, operator-only repair tools:

- dry-run parity report;
- replay current state for one table/record from authoritative server;
- replay by public identity/dedupe key;
- mark/retry failed change_log only after peer apply verification;
- repair watermarks after confirmed manual repair.

The repair tool must always show:

- source server;
- target server;
- table;
- record identity;
- before state hash;
- after state hash;
- exact rows changed.

## Roadmap

### Stage 0 - Documentation And Current Drift Audit

Goal: know current risk before changing code.

Actions:

- run read-only parity spot checks on both production DBs for all synced tables;
- check active relation null-link invariants;
- check `user_notification_preferences` backlog and change_log state;
- check `notifications` duplicate `dedupe_key` state;
- check `user_blocks` pair parity;
- document findings.

Exit criteria:

- no known active drift remains, or all drift is listed with repair plan;
- this document is current.

### Stage 1 - Receiver Coverage Hardening

Goal: prevent registry/receiver mismatch.

Historical actions:

- add `user_notification_preferences` to `TABLE_ORDER`;
- add `UserNotificationPreference` to `get_model_class()`;
- add sequence alignment if needed;
- add receiver upsert by `user_id`;
- add tests for registry coverage, receiver model coverage, and event listener coverage.
- add tests that every `SyncPolicy.SYNC` table is either receiver-enabled or explicitly exempted in one place.
- add tests that every event listener table is receiver-enabled or explicitly no-sync.

Current status on `candidate/sync-parity-hardening`:

- `user_notification_preferences` is receiver-enabled and uses `user_id` as
  its logical identity.
- The receiver upsert has a strict `updated_at` recency guard: incoming
  `updated_at = NULL` cannot overwrite an existing non-null timestamp.
- Coverage is locked by the receiver parsing/coverage tests, apply-item SQL
  tests, fail-closed policy tests, and the sync guarantee matrix.

Exit criteria:

- every `SyncPolicy.SYNC` table has receiver coverage or is explicitly exempt with failing test if changed;
- no-sync tables are rejected by receiver policy except documented mandatory-channel projection.

### Stage 2 - Offer Payload And Legacy Identity Drift Hardening

Goal: stop newly-created offer drift and plan safe historical repair before deeper parity enforcement.

Actions:

- add `exclude_from_competitive_price` and `price_warning_type` to `build_offer_sync_payload()`;
- add unit tests proving those fields survive sync payload generation and receiver apply;
- add a parity check that compares offers by stable historical key for legacy rows and by `offer_public_id` for new rows;
- design a dry-run repair plan for historical `offer_public_id` drift, including dependent tables that may reference offer public ids;
- ensure future migrations do not random-generate shared identities independently on each server.

Exit criteria:

- new offer payloads cannot lose competitive-warning state across servers;
- historical `offer_public_id` drift is visible and has a safe repair/exemption plan;
- `channel_message_id` remains explicitly local-only and excluded from business parity hashes.

### Stage 3 - Generic Source-Sequence Watermark

Goal: prevent out-of-order stale events for every synced table.

Actions:

- add `source_server` to sync metadata;
- add `sync_apply_watermarks` migration/model;
- compute logical aggregate key for all synced tables;
- enforce stale/equal/conflict handling before applying rows;
- update watermark atomically after successful apply;
- log and metric ignored stale events.

Exit criteria:

- replaying events out of order cannot overwrite a newer event from the same source;
- duplicate replay is idempotent;
- equal sequence with different hash is a conflict.

### Stage 4 - Table-Specific Invariants

Goal: protect business truth beyond generic ordering.

Actions:

- `users`: field-level merge and terminal deletion/deactivation guards;
- `accountant_relations` and `customer_relations`: full lifecycle state machine;
- `invitations`: used/expired terminal guard;
- `telegram_link_tokens`: used/revoked terminal guard;
- `notifications`: dedupe-first upsert and read-state monotonic rule;
- `user_blocks`: soft-delete/tombstone or pair-watermark model;
- `market_runtime_state`: `last_transition_at` guard;
- `offer_publication_states`: status precedence and `version_id` guard;
- `trade_delivery_receipts`: protect/localize lease fields.

Exit criteria:

- every sync table has an explicit merge rule in code and tests;
- dangerous `set_=excluded` paths are limited to append-only or watermarked tables.

### Stage 5 - Parity Checker

Goal: prove row-state parity, not only queue health.

Actions:

- implement quick parity report for critical tables;
- implement deep parity report for all `SyncPolicy.SYNC` tables;
- define stable hash columns per table;
- exclude legitimate local-only fields;
- add signed operator endpoint or script;
- integrate findings into `/api/sync/health`.

Exit criteria:

- `sync health` exposes both delivery and parity status;
- parity mismatch is visible even when queues are empty;
- operator can request table-level and record-level mismatch details.

### Stage 6 - Repair And Replay Tools

Goal: safe recovery from detected drift.

Actions:

- create dry-run repair command;
- create table/record current-state replay command;
- support public identities: offer public id, trade number, dedupe key, relation id/token, user id/mobile;
- require signed/operator-only execution;
- always log before/after checksums.

Exit criteria:

- detected drift can be repaired without ad hoc SQL;
- repairs are auditable and replay-safe.

### Stage 7 - Test Matrix For Sync Guarantees

Goal: prevent regression.

Actions:

- add unit tests for stale event ordering per table family;
- add integration tests for out-of-order delivery;
- add parity checker tests with intentional drift fixtures;
- add full matrix scenarios where events are delayed/reordered/deduplicated;
- include short outage and medium outage replay scenarios.

Exit criteria:

- CI fails if an unguarded sync table is added;
- CI fails if a stale event can overwrite newer state;
- CI fails if parity checker misses injected drift.

### Stage 8 - Staging Rollout

Goal: verify under realistic two-server conditions.

Actions:

- deploy to staging;
- run targeted out-of-order sync tests;
- run full candidate matrix without load first;
- run parity checker before/during/after tests;
- manually test Telegram/WebApp offer, trade, expiry, relation, block, and notification flows.

Exit criteria:

- no parity findings after full matrix;
- ignored stale events are expected and audited;
- no backlog remains after test completion.

### Stage 9 - Production Rollout

Goal: introduce guarantee safely.

Actions:

- take DB backups;
- run pre-deploy read-only parity report;
- deploy migrations and code;
- run post-deploy parity report;
- keep parity checker in warning-only mode briefly;
- then enable alerting/fail-closed behavior for critical drift.

Exit criteria:

- both servers report delivery clean and parity clean;
- no critical drift exists;
- repair tooling is available before alerting is enforced.

## Definition Of Real Guarantee

The project can claim real protection against this class of bug only when all are true:

- every synced table has receiver coverage;
- every synced table has a stale-event ordering rule;
- every business-sensitive table has a table-specific invariant;
- every synced table's payload includes all business fields or documents field-level local/volatile exclusion;
- `/api/sync/health` reports parity status, not only backlog status;
- parity checker can detect intentionally injected drift;
- repair tooling can fix targeted drift without manual SQL;
- CI blocks adding a new synced table without registry, receiver, ordering, parity hash, and tests.

Until then, the current system is improved but not mathematically or operationally guaranteed across all tables.
