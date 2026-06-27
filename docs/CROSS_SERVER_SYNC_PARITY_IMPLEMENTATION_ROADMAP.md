# Cross-Server Sync Parity Implementation Roadmap

Reviewed on: 2026-06-27
Source document: `docs/CROSS_SERVER_SYNC_PARITY_AUDIT_AND_ROADMAP.md`
Base commit: `9778f024`

This roadmap turns the sync parity audit into an executable implementation contract. It is intentionally ordered from lower-risk work to higher-risk work.

The main engineering rule is:

`sync delivery clean` is not enough. A release is acceptable only when delivery, parity, stale-event protection, and repairability are all covered.

## Execution Guardrails

- Before every code change, verify the current branch with `git branch --show-current` and verify there are no unrelated working-tree changes.
- Implementation should happen on a dedicated candidate branch, not directly on `main`, unless the user explicitly requests otherwise.
- Production data writes are forbidden unless there is an explicit production repair stage with dry-run output, backup confirmation, exact row count, and user approval.
- Staging must pass before production deploy.
- For sensitive sync changes, search the repository before editing: table name, model class, event listener, sync payload builder, receiver path, tests, and migration references.
- Every stage must finish with tests that prove both success paths and drift/failure paths.
- No stage may silently convert local-only data into shared state. Local-only and volatile fields must be explicitly documented in parity hashes.

## Stage 0 - Baseline And Safety Instrumentation

Goal: make the current production/staging state measurable before changing behavior.

Deliverables:

- Reusable read-only parity audit script for synced tables.
- Stable hash definitions for the current critical tables.
- Separate reporting for business fields, local-only fields, and volatile fields.
- Current-drift report format that can be attached to staging and production decisions.

Primary tests:

- Script can run against a local test DB without mutating data.
- Script classifies expected local-only fields without failing business parity.
- Script fails on intentionally injected relation null-link drift and offer business-field drift.

Logical challenges:

- A single table can contain shared business fields and local runtime fields. Example: `offers.channel_message_id` is Telegram-local while `offers.status` is shared business state.
- Equality by row count is unsafe. Current production had equal `offers` counts but divergent `offer_public_id` values.
- Volatile timestamps must not hide business drift, but they also must not create false critical alerts.

Operational challenges:

- Deep parity can be expensive on production tables if run too often.
- Remote DB access must remain read-only for audit mode.
- Audit output must not leak sensitive user data, phone numbers, tokens, or notification payloads.

Exit criteria:

- Operators can run a safe read-only parity report.
- The report separates critical drift from local/volatile differences.

## Stage 1 - Receiver Coverage Hardening

Goal: make it impossible for a `SyncPolicy.SYNC` table to have event emission without receiver support.

Deliverables:

- Add `user_notification_preferences` to `TABLE_ORDER`.
- Add `UserNotificationPreference` to `get_model_class()`.
- Add receiver upsert by `user_id`.
- Add sequence alignment only if this table can be locally inserted on both servers with integer id collision risk.
- Add CI tests comparing `core.sync_registry`, event listeners, `TABLE_ORDER`, `get_model_class()`, and no-sync policy.
- Replace the current test expectation for `user_notification_preferences` from `receiver_model_not_registered` to accepted receiver behavior.

Primary tests:

- Every `SyncPolicy.SYNC` table is receiver-enabled or explicitly exempted.
- Every listener-emitted table is receiver-enabled or explicitly no-sync.
- `user_notification_preferences` INSERT/UPDATE/DELETE is accepted by receiver and preserves `market_offer_push_enabled`.
- No-sync tables still fail closed, except the documented mandatory-channel projection.

Logical challenges:

- The registry and receiver are currently separate manual lists. That creates drift risk unless tests compare them.
- `user_notification_preferences` has historical data on Iran with no outgoing `change_log`; receiver coverage alone will not backfill old rows.
- Delete semantics must be explicit: hard delete from one side can conflict with a later recreated preference row.

Operational challenges:

- A one-time backfill from Iran to foreign is required after receiver enablement.
- Backfill must not create duplicate preferences for the same `user_id`.
- Existing sync-worker behavior marks peer rejections as pending unless receiver returns a clean success, so staging must verify no retry loop remains.

Exit criteria:

- No synced table is silently missing receiver coverage.
- Iran's existing 3 preference rows have a documented dry-run backfill path.

Implementation status - 2026-06-27:

- Completed on `candidate/sync-parity-hardening` in commit `69b75c7d`.
- `scripts/seed_shared_sync_tables.py --table user_notification_preferences --dry-run` is the documented read-only backfill preflight. On staging after deploy it reported `rows=1`, `sent=0`.
- Production writes remain forbidden until a separate production repair step has dry-run output, exact row count, backup confirmation, and explicit user approval.

## Stage 2 - Offer Payload And Legacy Public Identity Drift

Goal: stop new offer drift and define a safe policy for historical offer identity drift.

Deliverables:

- Add `exclude_from_competitive_price` and `price_warning_type` to `build_offer_sync_payload()`.
- Add sync tests proving competitive-warning fields survive source to receiver.
- Add a migration-review test or lint rule preventing random independent backfills for shared identities.
- Build a dry-run report for historical `offers.offer_public_id` mismatch.
- Define the repair or exemption policy for inactive historical offers.

Primary tests:

- Offer created with competitive warning on one server has identical warning fields on the peer.
- Existing stale offer guards still protect terminal state and version ordering.
- `channel_message_id` remains excluded from incoming sync apply.
- Future public-id migration/backfill cannot use random values independently on both servers.

Logical challenges:

- `offer_public_id` is now the public cross-server identity, but historical rows were random-backfilled independently on each server.
- Dependent tables may reference `offer_public_id`: `offer_requests`, `offer_publication_states`, trades payloads, public offer URLs, Telegram callbacks, reconciliation jobs, and test fixtures.
- It is unsafe to rewrite public ids for active offers without updating every dependent reference and visible channel/web state.
- `expired_at` drift may be operationally harmless for inactive rows but still breaks strict parity.

Operational challenges:

- Production repair must be dry-run first and must show before/after hashes.
- If historical public ids were exposed to users or Telegram callbacks, changing them may break old links.
- Offer publication state repair is blocked until `offer_public_id` policy is decided.

Exit criteria:

- New offers cannot lose competitive-warning state.
- Historical offer public-id drift is either repaired safely or explicitly classified as inactive historical exemption.
- The parity checker knows how to compare legacy rows while the historical drift remains unresolved.

Implementation status - 2026-06-27:

- New offer sync payloads now include `exclude_from_competitive_price` and `price_warning_type`.
- Receiver upsert coverage is guarded so these fields are kept during offer sync apply.
- A migration guard prevents new migrations from independently random-backfilling `offers.offer_public_id`; the only allowed random backfill is the already-known historical migration `a6b7c8d9e0f1_add_offer_public_id.py`.
- `scripts/report_offer_public_id_drift.py` provides a dry-run snapshot/compare report for historical `offer_public_id` mismatch. It performs no writes.
- Dry-run mismatch policy:
  - active offer public-id drift is repair-blocked;
  - rows with request/publication/trade dependencies are repair-blocked;
  - terminal offers without dependent references are classified as inactive historical exemption candidates;
  - all other shapes require manual review.
- Actual production repair is intentionally not implemented in this stage.

## Stage 3 - Source-Sequence Watermark

Goal: prevent older same-source sync events from overwriting newer same-source state.

Deliverables:

- Add explicit `source_server` to sync metadata.
- Add `sync_apply_watermarks` model and migration.
- Define `aggregate_key` per synced table.
- Enforce stale/equal/conflict handling before apply.
- Update watermark in the same transaction as the applied row.
- Log stale ignored events and equal-sequence conflicts.

Primary tests:

- Out-of-order same-source events cannot overwrite newer state.
- Duplicate replay with same payload hash is idempotent.
- Equal sequence with different payload hash is a conflict.
- FK-deferred events do not advance watermarks until actually applied.

Logical challenges:

- `change_log.id` is local to each server, so it cannot be used without pairing it with `source_server`.
- Some tables have natural identities, some have public identities, and some still rely on integer id.
- DELETE events need tombstone-aware aggregate keys; otherwise a later INSERT may be incorrectly ignored or resurrected.
- Replayed historical events must be classified without corrupting current state.

Operational challenges:

- Rolling deploy compatibility: a new receiver may receive old payloads without `source_server`.
- Backfill or repair tools must be able to set or repair watermarks intentionally.
- Bad watermark state can block legitimate future events, so repair tooling must exist before strict enforcement.

Exit criteria:

- Same-source event ordering is enforced generically.
- Receiver can run in compatibility mode during rollout and strict mode after both servers are upgraded.

## Stage 4 - Table-Specific Business Invariants

Goal: protect business truth beyond generic event ordering.

Deliverables:

- `users`: field-level merge rules for admin fields, Telegram link fields, profile fields, counters, deletion/deactivation, and volatile timestamps.
- `accountant_relations` and `customer_relations`: lifecycle state machine and no-null-link regression rule.
- `invitations`: used/expired terminal guard.
- `telegram_link_tokens`: used/revoked terminal guard.
- `notifications`: dedupe-first upsert and read-state monotonic rule.
- `user_blocks`: soft-delete/tombstone or pair-watermark model.
- `market_runtime_state`: transition timestamp guard.
- `offer_publication_states`: status precedence and version guard.
- `trade_delivery_receipts`: localize or protect lease fields.

Primary tests:

- Each table family has stale, duplicate, terminal, and delete/recreate cases.
- Completed trades remain immutable for business fields.
- Active relation link ids cannot be cleared by stale payloads.
- Notification read state cannot be reverted by stale sync.
- User deletion/deactivation cannot be undone by old active payloads.

Logical challenges:

- Generic watermark only handles same-source ordering; it does not decide cross-source authority.
- `users` is a multi-authority table. A single `set_=excluded` rule is too broad.
- `user_blocks` currently uses hard-delete semantics, which makes ordering difficult without tombstones.
- `trade_delivery_receipts.worker_id` and `lease_until` are execution-local and should not behave like shared business truth.

Operational challenges:

- Refactoring receiver merge logic for many tables has high blast radius.
- Existing tests may need realistic fixtures for every table family.
- A strict invariant can reject legitimate old events during rolling deploy unless compatibility is planned.

Exit criteria:

- Every synced table has an explicit merge rule.
- Dangerous generic overwrite paths are limited to append-only or watermarked tables.

## Stage 5 - Runtime Parity Checker

Goal: prove database parity instead of relying on delivery health.

Deliverables:

- Quick parity mode for critical tables.
- Deep parity mode for all synced tables.
- Stable hash schema per table.
- Signed operator endpoint or operator script.
- Integration with `/api/sync/health` as a separate `parity_status`.
- Severity levels: critical drift, business drift, local-only difference, volatile difference.

Primary tests:

- Injected drift is detected.
- Local-only differences do not fail business parity.
- Missing receiver table rows fail critical parity.
- Parity report redacts sensitive values.

Logical challenges:

- Some tables must be compared by public identity, some by natural key, some by row id, and some by composite keys.
- Legacy `offers.offer_public_id` drift must not make the checker unusable forever, but it must remain visible.
- `offer_publication_states` needs a product decision: shared state or foreign-local Telegram runtime.

Operational challenges:

- Running deep checks too often can add DB load.
- Cross-server parity endpoints must be authenticated and rate-limited.
- Alerts can become noisy if volatile/local-only fields are misclassified.

Exit criteria:

- `/api/sync/health` distinguishes delivery health from parity health.
- Operators can get table-level and record-level mismatch samples.

## Stage 6 - Repair And Replay Tools

Goal: make drift recovery safe, repeatable, and auditable.

Deliverables:

- Dry-run repair command.
- Current-state replay command by table and identity.
- Public/natural identity support: offer public id, trade number, dedupe key, relation id/token, user id/mobile.
- Before/after stable hash logging.
- Signed/operator-only execution path.
- Watermark repair path after confirmed repair.

Primary tests:

- Dry-run performs no writes.
- Repair updates exactly the expected rows.
- Repair refuses ambiguous identities.
- Repair is idempotent.
- Repair logs enough evidence for postmortem.

Logical challenges:

- Repairing one table may require dependent table updates.
- Repair must not replay local-only fields as shared state.
- Historical offer public-id repair needs a deterministic match key and dependent-reference update plan.

Operational challenges:

- Production repair needs backup confirmation and user approval.
- A partial repair can create worse drift than the original issue.
- Repair tools must be usable when normal sync is degraded.

Exit criteria:

- Known drift can be repaired without ad hoc SQL.
- Every repair has auditable before/after output.

## Stage 7 - Sync Guarantee Test Matrix

Goal: prevent regressions in delivery, ordering, parity, and repair.

Deliverables:

- Unit tests for every table merge rule.
- Integration tests for delayed, duplicated, reordered, and failed sync events.
- Parity checker tests with intentional drift fixtures.
- Repair tool tests with dry-run and real local fixture DB writes.
- Full matrix scenarios for short and medium outage replay.

Primary tests:

- Receiver coverage matrix.
- Payload coverage matrix.
- Stale event matrix.
- Table invariant matrix.
- Parity mismatch matrix.
- Repair/replay matrix.

