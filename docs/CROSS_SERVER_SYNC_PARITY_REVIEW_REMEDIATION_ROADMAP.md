# Cross-Server Sync Parity Review Remediation Roadmap

Reviewed on: 2026-06-28
Branch: `candidate/sync-parity-hardening`
Reviewed head: `c89c0315`
Source review: `tmp/sync_parity_hardening_agent_review.md`
Primary roadmap: `docs/CROSS_SERVER_SYNC_PARITY_IMPLEMENTATION_ROADMAP.md`

This document is the post-review remediation contract for the sync parity
hardening branch. The implementation roadmap added useful measurement,
watermarks, repair tooling, and rollout gates, but review confirmed several
P0/P1 issues that can still create drift or hide drift.

Until this remediation roadmap is completed, the branch is not ready for merge
to `main`, production rollout, strict watermark mode, or production repair.

## Non-Negotiable Guardrails

- Before every change, verify the branch with `git branch --show-current`; work
  must remain on `candidate/sync-parity-hardening` unless the user explicitly
  requests a different branch.
- Before editing a sensitive sync path, search the repository for the table,
  model, event listener, payload builder, receiver branch, parity rules, repair
  rules, and tests.
- No production data writes are allowed from this roadmap. Production repair
  stays manual, dry-run-first, backup-gated, and explicitly approved.
- Do not enable `sync_watermark_strict_mode` until aggregate identities,
  old/new payload coexistence, and truncation/duplicate parity behavior are
  fixed and tested.
- Do not treat `/api/sync/health` as parity proof. It reports local backlog and
  parity capability, not a fresh cross-server comparison result.
- Tests must cover both success paths and failure/drift paths. A stage is not
  complete if it only tests the happy path.

## Verified Findings

### P0 - Direct Push Can Publish Before Source Commit

`core/events.py::log_change` inserts `change_log` inside the source transaction
and immediately pushes the payload to Redis and direct HTTP. If the source
transaction later rolls back, the peer may have already applied an uncommitted
change.

Required direction:

- `log_change()` must remain a durable outbox write only.
- Peer delivery must be based on committed `change_log` rows.
- Immediate direct push must be disabled, removed, or moved to a real
  after-commit mechanism that cannot fire on rollback.

### P0 - Offer Replay Refresh Reuses Old Source Sequence With New Payload

`core/sync_worker.py::refresh_offer_sync_item_from_authoritative_state` rebuilds
queued offer payloads from current state while preserving the old
`change_log_id`/`source_sequence`. If direct push already applied the original
payload, worker replay can send a different payload for the same sequence and
trigger `same_source_sequence_different_payload`.

Required direction:

- Do not send refreshed current-state payloads under an old source sequence.
- Prefer original committed `change_log` payload replay for worker delivery.
- If a future current-state compaction path is needed, it must use explicit new
  monotonic replay sequence semantics and dedicated tests.

### P0/P1 - Watermark Aggregate Identity Is Wrong For Some Logical Rows

`offer_requests` currently aggregate by `offer_public_id`, which collapses
distinct request ledger rows for the same offer. `trading_settings` events use
record id `0`, so all setting keys share a single aggregate.

Required direction:

- `offer_requests` aggregate identity must prefer
  `request_home_server:idempotency_key`.
- `trading_settings` aggregate identity must use `data["key"]`.
- Tests must prove distinct logical rows do not suppress each other as stale.

### P1 - Parity Can Report Clean On Incomplete Or Duplicate Snapshots

The parity snapshot records `truncated=True`, but comparison does not fail or
warn on truncation. Duplicate `identity_hash` values are collapsed into a dict,
which can hide duplicate logical rows.

Required direction:

- Any truncated comparison must return an explicit non-clean status, preferably
  `incomplete`.
- Duplicate identity hashes must be counted and reported.
- Duplicate identities and unexplained row-count mismatches must be critical
  unless explicitly classified as accepted local-only behavior.

### P1 - `offer_publication_states` Policy Is Inconsistent

Code treats `offer_publication_states` as `SyncPolicy.SYNC`, while the primary
roadmap still lists it as an open product decision. This mismatch blocks a safe
strict parity release.

Required direction:

- Close the product decision before merge.
- If shared: document the shared fields, keep local Telegram-only fields out of
  business parity, and require staging deep parity evidence.
