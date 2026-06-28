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
- Production SSH tooling is improved in the online production deploy script, but
  the root `Makefile` still contains `StrictHostKeyChecking=no` for legacy Iran
  helper targets.

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
