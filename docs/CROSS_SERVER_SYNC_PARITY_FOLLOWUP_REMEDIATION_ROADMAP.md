# Cross-Server Sync Parity Follow-up Remediation Roadmap

Reviewed on: 2026-06-28
Branch: `candidate/sync-parity-hardening`
Source review: `tmp/sync_parity_hardening_followup_agent_review.md`
Parent roadmap: `docs/CROSS_SERVER_SYNC_PARITY_REVIEW_REMEDIATION_ROADMAP.md`

This document captures the follow-up remediation work accepted after reviewing
the second AI-agent report. The first remediation pass fixed the original P0
sync correctness risks, but this follow-up found remaining merge and production
blockers around repair safety, market runtime authority, parity false positives,
market notice retry, and stale operator documentation.

Until this follow-up roadmap is complete, the branch remains suitable for
controlled staging validation only. It must not be merged to `main`, deployed to
production, used for production repair apply, or used to enable strict parity
alerting.

## Evidence Summary

The follow-up review was checked against the current codebase. These findings
are accepted as technically valid:

- Committed-outbox delivery is now implemented correctly. `core/events.py`
  writes only `change_log`, while `core/sync_worker.py` drains committed
  `change_log` rows. This closes the original pre-commit direct-push risk.
- `offer_requests` aggregate identity now uses
  `request_home_server:idempotency_key`, and `trading_settings` uses `key`.
  This closes the known watermark identity blockers for new payloads.
- Truncated parity snapshots now compare as `incomplete`, and the comparison
  script fails closed for non-clean statuses.
- Repair apply remains too permissive for production. The CLI apply path only
  requires `--confirm-write` and `--source-sequence`; it is not tied to a repair
  manifest, backup artifact, branch/release gate, expected row counts, or
  before/after parity evidence.
- Parity classification still treats many local integer IDs and local foreign
  keys as business data unless a table-specific exception exists. This can
  create false positive business drift for logically equal rows.
- `market_runtime_state` has a real authority conflict. The background-job
  authority allows both Iran and foreign to mutate the table, but the sync
  receiver rejects non-Iran source events for this table.
- The current market-close transition also expires active local-home offers.
  Any move to Iran-only `market_runtime_state` authority must preserve expiry of
  foreign-home offers when market close is first observed through synced Iran
  state.
- Market channel notice reconciliation is idempotent and foreign-only, but a
  failed notice is only marked retryable in data. No automatic retry scanner or
  explicit operator retry command exists yet.
- `CROSS_SERVER_SYNC_OBSERVABILITY.md` still describes direct DB-change push as
  active. `CROSS_SERVER_SYNC_PARITY_AUDIT_AND_ROADMAP.md` still describes
  `user_notification_preferences` as receiver-missing, which is stale.
- `user_notification_preferences` is receiver-enabled, but its current
  `updated_at` guard intentionally allows incoming `updated_at = NULL` to update
  an existing row. This needs an explicit product/engineering decision and
  tests before strict parity confidence.
- `offer_publication_states` has improved local-only parity handling, but the
  receiver still needs an explicit policy statement about which fields are
  shared cross-server evidence and which fields are foreign-local Telegram
  runtime truth.
- Production SSH tooling is improved in the online production deploy script.
  The root `Makefile` Iran helper targets now use
  `StrictHostKeyChecking=accept-new`, matching the safer first-connection
  posture used by the other production helpers.

## Non-Negotiable Guardrails

- Before every change, verify the branch with `git branch --show-current`.
  Work for this roadmap must remain on `candidate/sync-parity-hardening` unless
  the user explicitly requests another branch.
- Before editing a sensitive sync path, search related tables, models, event
  listeners, payload builders, receiver branches, parity rules, repair rules,
  background jobs, and tests.
- No production writes are allowed while implementing this roadmap. Production
  repair apply must stay unavailable or dry-run-only until the production safety
  envelope is complete and explicitly approved.
- Do not enable `sync_watermark_strict_mode` or strict production parity alerts
  based only on `/api/sync/parity/status`. Strict behavior requires fresh,
  retained, non-truncated, artifact-backed parity evidence.