- If local: remove it from global sync/deep parity/repair expectations and keep
  only documented local observability.

### P1 - TLS Verification Is Disabled On Sync Paths

Earlier code paths in `core/sync_push.py` and `/api/sync/resync` used
`verify=False`. HMAC protects payload authenticity, but disabling TLS
verification weakens endpoint identity and is not acceptable as the production
default.

Required direction:

- TLS verification must default to enabled.
- If private/self-signed certificates are used, configure an explicit CA bundle.
- Any insecure mode must be environment-gated, loudly logged, and forbidden for
  production release gates.

### P1/P2 - Delete Payloads Are Not Natural-Identity Safe

Some delete event payloads include only database `id`. The receiver can resolve
public identities when present, but id-only deletes cannot be safely resolved
after id drift.

Required direction:

- Delete payloads for natural-key tables must include natural/public identity.
- Receiver delete resolution must prefer natural/public identity over database
  id.
- Id-only delete should be allowed only for tables where id parity is guaranteed
  or explicitly operator-approved.

### P1 - `user_notification_preferences` Receiver Exists But The Guard Is NULL-Unsafe

The receiver coverage concern is stale: the table is now in `TABLE_ORDER`,
model mapping, natural keys, and matrix tests. The remaining bug is the
`updated_at` upsert predicate, where `NULL <= timestamp` can skip a valid
update.

Required direction:

- Make the predicate NULL-safe, matching the existing safer user recency
  pattern.
- Test existing `NULL updated_at`, incoming `NULL updated_at`, older update,
  and newer update.

## Implementation Stages

### Stage R0 - Documentation And Release Stop

Goal: make the no-go state explicit and prevent accidental promotion.

Deliverables:

- Add this remediation roadmap.
- Link it from the primary implementation roadmap.
- Update the primary roadmap status to state that Stage 8/9 execution is
  blocked until this remediation is complete.

Tests:

- Documentation-only stage; verify links and branch status.

Exit criteria:

- Review blockers are documented with a clear execution order.

### Stage R1 - Commit-Safe Sync Delivery

Goal: ensure peer delivery cannot happen before source commit.

Deliverables:

- Remove Redis/direct payload publication from flush-time `log_change()`, or
  gate it behind a proven after-commit mechanism.
- Keep `change_log` as the durable source of sync truth.
- Ensure the sync worker can drain committed rows without relying on pre-commit
  Redis payloads.
- Add an operational compatibility flag only if needed, but production defaults
  must be safe.

Required searches before editing:

- `log_change`
- `sync:outbound`
- `push_sync_direct`
- `fetch_next_unsynced_change_log_item`
- `mark_change_log_delivered`
- `after_commit`

Tests:

- Transaction rollback after a synced-model mutation does not call peer push and
  does not enqueue a deliverable peer payload.
- Committed mutation is eventually picked up from committed `change_log`.
- Worker retry still marks delivered only after peer success.
- Existing no-sync rejection/drop behavior remains intact.

Exit criteria:

- No sync payload can leave the source as peer-deliverable before commit.

Implementation status - 2026-06-28:

- `core.events.log_change()` now only records the sanitized `change_log` row and
  outbox guard marker inside the source transaction.
- `log_change()` no longer publishes a peer-deliverable payload to
  `sync:outbound` and no longer calls direct HTTP sync push from flush-time
  listeners.
- `core.sync_worker` treats `sync:outbound` entries as wake-up signals only and
  builds peer delivery payloads from committed `change_log` rows.
- `sync:retry` still carries retry payloads created by the worker after a prior
  delivery failure.
- Targeted tests cover outbox-only event logging, committed change-log draining,
  outbound wake-up handling, and retry payload behavior.

### Stage R2 - Source-Sequence And Aggregate Identity Correctness

Goal: make watermarks safe for logical rows.

Deliverables:

- Stop offer current-state refresh from reusing old source sequences.
- Fix `_aggregate_identity()` for `offer_requests`.
- Add `_aggregate_identity()` support for `trading_settings.key`.
- Review all `SyncPolicy.SYNC` tables for missing logical aggregate identity.
- Keep `sync_watermark_strict_mode=False` until mixed-version tests pass.

Required searches before editing:

- `_aggregate_identity`
- `build_sync_metadata`
- `source_sequence`
- `refresh_offer_sync_item_from_authoritative_state`
- `trading_settings`
- `offer_requests`

