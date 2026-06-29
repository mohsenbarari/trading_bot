# Sync Parity Hardening Branch Review

Repository: `mohsenbarari/trading_bot`
Branch reviewed: `candidate/sync-parity-hardening`
Review scope: read-only code, docs, tests, commit stats, and repository-search inspection.
Reviewer output path: `tmp/sync_parity_hardening_agent_review.md`

## Executive verdict

**Go/no-go recommendation: NO-GO for merge to `main` as-is.**

This branch materially improves the cross-server sync story. It adds policy registry checks, a runtime parity checker, source-sequence watermarks, table-specific stale/terminal-state guards, dry-run repair tooling, a guarantee matrix, and staging/production rollout gates. Those are meaningful steps toward detecting and reducing silent drift.

However, it does **not yet fully solve silent sync drift**. The highest-risk issue is an implementation bug in sync watermark aggregation for `offer_requests`: metadata construction can aggregate multiple independent request rows by `offer_public_id` instead of by the request identity. A valid older-sequence request for the same offer can be classified as stale and skipped, while the source later marks the delivery as accepted. That is exactly the class of silent drift this branch is meant to prevent.

The branch is **staging-preflight-ready** and useful for non-mutating staging validation. It is **not merge-ready** until the P0/P1 issues below are fixed and covered by tests.

## Reviewed commits

The requested branch is 9 commits ahead of `main`, not 8. The extra commit is relevant because it fixes part of the documented `user_notification_preferences` receiver gap.

| Commit | Subject | Review note |
|---|---|---|
| `69b75c7d` | Enable user notification preference sync receiver | Not in the requested list, but part of this branch. Adds receiver/model/order/natural-key handling for `user_notification_preferences`. |
| `bc837f7b` | Preserve offer warning sync metadata | Good: extends canonical offer payload with warning/expiry metadata and adds drift reporting/tests. |
| `d4467164` | Add sync apply watermarks | Useful, but unsafe for some tables until aggregate identities are verified table-by-table. |
| `14366102` | Harden table-specific sync invariants | Adds needed terminal/stale guards, but fallback paths still need scrutiny. |
| `0f1fff1e` | Add runtime sync parity checker | Useful detector, but can false-negative on truncation and false-positive on local IDs/FKs. |
| `71f44613` | Add dry-run sync repair tooling | Good dry-run foundation. Apply mode is less guarded than production rollout gates. |
| `ab67406d` | Add sync guarantee matrix coverage | Valuable coverage matrix, but it proves presence/shape more than semantic correctness. |
| `510d378d` | Add sync parity stage8 rollout gate | Good staging planning/preflight/explicit-confirm structure. |
| `c89c0315` | Add sync parity production rollout gate | Stronger production gate separation; still depends on parity correctness. |

### Commit stat equivalents reviewed

I inspected `git show --stat` equivalents via GitHub compare for each commit:

- `69b75c7d`: `api/routers/sync.py` +43/-11; `tests/test_sync_coverage.py` +47; `tests/test_sync_router_apply_item_success.py` +43/-2; `tests/test_sync_router_fail_closed_policy.py` +5/-17; `tests/test_sync_router_parsing.py` +2.
- `bc837f7b`: `core/offer_sync_payload.py` +2; roadmap docs +19; `scripts/report_offer_public_id_drift.py` +247; migration smoke +16; offer drift/payload/router tests +189 total.
- `d4467164`: `api/routers/sync.py` +292/-3; config/events/metadata/worker changes; watermark migration/model; watermarks tests +170.
- `14366102`: `api/routers/sync.py` +253/-8; `core/events.py` +6/-1; `core/sync_metadata.py` +5; table-specific router tests +180.
- `0f1fff1e`: parity endpoint/helper/script; `core/sync_parity.py` +384; `scripts/compare_sync_parity.py` +114; parity/health/script tests.
- `71f44613`: `core/sync_repair.py` +331; `scripts/sync_repair_tool.py` +204; repair/tool tests.
- `ab67406d`: guarantee matrix test +552.
- `510d378d`: Stage 8 rollout gate script +707 and tests +155.
- `c89c0315`: Stage 9 rollout gate script +733 and tests +177.