Logical challenges:

- Full coverage is combinatorial. The matrix must group cases by table family and risk class, not duplicate every table blindly.
- Some failures are expected and must be asserted as safe rejection, not treated as test failure.
- Tests must cover both normal connection and outage/replay paths.

Operational challenges:

- Large matrix runs can be slow; split into fast CI, nightly, and manual release suites.
- Staging full matrix must not pollute production-like user data without cleanup tooling.
- Logs/artifacts must be retained but sanitized.

Exit criteria:

- CI fails if a new synced table lacks receiver coverage, payload coverage, ordering rules, parity hash, or tests.
- Release matrix catches intentionally injected drift.

## Stage 8 - Staging Rollout

Goal: verify under realistic two-server conditions before production.

Deliverables:

- Staging deploy plan.
- Pre-test parity baseline.
- Out-of-order sync tests.
- Candidate full matrix without load, then with controlled load if required.
- Manual Telegram/WebApp offer, trade, expiry, relation, block, and notification tests.
- Post-test cleanup and parity confirmation.

Primary tests:

- WebApp to WebApp, Bot to Bot, WebApp to Bot, Bot to WebApp market flows.
- User/customer/accountant visibility and notification flows.
- Short outage and medium outage replay.
- Drift injection and repair dry-run.

Logical challenges:

- Staging must mimic the Iran/foreign constraints: Iran cannot call Telegram; foreign cannot call WebApp.
- Some failures should be local-only by design and must not be classified as parity errors.

Operational challenges:

- Test data cleanup must be exact and safe.
- Telegram channel state may be manually changed and create expected publication warnings.
- The staging branch must not contain staging-only helpers when preparing candidate merge.

Exit criteria:

- Delivery and parity are clean after tests.
- No unexpected retry backlog remains.
- Cleanup report confirms no test data leak.

## Stage 9 - Production Rollout

Goal: introduce parity guarantees safely.

Deliverables:

- Production backup confirmation.
- Read-only pre-deploy parity report.
- Migration and code deploy plan.
- Post-deploy parity report.
- Warning-only parity alert window.
- Strict alert/fail-closed enablement plan for critical drift.

Primary tests:

- Pre/post deploy parity checks.
- Smoke tests for offer/trade/notification sync.
- Log review for stale ignored events, receiver rejections, retry backlog, and repair tool availability.

Logical challenges:

- A strict parity checker may reveal legacy drift that is known and intentionally exempted.
- Fail-closed behavior must not block legitimate local-only operations.

Operational challenges:

- Migration order must support rolling deploy between two servers.
- Backups and rollback must be ready before any production repair.
- Monitoring must distinguish service outage from parity drift.

Exit criteria:

- Both servers report delivery clean and parity clean, excluding documented accepted local-only/legacy exemptions.
- Repair tooling is available before critical parity alerting is enforced.

## Open Decisions Before Implementation

1. `offer_publication_states`: decide whether Telegram publication rows are shared product state or foreign-local operational state.
2. Historical `offers.offer_public_id`: decide repair vs inactive historical exemption.
3. `offers.expired_at`: decide whether offer-home-server timestamp is authoritative or whether terminal state parity is sufficient.
4. `user_blocks`: decide soft delete/tombstone vs pair-watermark model.
5. Parity checker transport: decide signed endpoint vs operator-only scripts.
6. `change_log.verified`: decide rename/documentation-only vs new real parity verification columns.
7. Production repair policy: define which repairs require explicit user approval even after dry-run.

## Highest-Risk Challenges

1. Rewriting or repairing historical offer public ids without breaking dependent public links, Telegram callbacks, or audit history.
2. Field-level merge for `users`, because this table combines admin authority, Telegram linking, profile edits, counters, deletion, and volatile runtime fields.
3. Watermark rollout compatibility, because old and new sync payloads may coexist during deploy.
4. `user_blocks` delete/recreate ordering without tombstones.
5. Separating true business drift from local-only Telegram/WebApp runtime state in parity reports.
6. Repair tooling blast radius if a dry-run identity match is ambiguous.

## First Practical Implementation Target

Start with Stage 1 because it is the lowest-risk real fix and closes a confirmed production gap:

- receiver-enable `user_notification_preferences`;
- add registry/receiver/listener coverage tests;
- stage a safe dry-run backfill for the 3 existing Iran rows;
- verify no retry loop remains.

Only after Stage 1 should Stage 2 begin, because offer public-id drift and offer payload gaps are more sensitive and require product decisions.