Tests:

- Direct-push-like original payload accepted, then worker replay of the same
  committed payload is duplicate/safe, not conflict.
- Two `offer_requests` for the same `offer_public_id` but different
  `(request_home_server, idempotency_key)` do not suppress each other.
- Two `trading_settings` keys delivered out of order do not suppress each
  other.
- Legacy payloads without complete metadata still apply in compatibility mode.

Exit criteria:

- A source sequence only orders one logical aggregate, never an unrelated row.

Implementation status - 2026-06-28:

- `core.sync_worker` no longer refreshes offer replay payloads from current
  offer state. Committed `change_log` rows are replayed with their original
  payload and original hash, so a source sequence is not reused for a different
  payload.
- `_aggregate_identity()` now keys `offer_requests` by
  `request_home_server:idempotency_key` when available, with record-id fallback
  for legacy payloads that do not carry idempotency metadata.
- `_aggregate_identity()` now keys `trading_settings` by `data["key"]` instead
  of collapsing all setting rows under record id `0`.
- Active `SyncPolicy.SYNC` aggregate identity coverage was reviewed. Remaining
  id/natural-key hardening for deletes and receiver merge rules is intentionally
  left for Stage R3.
- `sync_watermark_strict_mode` remains off by default; strict mode is still
  blocked until R3/R4 complete and staging parity is clean.
- Targeted tests cover committed offer replay, retry payload preservation,
  `offer_requests` per-request aggregate identity, `trading_settings` per-key
  aggregate identity, and watermark contexts for both table families.

### Stage R3 - Receiver Merge And Delete Identity Hardening

Goal: remove remaining id-drift and stale-merge traps from receiver paths.

Deliverables:

- Make `user_notification_preferences` `updated_at` guard NULL-safe.
- Add natural identity to delete payloads for:
  - `accountant_relations`
  - `customer_relations`
  - `telegram_link_tokens`
  - `market_schedule_overrides`
  - `commodities`
  - `commodity_aliases`
  - `notifications`
  - any other synced table where natural/public identity exists
- Update receiver delete resolution to prefer natural/public identity.
- Add source-authority/recency guards for Iran-authoritative admin/config tables
  where currently missing.

Required searches before editing:

- `after_delete`
- `NATURAL_KEYS`
- `_resolve_local_record_id_by_public_identity`
- `_build_upsert_stmt`
- `user_notification_preferences`
- `market_schedule_overrides`
- `telegram_link_tokens`
- `admin_market_messages`
- `admin_broadcast_messages`

Tests:

- Delete by natural identity works when database ids differ.
- Id-only legacy delete is either safely ignored, deferred, or allowed only for
  explicitly id-safe tables.
- Preference rows update correctly when either side has `NULL updated_at`.
- Stale admin/config updates do not overwrite newer authority-owned state.

Exit criteria:

- Deletion and upsert behavior does not depend on accidental database id parity
  when a safer logical identity exists.

Implementation status - 2026-06-28:

- `user_notification_preferences` now uses a NULL-safe `updated_at` upsert
  guard, matching the safer user-recency pattern.
- Delete payloads now include natural identity for accountant/customer
  relations, Telegram link tokens, commodities, commodity aliases, market
  schedule overrides, and notifications.
- Receiver delete handling now refuses unsafe id-only deletes for tables with
  natural/public identity. Legacy id-only deletes are ignored with audit
  metadata instead of deleting a potentially unrelated local row.
- Receiver upsert handling now prefers natural keys for relation rows,
  commodities, commodity aliases, and market schedule overrides. Public/natural
  identity payloads drop source database `id` before persistence to avoid
  primary-key collision with unrelated local rows.
- Commodity alias sync now carries the canonical commodity name and resolves it
  to the local `commodity_id`; aliases defer until the referenced commodity is
  present locally.
- Watermark/public-identity metadata and parity identity now use stable
  relation/commodity/notification natural keys instead of falling back to local
  database id.
- Iran-authoritative admin/config tables reject payloads with explicit
  non-Iran `source_server`; legacy payloads without source metadata remain in
  compatibility mode and are logged.
- `sync_apply_watermarks` is registered as internal sync bookkeeping, not
  product sync data.
- Targeted tests cover natural-key upserts, safe delete resolution, id-only
  delete ignore behavior, commodity alias localization/defer, NULL-safe
  preference updates, source-authority rejection, and metadata/parity identity.