Branch-level stat equivalent against `main`: 38 changed files, including `api/routers/sync.py` +630/-22, new parity/repair modules, one watermark migration, Stage 8/9 scripts, and substantial tests.

## Reviewed documents

Reviewed:

- `docs/CROSS_SERVER_SYNC_PARITY_AUDIT_AND_ROADMAP.md`
- `docs/CROSS_SERVER_SYNC_PARITY_IMPLEMENTATION_ROADMAP.md`
- `docs/BOT_WEBAPP_INTEGRATION_IMPLEMENTATION_CONTRACT.md`
- `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md`

Document consistency findings:

1. The audit correctly identifies the old failure mode: sync health/backlog can be green while database parity is not proven.
2. The branch implements much of the implementation roadmap: receiver coverage, outbox guard, offer public identity, watermarks, parity snapshots, repair dry-runs, staging gate, and production gate.
3. Some docs are now stale. `core/sync_registry.py` still describes `user_notification_preferences` as having merge TBD before receiver enablement, but the branch now enables receiver behavior in `69b75c7d`.
4. The roadmap says production repair is intentionally not automated. That is mostly true for the Stage 9 gate, but `scripts/sync_repair_tool.py replay-row --apply` can still write to a target outside Stage 9 with only `--confirm-write` and `--source-sequence`.
5. The docs treat parity as a rollout gate, but parity implementation limitations mean the gate is not yet a complete business-parity proof.

## What is correctly implemented

### 1. Messenger data remains excluded by policy

`core/sync_registry.py` classifies messenger/runtime tables such as `chats`, `chat_members`, `messages`, `chat_files`, upload/session runtime tables, and push subscription raw runtime as `NO_SYNC`. `tests/test_sync_coverage.py` asserts event-listener tables either have receiver coverage or are explicitly non-sync/internal bookkeeping.

This is aligned with the production topology:

- Iran server: WebApp only, never Telegram.
- Foreign server: Telegram bot only, never WebApp.
- Messenger data must not be cross-synced.

### 2. Durable outbox coverage is stronger than before

`core/events.py::log_change()` inserts a `change_log` row in the same DB transaction before pushing Redis/direct HTTP. `core/sync_outbox_guard.py` adds a pending-write guard that raises if a synced authoritative ORM write does not get a durable outbox row. It also blocks bulk/raw ORM writes against synced tables unless explicitly marked as sync execution.

This is a real hardening step: direct Redis/HTTP push can still fail, but durable `change_log` remains the source of truth for retry.

### 3. Receiver coverage is materially improved

`api/routers/sync.py` now registers and orders all active `SyncPolicy.SYNC` tables, including the previously missing `user_notification_preferences`. The receiver supports public/natural identity resolution for key tables such as offers, offer requests, trades, trade delivery receipts, offer publication states, Telegram link tokens, and notification preferences.

The guarantee matrix checks that every active sync table has:

- registry coverage,
- model receiver coverage,
- `TABLE_ORDER` coverage,
- parity identity coverage,
- repair identity coverage,
- a named rule family.

### 4. Offer warning/expiry metadata is now in canonical payloads

`core/offer_sync_payload.py` includes `price_warning_type`, expiry reason/user/actor/source fields, `expired_at`, and other offer state fields. This addresses a real audit drift category: offer warning/expiry fields would previously not necessarily be present in replay payloads.

### 5. Table-specific terminal/stale-state guards were added

The receiver has additional special-case logic for critical state machines:

- offers: public identity/version/terminal status guards,
- offer requests: request-home/idempotency conflict key and version/status guard,
- trades: trade-number identity and completed-trade protection,
- trade delivery receipts: dedupe-key and terminal-state precedence,
- offer publication states: dedupe-key/version/status precedence,
- relations: stale-null-link protection,
- Telegram link tokens: token hash/terminal-state behavior,
- user notification preferences: user-id identity and updated-at guard.