- Tests must include failure paths, duplicate/replay paths, and stale/out-of-
  order paths. Happy-path coverage is not enough for completion.
- Iran must never connect to Telegram. Any Telegram market notice or channel
  side effect must remain foreign-owned.
- Any new foreign-only side effect worker must have a safe disable/degrade path
  so operators can stop the side effect without deleting receipts or sync
  backlog.

## Stage F0 - Baseline And Regression Lock

Goal: lock the current accepted fixes before changing more behavior.

Required searches before editing:

- `log_change`
- `change_log_entry_to_sync_item`
- `sync:outbound`
- `_aggregate_identity`
- `offer_requests`
- `trading_settings`
- `compare_parity_snapshots`
- `market_channel_notice_receipts`

Implementation direction:

- Add or extend a compact regression test group that proves the already-fixed
  behaviors remain fixed:
  - flush-time `log_change()` does not push DB changes to Redis/direct HTTP;
  - `sync_worker` treats fresh outbound queue payloads as wake-up only;
  - `offer_requests` and `trading_settings` aggregate identities do not
    collapse distinct logical rows;
  - truncated parity is not clean.
- Do not refactor these paths in this stage unless a regression is discovered.

Exit criteria:

- Regression tests pass locally.
- No production/staging runtime behavior is changed by this stage except tests
  or harmless documentation notes.

Implementation status on `candidate/sync-parity-hardening`:

- Added `tests/test_sync_parity_followup_f0.py` as the focused F0 regression
  gate for committed outbox behavior, outbound wake-up semantics, logical sync
  metadata identities, incomplete parity truncation, and foreign-local market
  notice receipt registry classification.

## Stage F1 - Market Runtime Authority Decision And Enforcement

Goal: remove the current contradiction for `market_runtime_state`.

Problem:

`core/background_job_authority.py` says the market schedule job may mutate
`market_runtime_state` on both servers. `api/routers/sync.py` classifies the
same table as Iran-authoritative and rejects non-Iran source metadata. If
foreign creates a runtime transition and sync metadata is present, Iran will
reject it, causing backlog and parity drift.

Recommended decision:

Use Iran-only authority for `market_runtime_state` product truth. Foreign should
not create authoritative `market_runtime_state` transitions. Foreign should only
read synced Iran state and reconcile Telegram notices locally. This matches the
policy that WebApp/product state is Iran-centered while Telegram execution is
foreign-only.

Important coupling:

The existing market-close transition does more than write
`market_runtime_state`: it also expires active offers where
`Offer.home_server == current_server()`. Therefore, simply disabling the foreign
market schedule transition can leave Telegram-origin/foreign-home active offers
open after market close. This stage must explicitly preserve foreign-home offer
expiry before changing foreign runtime authority.

Stage F1A - authority decision and tests, no behavior change:

- Decide the final authority model before changing runtime behavior.
- Add tests that document the intended outcome for a foreign-origin
  `market_runtime_state` sync item:
  - rejected intentionally under Iran-only authority; or
  - accepted under a dual-origin merge policy.
- Add tests that document current market-close offer expiry coupling:
  - close transition expires only local-home active offers;
  - duplicate or stale transition does not reopen or re-expire terminal offers.

Implementation status on `candidate/sync-parity-hardening`:

- Added a router regression proving foreign-origin `market_runtime_state`
  payloads are intentionally rejected under the current Iran-authoritative
  receiver policy.
- Added market transition regressions proving close expiry targets only
  active local-home offers and duplicate already-closed schedule evaluation
  does not reload or re-expire offers.

Stage F1B - implementation after F1A passes:

- Prevent or disable foreign writes to `market_runtime_state` from the market
  schedule loop. The foreign loop may still:
  - read the current synced state;
  - reconcile missing Telegram notices;
  - execute local Telegram-only side effects when needed;
  - expire foreign-home active offers on synced Iran close state without writing
    `market_runtime_state` as foreign authority.
- Add an explicit foreign-home market-close expiry path triggered by a newer
  synced Iran close transition, or document and implement an equivalent
  replacement authority. The preferred path is:
  - sync receive applies newer Iran `market_runtime_state` close state on
    foreign;
  - after commit, foreign runs an idempotent local-home offer expiry command for
    `Offer.home_server == "foreign"`;
  - duplicate close sync does not duplicate side effects;
  - stale open/close replay cannot revert terminal offer state.