### Stage R4 - Parity Checker False-Negative Hardening

Goal: make parity reports fail safe.

Deliverables:

- Add `incomplete` status for truncated snapshots.
- Add duplicate identity counting and samples.
- Treat duplicate identities and unexplained row-count mismatches as critical
  drift.
- Update `scripts/compare_sync_parity.py` exit codes and output contract.
- Update Stage 8/9 gates to fail on `incomplete` unless an explicit operator
  override is present.

Required searches before editing:

- `build_table_parity_snapshot`
- `_records_by_identity`
- `compare_parity_snapshots`
- `compare_sync_parity.py`
- `run_sync_parity_stage8`
- `run_sync_parity_stage9`

Tests:

- Truncated local or peer snapshot does not return `ok`.
- Duplicate identity hashes are reported and fail comparison.
- Same identity set with different raw row counts fails unless explicitly
  classified.
- Quick parity remains clearly labeled as limited coverage.

Exit criteria:

- A clean parity result means the compared data was complete enough to trust.

Implementation status - 2026-06-28:

- Table snapshots now include `duplicate_identity_count` and sampled duplicate
  identity hashes before records are collapsed for comparison.
- `compare_parity_snapshots()` now returns status `incomplete` when either side
  of any compared table is truncated.
- Duplicate identities and unexplained row-count mismatches now produce
  `critical_drift` instead of being hidden by the identity dictionary.
- Table reports now expose truncation flags, duplicate identity counts,
  row-count mismatch status, and duplicate samples for operator diagnostics.
- `scripts/compare_sync_parity.py` already failed non-clean statuses; tests now
  explicitly cover the `incomplete` exit path.
- Targeted tests cover truncated snapshots, duplicate identities, unexplained
  row-count mismatch, script exit behavior, and the existing repair/gate
  consumers.

### Stage R5 - `offer_publication_states` Policy Closure

Goal: remove the code/document mismatch for publication state.

Decision:

- `offer_publication_states` remains a shared `SyncPolicy.SYNC` table because it
  is the cross-server evidence that an offer has been exposed on a surface.
- Shared/business truth is limited to the natural identity and state machine:
  `dedupe_key`, `offer_public_id`, `offer_home_server`, `surface`,
  `publication_owner_server`, `status`, `offer_version_id`,
  `last_known_offer_status`, terminal state timestamps, and `archived`.
- Local/runtime publication evidence is not business truth and must not cause a
  strict parity failure: local row `id`, localized `offer_id`,
  `surface_resource_id`, `telegram_chat_id`, `telegram_message_id`,
  provider `error_code` / `error_message`, and `state_metadata`.
- Volatile retry timing (`last_attempt_at`, `last_success_at`, `next_retry_at`,
  `updated_at`) remains excluded from business parity.
- The repair tool may replay the shared state machine, but it must not enforce
  Telegram runtime identifiers or provider diagnostics from the opposite server.

Deliverables:

- Decide and document shared-vs-local semantics.
- Align `core/sync_registry.py`, parity rules, repair rules, and receiver apply
  behavior with the decision.
- If shared, document which fields are business/shared and which fields are
  local/volatile.
- If local, remove it from deep parity and repair expectations.

Required searches before editing:

- `offer_publication_states`
- `OfferPublicationState`
- `publication_owner_server`
- `dedupe_key`
- `offer_version_id`
- `channel_message_id`

Tests:

- Shared mode: peer receives expected publication state without rewriting offer
  business truth.
- Local mode: peer rejects or ignores non-shared publication runtime without
  sync backlog.
- Parity output matches the selected policy.

Exit criteria:

- Roadmap and code agree on publication-state semantics.

Implementation status:

- `core/sync_registry.py` documents the selected shared policy.
- `core/sync_parity.py` classifies Telegram/runtime publication identifiers and
  diagnostics as local-only so they report as non-business differences.
- `core/sync_repair.py` excludes local Telegram/runtime publication fields from
  current-state replay payloads.
- Targeted tests cover Telegram runtime parity differences, strict status drift,
  and repair replay payload filtering.

### Stage R6 - Secure Transport And Production Gates

Goal: make rollout gates enforce the new safety assumptions.

Deliverables:

- Replace default `verify=False` with secure TLS verification.
- Add explicit CA bundle/config support for peer sync clients.
- Make insecure sync transport fail production release gates.
- Document Stage 9 backup mode as production-mutating even on candidate branch,
  or restrict backup mode to `main`.
- Revisit `StrictHostKeyChecking=no` and either pin hosts or document the
  accepted deployment constraint.

Required searches before editing:

- `verify=False`
- `trade_forward_ca_bundle`
- `httpx.Client`
- `httpx.AsyncClient`
- `StrictHostKeyChecking`
- `run_sync_parity_stage9`

Tests:

- Default sync HTTP client verifies TLS.
- CA bundle config is passed to httpx.
- Insecure mode is rejected in production release gate.
- Stage 9 backup behavior is explicit and tested.

Exit criteria:

- Production sync transport and rollout gates no longer rely on insecure
  defaults.

Implementation status:

- Added `SYNC_VERIFY_TLS=true` default and optional `SYNC_CA_BUNDLE` runtime
  configuration for cross-server sync HTTP clients.
- `core/sync_worker.py`, `core/sync_push.py`, `/api/sync/resync`,
  shared-table seed helpers, dev-admin remote session reset, and
  session-authority checks now use the shared sync transport policy instead of
  unverified client settings.
- Production env rendering and the production deployment manifest carry the sync
  TLS settings, and `scripts/production_deploy_online.sh` rejects
  `SYNC_VERIFY_TLS=false` without `SYNC_CA_BUNDLE`.
- Stage 9 production rollout planning now includes a transport security gate and
  blocks preflight/execute/postdeploy modes if sync TLS verification is disabled
  without a CA bundle.
- Stage 9 backup mode remains explicitly production-mutating: it has a separate
  confirmation token, marks every backup command as `mutates_production=true`,
  and release execution still requires `main`.
- Stage 9 SSH access, production deploy helpers, sync-health sampling, recovery
  helpers, worker-pool probes, and production backup pullback now use
  `StrictHostKeyChecking=accept-new` instead of disabling host-key checks. This
  keeps first-time host onboarding workable while still refusing silent host-key
  replacement.
- Remaining `verify=False` text appears only in historical/roadmap notes, not in
  runtime code paths.

### Stage R7 - Observability And Strict-Mode Readiness

Goal: make operators see actual parity state before strict mode is enabled.

Deliverables:

- Store/report latest parity comparison status, timestamp, mode, truncation,
  duplicate count, business drift count, and critical drift count.
- Add observability for watermark stale/duplicate/conflict counters.
- Add a rollback/degrade playbook covering direct-push disable, strict-mode
  disable, queue drain/hold, publication gate controls, and repair dry-run.
- Keep strict alerts warning-only until staging evidence proves no false
  positives.

Required searches before editing:

- `/api/sync/health`
- `record_sync_health`
- `compare_sync_parity`
- `sync_watermark`
- `observability`
- `CROSS_SERVER_SYNC_OBSERVABILITY`

Tests:

- Health output cannot be mistaken for parity proof unless a fresh comparison
  exists.
- Warning-only alert state includes latest parity evidence.
- Strict alert activation is blocked when parity status is stale, incomplete, or
  critical.

Exit criteria:

- Strict mode and strict alerts have enough live evidence to be safely enabled.

## Suggested Execution Order

1. Stage R0
2. Stage R1
3. Stage R2
4. Stage R3
5. Stage R4
6. Stage R5
7. Stage R6
8. Stage R7

R1 and R2 are merge blockers. R3 and R4 are production blockers. R5 is both a
merge blocker and a product decision blocker because current docs and code
disagree. R6 and R7 are required before production rollout or strict alerting.

## Minimum Test Gate Before Merge

- Targeted unit tests for R1/R2/R3/R4.
- `tests/test_sync_guarantee_matrix.py`
- `tests/test_sync_router_watermarks.py`
- `tests/test_sync_parity.py`
- `tests/test_sync_repair.py`
- Stage 8 rollout contract tests in non-mutating mode.

## Minimum Gate Before Production

- All merge gates pass.
- Staging deep parity comparison is clean and not truncated.
- Stage 8 staging preflight passes after remediation.
- Stage 9 local gates pass from `main`.
- Production preflight is read-only and shows no unresolved business/critical
  drift.
- Backups and restore-smoke are present before any production deploy.