The stale-event tests cover a number of these cases, especially offers, offer requests, trades, and linked relations.

### 6. Runtime parity snapshots are useful and redacted

`core/sync_parity.py` builds redacted table snapshots, uses stable identity hashes, separates business/local-only/volatile hashes, and exposes quick/deep table modes through `/api/sync/parity/snapshot` with observability auth.

Good aspects:

- Sensitive identity values such as tokens/mobile fields are not exposed as labels.
- `offers.channel_message_id` is local-only.
- `trade_delivery_receipts.worker_id` and `lease_until` are excluded from replay payloads and classified as no-sync by field policy.
- Quick mode covers major product/business tables; deep mode covers all active sync tables.

### 7. Stage 9 production execution is blocked on candidate branches

`run_sync_parity_stage9_production_rollout.py` allows planning from candidate or main, but production `execute` requires `current_branch == "main"`. It also requires separate preflight, backup, and release confirmation environment variables. That separation is good.

### 8. Backup/preflight/release confirmation separation is good

Stage 9 separates:

- local release gates,
- production read-only preflight,
- backup creation,
- production release,
- post-deploy checks,
- warning-only alert window,
- future strict alert enablement.

This is the right operational shape.

## What is partially implemented

### 1. Watermarks reduce stale replay, but do not yet safely cover every table

The watermark model is appropriate in principle: `(source_server, aggregate_table, aggregate_key)` stores the last source sequence/payload hash and rejects stale or conflicting sequence replays.

However, safety depends entirely on correct `aggregate_key` construction for every table. The branch does not have table-by-table tests proving aggregate identity for every synced table. There is a concrete bug for `offer_requests` described below.

Also, compatibility behavior is permissive:

- If metadata is absent, malformed, or missing source sequence, the receiver applies the item.
- If watermark evaluation/persistence fails and `sync_watermark_strict_mode` is false, the receiver applies/continues in compatibility mode.
- `sync_watermark_strict_mode` defaults to false.

That is understandable for rolling deploy, but it means source-sequence watermarks are not yet a hard guarantee.

### 2. Parity checker measures drift, but does not prove full parity

The parity checker is a good detector and rollout artifact. It does not solve drift by itself and currently has both false-positive and false-negative paths:

- It can false-negative when snapshots are truncated because `truncated` is included in table snapshots but not treated as a comparison failure.
- It can false-positive when local DB IDs or local FK IDs differ even though public business identity matches.
- It may mark volatile differences as non-business only for fields currently configured; other runtime timestamps/counters may remain in business hashes.

### 3. Repair tooling is dry-run-first, but apply mode is not production-grade guarded

The repair module and CLI are useful for generating replay plans and current-state replay payloads. They are not yet safe enough to be treated as general production repair automation.

Concerns:

- `REPLAY_IDENTITY_FIELDS_BY_TABLE` permits `id` for many cross-server tables, even where IDs can differ by server.
- The repair plan emits `identity_hash`, not an executable canonical identity object, so operator translation is required and error-prone.
- `replay-row --apply` requires `--confirm-write` and `--source-sequence`, but does not enforce branch, environment, backup evidence, target role, or exact typed production confirmation like Stage 9.

### 4. Stage 8 is useful, but not a complete pre-merge proof

Stage 8 is fail-closed by default and useful for staging. But its synthetic matrix and parity snapshots do not prove production-scale parity, do not cover all legacy drift shapes, and inherit parity-checker limitations.

## What is missing

### P0/P1 missing tests