- Keep receiver rejection for non-Iran source events on
  `market_runtime_state`.
- Update `core/background_job_authority.py` to state the final authority model.
- Add a test that a foreign-origin `market_runtime_state` sync item with source
  metadata is rejected intentionally.
- Add a test that an Iran-origin runtime transition applied on foreign triggers
  exactly one market channel notice.

Implementation status on `candidate/sync-parity-hardening`:

- `core/market_schedule_loop.py` now routes foreign market-schedule cycles to
  synced-state side-effect reconciliation instead of local schedule evaluation
  and `market_runtime_state` writes.
- `core/services/market_transition_service.py` now guards direct foreign calls
  to `apply_market_schedule_transition()` and reconciles foreign-home active
  offer expiry from the latest synced closed Iran runtime state.
- `api/routers/sync.py` now runs the same runtime side-effect reconciliation
  after an applied `market_runtime_state` sync item, so foreign can send/retry
  Telegram market notices and expire local foreign-home offers without owning
  product runtime truth.
- `core/sync_worker.py` now treats single-item receiver rejections for
  terminal policy reasons, including `source_authority_forbidden:*`, as
  delivered/non-applicable instead of retrying forever. This prevents a valid
  Iran-authoritative table rejection from blocking later foreign-to-Iran sync
  rows at the head of the queue.
- `api/routers/sync.py` now returns structured `error_items` for apply failure,
  retry exception, and unresolved deferred FK dependency paths. This keeps
  worker and operator evidence actionable when a row is legitimately still
  retryable.

Exit criteria:

- No code path on foreign mutates `market_runtime_state` as product truth.
- Foreign still sends/reconciles Telegram market notices from synced Iran state.
- Foreign-home active offers still expire on market close under the final
  authority model.
- The final authority policy is documented in code-facing docs.

## Stage F2 - Production Safety Envelope For Repair Apply

Goal: make accidental production repair writes impossible without complete
evidence and explicit operator intent.

Problem:

`scripts/sync_repair_tool.py replay-row --apply` can send signed write payloads
to `/api/sync/receive` when given an API key, target URL, `--confirm-write`, and
`--source-sequence`. The tool does not require a repair manifest, branch gate,
backup id, artifact hashes, expected row count, or before/after parity evidence.
It also allows raw `id` identities for many tables.

Implementation direction:

- Keep dry-run behavior as the default and safe path.
- Add a repair manifest format generated by the `plan` command. The manifest
  must include at least:
  - source and target server;
  - table and operation;
  - identity fields and identity hash;
  - expected source row count and target row count impact;
  - source sequence policy;
  - before-parity artifact hash;
  - required after-parity command;
  - backup artifact id or backup path;
  - git branch and commit SHA;
  - operator approval phrase.
- Make `--apply` require `--manifest` and validate the manifest before sending.
- Block production apply unless:
  - the branch is `main` or an explicitly approved release artifact;
  - backup evidence is present;
  - the manifest target matches the CLI target;
  - the table identity is not raw local `id` when a public/natural identity
    exists.
- Add an emergency `--allow-local-id-identity` flag only for non-production.
  In production it must fail closed.
- Add tests proving production apply refuses missing manifest, wrong branch,
  missing backup, wrong target, and raw local `id` identity.

Implementation status on `candidate/sync-parity-hardening`:

- `scripts/sync_repair_tool.py replay-row --apply` now requires
  `--manifest`, `--operator-approval`, `--confirm-write`, and
  `--source-sequence` before sending any peer write.
- The repair apply manifest validates source/target, table, operation,
  identity fields/hash, expected row counts, source sequence, backup evidence,
  before/after parity evidence, git branch/commit metadata, and operator
  approval phrase.
- Production apply refuses non-`main` manifests, refuses current git
  branch/commit mismatch, and refuses raw local `id` identity even if the
  non-production override flag is present.
- Non-production raw local `id` apply remains possible only with
  `--allow-local-id-identity` and complete manifest evidence.

Exit criteria:

- Staging repair apply is still possible with complete manifest evidence.
- Production repair apply cannot run accidentally.
- Raw local IDs are not accepted for public/natural identity tables in
  production repair.

## Stage F3 - Parity Field Classification Matrix

Goal: reduce false-positive `business_drift` without hiding real product drift.

Problem:

`core/sync_parity._classify_fields()` treats every field as business unless it
is configured volatile/local-only/no-sync. For public-identity tables, local DB
IDs and some local FKs can differ while the logical product row is identical.
Those differences currently become business drift.

Implementation direction:

- Define a per-table parity field matrix:
  - identity fields;
  - business fields;
  - local DB identity fields;
  - local FK fields;
  - volatile fields;
  - sensitive/redacted fields.
- Exclude generic `id` from the business hash for tables with stable public or
  natural identities.
- For local FK columns, prefer comparing resolved public/natural parent
  identities where practical. If resolution is not practical in this stage,
  classify the local FK separately and document the limitation.
- Keep fields that represent real product semantics in `business_hash`.
  Examples: offer status, price, quantity, remaining quantity, trade number,
  trade status, publication status, notification dedupe key.
- Add tests for at least:
  - `offers`: same `offer_public_id`, different local `id`, same business data
    must not be business drift;
  - `trades`: same `trade_number`, different local `id`, same business data
    must not be business drift;
  - `trade_delivery_receipts`: same `dedupe_key`, different local `id` or local
    FK, same business data must not be business drift;
  - real business changes must still be business drift.

Implementation status on `candidate/sync-parity-hardening`:

- `core/sync_parity.py` now treats generic `id` as local-only when a row is
  compared by a stable public/natural identity, and classifies
  `trade_delivery_receipts.trade_id`, `offer_id`, and `notification_id` as
  local FK differences rather than business drift.
- `notifications` now use `dedupe_key` as the stable parity identity when it is
  present, falling back to local `id` only for rows without a dedupe key.
- Tests cover offers, trades, and trade-delivery receipts where local IDs differ
  but business data matches, plus a real receipt-status drift that must still
  fail as `business_drift`.

Exit criteria:

- Strict parity can distinguish local identity differences from true product
  drift.
- The field matrix is documented and covered by tests.

## Stage F3A - Receiver Recency And Shared Evidence Policy

Goal: close smaller receiver-policy ambiguities before strict parity is trusted.

Problems:

- `user_notification_preferences` now has receiver coverage, but the generic
  `updated_at` guard allows incoming `updated_at = NULL` to update an existing
  non-null row. That may be acceptable for legacy compatibility, but it must not
  silently overwrite newer local preference state.
- `offer_publication_states` is a shared surface-state table, while Telegram
  message ids and provider/runtime details are foreign-local execution evidence.
  Parity and repair already treat several runtime fields as local-only, but the
  receiver/operator policy still needs to state whether normal sync payloads may
  persist those runtime fields as shared evidence.

Implementation direction:

- For `user_notification_preferences`, choose and implement one explicit policy:
  - strict recency: incoming `updated_at = NULL` cannot overwrite an existing
    non-null `updated_at`; or
  - compatibility mode: incoming `NULL` may apply only when the current row is
    missing or also has `updated_at = NULL`, and metrics/logs record that a
    legacy payload was accepted.
- Add tests for:
  - newer non-null incoming preference update applies;
  - older non-null incoming preference update is ignored;
  - incoming `updated_at = NULL` does not overwrite newer non-null state unless
    the chosen compatibility policy explicitly allows it;
  - missing `updated_at` legacy payload behavior is visible and documented.
- For `offer_publication_states`, document the final split between:
  - business/shared fields such as `dedupe_key`, `offer_public_id`, `surface`,
    `publication_owner_server`, `status`, and `offer_version_id`;
  - local Telegram/provider runtime fields such as chat/message ids, attempts,
    errors, and provider resource ids.
- If normal receiver payloads should not persist foreign-local runtime fields,
  sanitize or drop them on receive and add tests. If they are intentionally
  persisted as shared evidence, document that they must not be treated as strict
  business truth by parity or repair.

Implementation status on `candidate/sync-parity-hardening`:

- `user_notification_preferences` now uses strict `updated_at` recency on
  conflict: incoming `updated_at = NULL` can insert a missing row but cannot
  overwrite an existing non-null preference timestamp.
- `offer_publication_states` shared truth is limited to fields such as
  `dedupe_key`, `offer_public_id`, `offer_home_server`, `surface`,
  `publication_owner_server`, `status`, `offer_version_id`,
  `last_known_offer_status`, `disabled_at`, `lagged_at`, `archived`, and
  creation/update metadata.
- Telegram/provider runtime fields including `offer_id`, `surface_resource_id`,
  `telegram_chat_id`, `telegram_message_id`, retry timestamps, provider errors,
  and `state_metadata` are dropped from normal sync payloads and remain
  local execution evidence.

Exit criteria:

- Preference recency behavior is unambiguous and tested for `NULL` and non-null
  timestamps.
- `offer_publication_states` no longer has an undocumented mixed truth model.

## Stage F4 - Market Channel Notice Retry Path

Goal: make market open/close Telegram notice delivery recoverable after
transient Telegram failures.

Problem:

`reconcile_market_channel_notice_for_state()` records failed notices with
`next_retry_at`, but no worker or operator command scans due failed receipts.
A transient Telegram failure at the market transition moment can leave the
notice failed forever unless another transition or manual code path invokes the
reconciler.

Implementation direction:

- Add a foreign-only retry path for `market_channel_notice_receipts` where:
  - `status = failed`;
  - `next_retry_at <= now`;
  - the dedupe key remains unchanged;
  - sent receipts are never resent.
- Add a no-receipt recovery path for the current market transition. If the
  reconciler failed before creating or committing a receipt, a retry scanner
  cannot see a failed row. A current-state reconciliation command/job must be
  able to derive the current transition dedupe key, create the missing receipt,
  and send or skip idempotently.
- Add a runtime disable/degrade switch for the retry/reconciler path. Disabling
  it must stop Telegram side effects without deleting receipt rows, sync backlog,
  or parity evidence.
- The retry path may be either:
  - a small background job integrated with the existing market schedule loop; or
  - an explicit operator command plus a scheduled worker hook.
- Keep Iran behavior as no-op.
- Add tests for:
  - failed due receipt is retried and marked sent;
  - already sent receipt is skipped;
  - missing channel configuration remains skipped without retry noise;
  - retry failure updates `attempt_count`, `last_error`, and `next_retry_at`.

Implementation status on `candidate/sync-parity-hardening`:

- Added `TRADING_BOT_MARKET_CHANNEL_NOTICE_DISABLED` as a foreign-side degrade
  switch. When enabled, market notice reconciliation and retry skip Telegram
  side effects without creating or mutating receipt rows.
- Added `reconcile_due_market_channel_notice_receipts()` to retry due failed
  `market_channel_notice_receipts` rows on the foreign server with the existing
  dedupe key and the existing sent-receipt guard.
- Foreign market schedule cycles now run current-state notice recovery and due
  failed-receipt retry. Iran remains a no-op for Telegram notices.
- Retry limit is runtime-configurable through
  `TRADING_BOT_MARKET_NOTICE_RETRY_LIMIT`; invalid values fall back to the safe
  default instead of preventing service startup.

Exit criteria:

- A transient Telegram send failure does not permanently lose the market notice.
- Missing-receipt failures are recoverable by current-state reconciliation.
- Retry remains exactly-once for successful sends.
- Operators can temporarily disable market notice sending/retry without data
  loss and re-enable it later.

## Stage F5 - Documentation And Operator Model Refresh

Goal: align operator documentation with current code.

Required updates:

- `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md`
  - Rewrite current architecture so synced DB changes are described as durable
    `change_log` outbox rows drained by `sync_worker`.
  - Clarify that `sync:outbound` is at most a wake-up signal for DB sync.
  - Clarify that direct HTTP push still exists only for narrow non-DB
    notification relay paths unless a future post-commit DB push is introduced.
- `docs/CROSS_SERVER_SYNC_PARITY_AUDIT_AND_ROADMAP.md`
  - Mark the old `user_notification_preferences` receiver gap as fixed on this
    branch.
  - Keep the historical finding, but add current status and references to the
    receiver coverage and tests.