1. **No table-by-table aggregate identity tests for watermarks.** The tests cover a simple `users` aggregate, but not `offer_requests`, trades, receipts, publication states, user blocks, relations, tokens, or notification preferences.
2. **No test proving two independent `offer_requests` for the same `offer_public_id` but different idempotency keys can both apply out of order.** This is the concrete silent-drift bug.
3. **No parity truncation-fail test.** A truncated snapshot can compare as clean for the first N rows while hiding drift beyond the row cap.
4. **No test that parity excludes or normalizes cross-server-local IDs/FKs.** Current hashes include many raw IDs/FKs as business fields.
5. **No repair apply safety tests for production/candidate branch blocking.** Stage 9 is guarded; `sync_repair_tool.py --apply` is not guarded equivalently.
6. **No null-safe `user_notification_preferences.updated_at` stale guard test.** The upsert uses `updated_at <= excluded.updated_at`; null/absent timestamps need explicit behavior.
7. **No generic natural-key fallback safety tests for all special guarded tables.** The duplicate-key fallback can bypass some table-specific invariant checks outside the explicitly covered trade path.

### Missing operational controls

1. No production rollback command is implemented in Stage 9; the gate requires backups and post-deploy checks but does not provide an explicit rollback execution path.
2. Strict alert enablement is a plan object, not an executable guarded command.
3. Parity drift after deploy is warning-only in the initial window, but there is no automated requirement to attach repair/rollback decision evidence before completing the window.
4. There is no explicit production freeze/quiescence gate before parity snapshots. Near-real-time writes can cause benign mismatches during snapshot capture.

## Bugs or likely bugs

## P0: `offer_requests` watermark aggregate identity is wrong and can silently drop valid rows

### Evidence

`core/sync_metadata.py::_aggregate_identity()` handles `offer_publication_states`, `trade_delivery_receipts`, `user_blocks`, then has:

```python
if table_name in {"offers", "offer_requests"}:
    public_id = _string_or_none(data.get("offer_public_id"))
    if public_id:
        return public_id
```

For `offer_requests`, this is wrong. The durable identity is `request_home_server + idempotency_key`, which the parity checker and public identity builder mostly recognize elsewhere.

### Blast radius

If two independent offer requests exist for the same offer:

- request A: source sequence 100, idempotency key A,
- request B: source sequence 101, idempotency key B,
- both have the same `offer_public_id`,

then the receiver watermark key can be the same aggregate (`offer_requests`, `offer_public_id`). If B arrives first, the watermark stores sequence 101. When A arrives later, it is classified as stale and skipped. The receiver increments processed count for stale items. The sync worker then treats the peer response as successful and marks the local `change_log` delivered. The target never receives request A.

This is silent business drift in a critical ledger table.

### Safe fix strategy

- Change `_aggregate_identity()` for `offer_requests` to prefer `request_home_server:idempotency_key` before `offer_public_id`.
- Only fall back to `offer_public_id` for legacy payloads if no request-home/idempotency identity exists, and log/metric that legacy fallback occurred.
- Add tests:
  - `build_sync_metadata("offer_requests", ...)` uses `request_home_server:idempotency_key`.
  - Two same-offer/different-idempotency requests with sequences 100/101 both apply even when delivered out of order.
  - Stale detection remains per request, not per offer.
- Add one matrix assertion that every table's watermark aggregate key equals the parity/repair/public identity policy for that table.

## P1: Parity comparison ignores truncation

`build_table_parity_snapshot()` sets `truncated=True` when row count exceeds `max_rows_per_table`, but `compare_parity_snapshots()` compares only available records and does not fail or warn on truncation.

### Blast radius

A production deep snapshot with 10,000 rows capped at 5,000 can show `ok` even when rows 5,001+ differ. This creates a false sense of production readiness.

### Safe fix strategy

- Treat any truncated table as at least `incomplete_snapshot` / warning, and fail production rollout gates unless `max_rows_per_table` covers full row counts or an explicit override is supplied.
- Include `truncated_tables` in compare output.
- Add tests for local-only truncation, peer-only truncation, and both-truncated cases.

## P1: Parity business hashes include many local IDs/FKs

The parity classifier currently places any field not configured as local-only/no-sync/volatile into the business hash. For public-identity synced tables, raw DB IDs and local FK IDs can differ cross-server even when business identity is correct.

High-risk examples:

- `offers.id`, `offers.user_id`, `offers.commodity_id`, `offers.republished_offer_id`,
- `offer_requests.id`, `local_offer_id`, `resulting_trade_id`, `customer_relation_id`,
- `trades.id`, `offer_id`, user IDs if users are not guaranteed ID-identical,
- `trade_delivery_receipts.id`, `trade_id`, `offer_id`, `notification_id`,
- `offer_publication_states.id`, `offer_id`, Telegram-specific resource IDs.

### Blast radius

Stage 8/9 parity can fail on benign local identity differences, causing noise and manual overrides. Conversely, operators may learn to ignore parity reports, weakening the gate.

### Safe fix strategy

- Define table-specific parity business columns, not implicit all-column hashing.
- Separate `reference_hash` from `business_hash`, or normalize references to public identities (`offer_public_id`, `trade_number`, dedupe keys) before hashing.
- Add parity fixture tests where DB IDs differ but public identity/business content matches.

## P1: Watermark compatibility mode is too permissive for final rollout

`sync_watermark_strict_mode` defaults to false. If watermark evaluation/persistence fails, the receiver logs and applies/continues. Missing metadata also causes compatibility apply.

### Blast radius

Rolling deploy compatibility is necessary, but after rollout this can reintroduce out-of-order drift under DB/migration/config failure. A migration missing on one server may be treated as warning-only while stale writes apply.

### Safe fix strategy

- Keep compatibility mode during explicit Stage 8/early Stage 9 only.
- Add a Stage 9 post-warning-window strict-mode gate.
- Fail production rollout if any `sync.watermark_compatibility_apply` or `sync.watermark_persist_failed` events occur after both servers are upgraded.

## P1: Repair apply path is less guarded than production rollout

`sync_repair_tool.py replay-row --apply` requires `--confirm-write` and `--source-sequence`, but it is not tied to the Stage 9 confirmation model. It can send a signed `/api/sync/receive` item to any supplied `--target-url` with any `--source-server` choice.

### Blast radius

An operator can replay the wrong row, wrong direction, or wrong source sequence into production without the backup/preflight/release separation that Stage 9 enforces.

### Safe fix strategy

- Add `--dry-run` default explicitly and rename apply mode to something harder to invoke, e.g. `--apply-after-backup-and-ticket`.
- Require environment-specific confirmation strings for production.
- Require target server role, source role, branch, release SHA, backup artifact ID, and parity evidence file.
- Disallow `id` as replay identity for tables where public identity exists unless `--allow-db-id-identity` is explicitly set and logged.

## P2: `user_notification_preferences` registry notes are stale and merge rule is null-sensitive

The registry still says receiver behavior is not enabled and merge is TBD, but the branch enables receiver behavior. The upsert guard depends on incoming `updated_at`; if absent, it falls back to unguarded conflict update.

### Safe fix strategy

- Update registry notes to reflect current behavior.
- Make the guard null-safe, e.g. require `updated_at` for sync payloads or use `COALESCE`/server sequence/version.
- Add tests for stale preference update with absent/null `updated_at`.

## P2: Natural-key fallback may bypass table-specific invariants

The receiver has generic duplicate-key fallback behavior for natural-key tables. Some special guards are applied before fallback for trades, but the same level of proof is not visible for all tables.

Risk tables:

- `telegram_link_tokens`,
- `offer_publication_states`,
- `trade_delivery_receipts`,
- `user_notification_preferences`,
- `notifications`,
- `accountant_relations` / `customer_relations`,
- `user_blocks`.

### Safe fix strategy

- Add per-table fallback tests proving duplicate-key fallback preserves the same where-clause/terminal-state guard semantics as the primary upsert.
- For critical terminal-state tables, prefer `ON CONFLICT` by natural key directly and avoid generic fallback updates.

## P2: `trading_settings` special handling needs stronger ordering/authority semantics

`trading_settings` is sync-enabled and has a special key handler. It should be single-writer/admin-authority. There is not enough evidence that stale/out-of-authority settings changes are blocked by source sequence or updated-at semantics for all operations.