- `docs/CROSS_SERVER_SYNC_PARITY_REVIEW_REMEDIATION_ROADMAP.md`
  - Link this follow-up roadmap.
  - Mark remaining F-stage work as the active blocker list.
  - Update suggested execution-order text so a reviewer does not treat the old
    R-stages as the only active blockers.
- Root `Makefile`
  - Replace legacy `StrictHostKeyChecking=no` with `accept-new` or document why
    that helper is not production-authoritative.

Implementation status on `candidate/sync-parity-hardening`:

- `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md` now describes committed
  `change_log` rows as the durable database sync outbox, `sync_worker` as the
  DB delivery mechanism, `sync:outbound` as wake-up/compatibility state, and
  `push_sync_direct()` as a narrow non-DB relay helper.
- `docs/CROSS_SERVER_SYNC_PARITY_AUDIT_AND_ROADMAP.md` keeps the historical
  `user_notification_preferences` gap, but marks receiver coverage as fixed on
  this branch and references the current coverage/receiver tests.
- `docs/CROSS_SERVER_SYNC_PARITY_REVIEW_REMEDIATION_ROADMAP.md` now separates
  historical R-stage evidence from the active follow-up F-stage blocker list.
- Root `Makefile` uses `StrictHostKeyChecking=accept-new` for Iran SSH helper
  targets instead of disabling host-key checks.
- `core/sync_push.py` comments now match the current narrowed helper role and
  no longer imply DB event delivery should use direct HTTP push.

Exit criteria:

- A new reviewer does not get a stale mental model from docs.
- SSH host-key posture is consistent or explicitly scoped.

## Stage F6 - Artifact-Backed Parity Status

Goal: prevent strict operational decisions from relying only on a posted Redis
summary.

Problem:

`POST /api/sync/parity/status` stores a summarized operator-provided comparison
in Redis. This is acceptable for observability, but it is not enough for strict
fail-closed production behavior because the endpoint does not verify retained
snapshot artifacts, server IDs, commit SHA, mode, table count, or artifact hash.

Implementation direction:

- Keep the current endpoint as observability.
- Add optional artifact metadata fields to the posted comparison summary:
  - local server mode and peer server mode;
  - local and peer release SHA;
  - snapshot mode;
  - table counts;
  - snapshot timestamps;
  - full comparison artifact hash;
  - artifact path or storage reference.
- Do not fail current observability if metadata is missing.
- Make any future strict gate require artifact metadata and reject summaries
  without it.
- Add tests proving strict gate blocks missing artifact metadata when strict
  artifact mode is requested.

Implementation status on `candidate/sync-parity-hardening`:

- `core/sync_parity_observability.py` now normalizes optional
  `artifact_metadata`, reports whether it is complete, and lists missing fields.
- `strict_alert_gate_from_parity_summary(..., require_artifact_metadata=True)`
  now blocks otherwise-clean parity evidence when required artifact metadata is
  missing.
- `scripts/compare_sync_parity.py compare` carries artifact metadata from
  snapshots and optional CLI values such as `--comparison-artifact-hash` and
  `--artifact-reference`.
- `POST /api/sync/parity/status` remains backward-compatible but stores the
  normalized artifact metadata when present.
- Stage 9 strict alert planning has an optional
  `--require-artifact-backed-parity` flag for future fail-closed gates.
- `docs/CROSS_SERVER_SYNC_OBSERVABILITY.md` documents the artifact metadata
  contract and clarifies that monitoring does not require it while strict gates
  can.

Exit criteria:

- `/api/sync/parity/status` remains useful for monitoring.
- Strict production gates can require artifact-backed evidence without breaking
  existing staging workflows.

## Stage F7 - Two-Server Staging Evidence

Goal: prove the corrected behavior in a real two-server staging topology.

Required evidence:

- Iran staging and foreign staging use separate DB and Redis instances.
- Quick and deep parity snapshots are collected from both sides.
- Compare result is fresh, non-truncated, and clean or only documented
  non-business differences.
- Iran-origin market open transition syncs to foreign and sends exactly one
  Telegram channel notice.