### Safe fix strategy

- Use key as aggregate identity for watermarks.
- Reject or explicitly handle DELETE.
- Add stale/out-of-order settings tests.

## Production safety concerns

### Candidate branch production execution

Stage 9 correctly blocks `execute` unless the branch is `main`. This is good and should remain.

### Production read-only preflight from candidate

Stage 9 allows read-only preflight from candidate if explicitly confirmed. That is acceptable, provided operators understand that parity snapshot code itself is candidate code and may have false-positive/false-negative behavior.

### Backup/preflight/release separation

The separation is good. Production execution requires separate confirmations for preflight, backup, and release. Backup command includes restore-smoke. This is stronger than typical ad-hoc scripts.

### Remaining production risk

The production gate's correctness depends on parity checker correctness. Because the parity checker currently ignores truncation and hashes local IDs/FKs as business data, the production gate can either miss drift or block on benign differences.

### Warning-only alerts and rollback

The Stage 9 warning-only alert window is appropriately conservative, but there is no executable rollback command or strict alert activation command. Strict alert enablement is a plan/reservation, not an implemented fail-closed transition.

## Test coverage assessment

### Strong coverage

- Registry/model/order coverage for active sync tables.
- Event listener table coverage against registry policy.
- Offer payload includes added warning/expiry fields.
- Source-sequence watermark decisions for apply/stale/duplicate/conflict.
- Offers terminal-state protection.
- Offer requests conflict key/version guard shape.
- Completed trade guard paths.
- Relation stale-null-link guard.
- Basic parity business/local-only/volatile/missing-row detection.
- Repair dry-run payloads and local-only field dropping.
- Stage 8 and Stage 9 fail-closed contracts.

### Weak or missing coverage

- Watermark aggregate identity table-by-table.
- Multi-request same-offer `offer_requests` replay ordering.
- Parity truncation failure.
- Parity normalization for public identity vs DB IDs/FKs.
- Production repair apply safety.
- User notification preference null/absent timestamp stale update.
- Fallback duplicate-key behavior for every terminal-state table.
- `offer_publication_states` terminal/state precedence beyond compile-level coverage.
- Trade delivery receipt terminal precedence and local lease non-overwrite beyond simple matrix checks.
- `telegram_link_tokens` stale/reuse/terminal token behavior under out-of-order replay.
- `user_blocks` pair identity out-of-order insert/delete semantics.

## Rollout safety assessment

### Stage 8 staging gate

Verdict: **useful and safe for staging preflight**, not sufficient as a merge gate alone.

Good:

- Default mode is plan-only.
- Branch gate requires `candidate/sync-parity-hardening`.
- Mutating staging execution requires explicit environment confirmation.
- Uses synthetic prefixes and cleanup dry-run checks.
- Runs local guarantee matrix and stale/watermark tests.
- Captures quick/deep parity snapshots before/after execution.

Gaps:

- It does not compare every business path under real production cardinalities.
- It inherits parity checker limitations.
- Optional controlled load is not required.
- Manual checks are still required for surface-specific behavior.

### Stage 9 production gate

Verdict: **operationally well-structured but not safe enough to compensate for code issues.**

Good:

- `execute` requires `main`.
- Preflight/backup/release confirmations are separate.
- Backups and restore-smoke are part of the backup phase.
- Pre/post deploy parity and health checks exist.
- Warning-only alert window is explicit.

Gaps:

- No implemented rollback command.
- Strict alert enablement is not executable.
- Parity checker can misclassify.
- Production read-only parity snapshots can race with live writes.
- Repair remains manual and the separate repair CLI has weaker apply controls.

## Specific recommendations ordered by severity

### P0 - Fix before merge

1. **Fix `offer_requests` aggregate identity in `core/sync_metadata.py::_aggregate_identity()`.** Prefer `request_home_server:idempotency_key`; only legacy-fallback to `offer_public_id` with an explicit compatibility metric/log.
2. **Add out-of-order multi-request tests.** Same `offer_public_id`, different `idempotency_key`, sequences delivered newest-first must both apply.
3. **Make parity truncation fail or warn hard.** Production rollout compare must not return `ok` when either side is truncated.