- Iran-origin market close transition syncs to foreign and sends exactly one
  Telegram channel notice.
- Iran-origin market close transition syncs to foreign and expires foreign-home
  active offers exactly once under the final F1 authority model.
- Duplicate replay of the same `market_runtime_state` transition does not send
  a duplicate notice.
- Duplicate replay of the same market close transition does not re-expire or
  duplicate foreign-home offer side effects.
- A forced Telegram failure creates a failed receipt and the retry path repairs
  it.
- A forced no-receipt failure is repaired by current-state reconciliation.
- The market notice retry/reconciler disable switch stops side effects without
  deleting receipts, and re-enable resumes safe retry.
- `user_notification_preferences` recency tests cover `updated_at=NULL`
  behavior.
- Repair tool production-safety tests pass; no production apply is attempted.

Evidence collected on `candidate/sync-parity-hardening`:

- Artifact directory:
  `tmp/sync-parity-f7-evidence-20260628T112937Z/`.
- Remote Iran staging was validated as a separate staging app/DB/Redis stack on
  the Iran server, with release `220528b0c29a`.
- Local foreign staging and local Iran staging were also healthy, but they share
  the same local staging DB/Redis and therefore are not sufficient by
  themselves for the two-server evidence requirement.
- Deep snapshot compare between local foreign staging and remote Iran staging
  was clean across 20 synced tables: status `ok`, business drift `0`, critical
  drift `0`, incomplete tables `0`, duplicate identities `0`, and truncated
  tables `0`.
- The compare record includes artifact metadata:
  `local_server_mode=foreign`, `peer_server_mode=iran`, both release SHAs
  `220528b0c29a`, `snapshot_mode=deep`, table counts, snapshot timestamps,
  `artifact_reference`, and `comparison_artifact_hash`.
- `POST /api/sync/parity/status` accepted the artifact-backed comparison on
  local Iran staging, local foreign staging, and remote Iran staging.
- Final `/api/sync/health` on all three checked endpoints reported fresh parity
  status `ok`, no sync backlog, no missing comparison state, and
  `artifact_metadata_complete=true`.
- Live evidence directory:
  `tmp/sync-parity-f7-live-20260628T114500Z/`.
- Staging now has a profile-gated `staging-sync` compose surface for
  `sync_worker` and `foreign_sync_worker`. Normal staging deploys do not start
  these workers unless the profile is explicitly enabled.
- Local staging exposes `foreign_app` only on localhost
  `127.0.0.1:${STAGING_FOREIGN_APP_PORT:-8121}` and nginx exposes only the
  exact public sync ingress `/foreign-sync/api/sync/receive` to that service
  with staging basic-auth disabled for this exact path. The FastAPI sync HMAC
  authentication still protects the endpoint; an unsigned smoke request
  returned JSON `401` from FastAPI rather than nginx basic-auth.
- Remote Iran staging `sync_worker` was configured to use
  `https://staging.362514.ir/foreign-sync` as the foreign peer base URL and
  delivered `market_runtime_state` changes through the real public staging
  route.
- Clean controlled Iran-origin open and close transitions were applied with
  SQLAlchemy event listeners enabled so real `change_log` rows were generated.
  The Iran and local staging app schedule loops were paused during the clean
  transition window and restored afterward to avoid test races with the live
  schedule.
- The clean Iran-origin open transition synced to foreign and produced exactly
  one sent foreign Telegram channel notice receipt with `source=sync_receive`.
- The clean Iran-origin close transition synced to foreign and produced exactly
  one sent foreign Telegram channel notice receipt with `source=sync_receive`.
- Replaying the clean close sync item with the same source sequence did not
  create a second receipt and did not increment the existing receipt
  `attempt_count`; the close transition timestamp kept one sent receipt with one
  Telegram message id.
- A final deep parity compare after the live evidence was clean across 20 synced
  tables: status `ok`, business drift `0`, critical drift `0`, incomplete
  tables `0`, duplicate identities `0`, truncated tables `0`, and complete
  artifact metadata. The final compare was recorded to local Iran, local
  foreign, and remote Iran `/api/sync/parity/status`.
- Active foreign-home offer expiry evidence directory:
  `tmp/sync-parity-f7-offer-expiry-20260628T130024Z-valid/`.
- The first attempted active-offer fixture intentionally exposed a
  head-of-line sync blocker: foreign-origin `market_runtime_state` and
  foreign-created `commodities` rows were correctly rejected by Iran authority,
  but the worker did not previously classify `source_authority_forbidden:*` as
  terminal. The fix above was added before collecting the valid evidence.
- The valid active-offer fixture used an existing synced commodity and created a
  foreign-home active offer on the foreign staging DB. The offer synced to
  remote Iran staging as `ACTIVE` with the same `offer_public_id` and
  `home_server=foreign`.
- A controlled Iran-origin market close at
  `2026-06-28T13:02:38.976909Z` synced to foreign. Foreign reconciled the
  synced closed state, expired the foreign-home offer with
  `status=EXPIRED`, `expire_reason=market_closed`,
  `expire_source_server=foreign`, and synced that terminal offer state back to
  Iran.
- The same close transition replayed from remote Iran `change_log.id=12` did
  not duplicate side effects: the offer `expired_at` and `updated_at` stayed
  unchanged, the close notice stayed one row with `attempt_count=1`, and
  `closed_notice_count=1`.
- The post-expiry deep parity compare between local foreign staging and remote
  Iran staging reported `business_drift=0`, `critical_drift=0`,
  `incomplete=0`, `truncated_table_count=0`, `duplicate_identity_count=0`, and
  only documented non-business/local-only differences. The artifact-backed
  comparison was recorded to local foreign `/api/sync/parity/status`, and
  `/api/sync/health` reported parity `available` instead of `missing` with
  `unsynced_change_log_count=0`.
- Post-deploy F7 evidence directory:
  `tmp/sync-parity-post-deploy-20260628T1325Z/`.
- The terminal policy rejection fix was deployed to local staging and copied to
  remote Iran staging. Both local foreign staging and remote Iran staging were
  then refreshed with a new deep parity compare using release metadata
  `a50ecbb9` on both sides. Final `/api/sync/health` on both checked sides
  reported `fresh=true`, `comparison_status=non_business_difference`,
  `business_drift_count=0`, `critical_drift_count=0`, `incomplete_count=0`,
  `unsynced_change_log_count=0`, empty Redis sync queues, and complete artifact
  metadata.
- Market channel notice repair evidence is saved in
  `tmp/sync-parity-post-deploy-20260628T1325Z/f7-market-notice-repair-evidence.json`.
  The foreign staging evidence forced a Telegram send failure with an invalid
  channel id, preserved the failed receipt unchanged while
  `TRADING_BOT_MARKET_CHANNEL_NOTICE_DISABLED=1`, then re-enabled delivery and
  repaired the receipt to `sent` with exactly one retry. The same evidence also
  proved no-receipt reconciliation: a missing receipt for a unique transition
  was created and sent, and replaying the same transition returned
  `already_sent` without changing `attempt_count` or the Telegram message id.

Remaining F7 evidence before this stage can be called complete: none.

Exit criteria:

- Evidence artifacts are saved under `tmp/` with a short manifest.
- `/api/sync/health` on both sides reports fresh parity status, no backlog, and
  no misleading `missing` comparison state after the compare result is recorded.

## Merge And Production Criteria

The branch can be considered for merge only after:

- Stages F0 through F7, including F3A, are complete.
- All new tests pass.
- Existing sync, market transition, parity, and repair tests pass.
- Stale docs are refreshed.
- No production repair apply path remains available without a manifest and
  backup evidence.
- A fresh two-server staging evidence package exists.

Production rollout still requires a separate production preflight:

- fresh production backups;
- restore-smoke evidence for database backups;
- single Alembic head and successful migration evidence on both servers;
- both production sync workers running and draining;
- transport security evidence for `SYNC_VERIFY_TLS` and any configured CA
  bundle;
- no market notice retry backlog, no sync backlog, and no stale/conflict
  watermark spike after preflight;
- read-only production parity snapshot and compare;
- no active repair apply;
- strict alerts warning-only during the observation window;
- explicit user approval before deployment.