### P1 - Fix before production rollout; preferably before merge

4. **Define table-specific parity business field policies.** Do not implicitly hash raw DB IDs/FKs as business truth.
5. **Add per-table watermark identity tests.** Cover all active sync tables in the guarantee matrix.
6. **Harden `sync_repair_tool.py --apply`.** Add production branch/env/backup/evidence confirmations and disallow DB `id` identity unless explicitly overridden.
7. **Plan strict watermark mode transition.** Stage 9 should include a post-warning-window gate that fails on compatibility watermark logs.
8. **Add null-safe stale tests and behavior for `user_notification_preferences.updated_at`.**

### P2 - Fix soon

9. **Update stale registry/docs text for `user_notification_preferences`.** Current registry notes contradict implementation.
10. **Strengthen natural-key fallback tests for every special table.** Make fallback behavior as guarded as primary upsert.
11. **Add rollback execution plan.** At minimum, a guarded plan command that records exact rollback steps/artifacts.
12. **Add production snapshot quiescence guidance.** Either freeze high-volume writes briefly or take repeated snapshots and require stability.
13. **Add warning-only alert completion criteria.** Require evidence files for accepted differences and a decision record before strict mode.

## Concrete file/function references

- `core/sync_metadata.py::_aggregate_identity()` - likely P0 bug for `offer_requests` aggregate key.
- `core/sync_metadata.py::build_sync_metadata()` - source sequence, aggregate identity, authority metadata.
- `models/sync_apply_watermark.py::SyncApplyWatermark` - unique watermark key by source/table/aggregate.
- `api/routers/sync.py::_sync_watermark_context_from_item()` - metadata/fallback construction.
- `api/routers/sync.py::_evaluate_sync_watermark()` - stale/duplicate/conflict decision and compatibility fallback.
- `api/routers/sync.py::_record_sync_watermark_applied()` - watermark persistence compatibility behavior.
- `api/routers/sync.py::receive_sync_data()` - stale items counted as processed; conflicts returned as partial errors.
- `api/routers/sync.py::_build_upsert_stmt()` - table-specific upsert guards.
- `api/routers/sync.py::_apply_item()` - table-specific guard execution and fallback behavior.
- `core/sync_parity.py::build_table_parity_snapshot()` - truncation flag generation.
- `core/sync_parity.py::compare_parity_snapshots()` - currently does not fail on truncation.
- `core/sync_repair.py::REPLAY_IDENTITY_FIELDS_BY_TABLE` - permits `id` identities broadly.
- `core/sync_repair.py::build_current_state_replay_item()` - replay payload and metadata construction.
- `scripts/sync_repair_tool.py::replay_row_command()` - apply guard is weaker than Stage 9 production gate.
- `scripts/run_sync_parity_stage8_staging_rollout.py::execute_plan()` - staging branch/confirm gate.
- `scripts/run_sync_parity_stage9_production_rollout.py::execute_plan()` - production branch/confirm gate.
- `core/events.py::log_change()` - durable outbox row and direct push/Redis enqueue.
- `core/sync_outbox_guard.py::verify_pending_sync_outbox()` - outbox guard.

## Commands and searches performed

Read-only GitHub connector equivalents were used for repository inspection. No production/staging mutating commands were run.

Equivalent commands inspected:

- `git log --oneline -12` via branch/commit history discovery.
- `git show --stat <commit>` via `compare_commits(<commit>^, <commit>)` for each listed commit and the additional `69b75c7d` commit.
- `git show <commit>` via `fetch_commit` / commit diffs where needed.
- Branch diff equivalent: `compare_commits(main, candidate/sync-parity-hardening)`.

Search terms inspected across the repository:

- `sync_registry`
- `receive_sync_data`
- `sync_parity`
- `sync_repair`
- `source_sequence`
- `watermark`
- `parity`
- `offer_public_id`
- `version_id`
- `user_notification_preferences`
- `trade_delivery_receipts`
- `offer_publication_states`
- `user_blocks`
- `telegram_link_tokens`

Key files directly inspected:

- `api/routers/sync.py`
- `core/sync_metadata.py`
- `core/sync_parity.py`
- `core/sync_repair.py`
- `core/sync_registry.py`
- `core/sync_field_policy.py`
- `core/sync_protocol.py`
- `core/events.py`
- `core/sync_worker.py`
- `core/sync_outbox_guard.py`
- `core/offer_sync_payload.py`
- `models/sync_apply_watermark.py`
- `scripts/compare_sync_parity.py`
- `scripts/sync_repair_tool.py`
- `scripts/run_sync_parity_stage8_staging_rollout.py`
- `scripts/run_sync_parity_stage9_production_rollout.py`
- `tests/test_sync_guarantee_matrix.py`
- `tests/test_sync_router_watermarks.py`
- `tests/test_sync_router_stale_events.py`
- `tests/test_sync_parity.py`
- `tests/test_sync_repair.py`
- `tests/test_sync_coverage.py`
- `tests/test_sync_metadata.py`
- `tests/test_sync_field_policy.py`

## Evidence snippets summarized

- Audit docs correctly state old sync health was not full parity proof and list real drift classes such as `user_notification_preferences`, `offer_public_id`, warning fields, and publication states.
- Registry marks messenger tables as `NO_SYNC`, while product/business tables are `SYNC`.
- Event listeners create durable change-log rows and the outbox guard verifies synced ORM writes record outbox rows.
- Sync worker only marks change-log rows delivered after peer success, but stale watermark decisions count as success at receiver level.
- Watermark table is keyed by `(source_server, aggregate_table, aggregate_key)`, so aggregate key correctness is critical.
- `offer_requests` parity identity uses `(request_home_server, idempotency_key)`, but `_aggregate_identity()` can use `offer_public_id` first, causing cross-request collision.
- Parity snapshots set `truncated`, but comparison does not treat truncation as an error/warning status.
- Repair dry-run tests verify redaction and field dropping, but apply mode does not have the same production guard model as Stage 9.
- Stage 9 production execute requires `main` and separate confirmations, which is good.

## Accepted/rejected concerns

### Accepted

- The branch mostly documents and measures parity, with partial prevention. It does not fully prevent silent drift yet.
- `offer_requests` watermark aggregate collision is a likely silent-drift bug.
- Parity checker can produce both false positives and false negatives.
- Repair tooling can be misapplied if used operationally without stricter controls.
- Stage 9 gate is structurally strong but not enough while code-level parity/watermark bugs remain.

### Rejected or mitigated

- Concern: event listener exceptions can silently miss outbox writes. Mitigated for normal ORM paths by `sync_outbox_guard`, provided event listeners are registered in the process. Raw/bulk writes are blocked by guard unless explicitly sync execution.
- Concern: messenger tables are accidentally synced. Rejected based on registry and coverage tests; messenger/runtime tables are explicitly `NO_SYNC`.
- Concern: production execute can run from candidate branch. Rejected for the Stage 9 script; execute requires `main`. Note that separate repair apply tooling still needs more controls.

## Final go/no-go recommendation

**Do not merge this branch to `main` yet.**

Recommended path:

1. Fix the `offer_requests` aggregate identity bug and add table-by-table watermark identity tests.
2. Make parity truncation a rollout-blocking condition.
3. Normalize parity business hashes away from local DB IDs/FKs or explicitly classify them.
4. Harden repair apply controls.
5. Update stale registry/docs and add missing null/fallback tests.
6. Then run Stage 8 staging preflight and non-mutating parity checks.
7. Only after clean Stage 8 evidence should this branch be considered for merge and Stage 9 production planning.

Current readiness classification: **needs fixes first; staging preflight useful; not merge-ready; not production-rollout-ready.**
