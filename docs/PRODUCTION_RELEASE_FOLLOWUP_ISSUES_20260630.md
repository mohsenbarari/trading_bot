# Production Release Follow-Up Issues - 2026-06-30

## Context

This document records the operational issues observed while releasing the Telegram tier1 customer invite flow to production on 2026-06-30.

The release eventually completed successfully and production health checks passed, but several release-path problems should be fixed during a closed-market maintenance window before relying on this flow for repeated low-risk production releases.

Released commit:

- `4fc10df7` - `Implement Telegram tier1 customer invite flow`
- `375cbb88` - `Fix bot customer invite sync gate Redis fallback` (pending production release at the time this follow-up was expanded)

Production state after release:

- Foreign services were up.
- Iran services were up and healthy.
- Production data hygiene passed on both servers.
- Sync queues were clean on both servers.
- `OBSERVABILITY_API_KEY` was configured on both servers, which is required by the bot customer-invite sync gate.

## Issues Observed

### 1. Production release can mutate foreign before required Iran release decisions are known

Observed behavior:

- The first `make production-release` run deployed the foreign server successfully.
- The script then stopped at the Iran connectivity prompt in the non-interactive execution context:

```text
ERROR: Please answer yes or no.
```

Impact:

- The release path can leave foreign updated while Iran has not completed the same release flow.
- This creates a temporary version skew risk even when the script exits safely.

Required follow-up:

- Move all required non-interactive production decisions to preflight before any server mutation.
- For automated/operator-driven runs, require explicit environment values such as `IRAN_CONNECTIVITY_MODE=online` before deploying foreign.
- Fail fast if the script is running non-interactively and a required decision is missing.

### 2. Iran shared-data policy is validated too late in the release flow

Observed behavior:

- The second release run used `IRAN_CONNECTIVITY_MODE=online`.
- Iran services and migrations were already deployed.
- The script then inspected Iran shared data and stopped because production tables contained existing data:

```text
ERROR: Iran shared tables contain existing data. Set IRAN_SHARED_DATA_MODE=skip, reset, or abort.
```

Impact:

- The script can update services before discovering that the shared-data mode decision is missing.
- In production, the safe mode for existing data is `IRAN_SHARED_DATA_MODE=skip`, but the operator had to rerun the full release to provide it.

Required follow-up:

- Validate `IRAN_SHARED_DATA_MODE` before any foreign or Iran service mutation when existing Iran shared data is detected.
- Consider making `skip` the default only for an explicitly production-classified existing-data environment, while keeping `reset` opt-in with the existing strong confirmation.
- Keep `reset` impossible unless the current explicit confirmation text is provided.

### 3. Healthcheck can show transient connection-reset noise while Iran app is still starting

Observed behavior:

- During the successful release, the post-deploy healthcheck initially printed:

```text
curl: (56) Recv failure: Connection reset by peer
curl: (56) Recv failure: Connection reset by peer
```

- The same healthcheck later passed and the script exited with code `0`.

Impact:

- This is probably a harmless startup race, but it makes release logs look worse than the final state.
- Operators may waste time distinguishing expected startup retry noise from real failure.

Required follow-up:

- Make the healthcheck log retries explicitly, for example `waiting for Iran app readiness`.
- Suppress or label expected transient curl failures during the configured startup grace window.
- Keep the final healthcheck strict after the grace window.

### 4. Iran Nginx has duplicate staging server-name config

Observed behavior:

- Nginx syntax was valid, but this warning appeared repeatedly on Iran:

```text
conflicting server name "staging.gold-trade.ir" on 0.0.0.0:80, ignored
conflicting server name "staging.gold-trade.ir" on 0.0.0.0:443, ignored
```

Impact:

- Production `coin.gold-trade.ir` was healthy, so this did not block the release.
- The duplicate staging config can hide future staging routing mistakes and adds release-log noise.

Required follow-up:

- Audit `/etc/nginx/sites-enabled` and the rendered staging/production Nginx templates on Iran.
- Ensure exactly one active server block owns `staging.gold-trade.ir` for each listener.
- Keep production and staging domain configs separated and idempotent.

### 5. Post-deploy parity evidence is fresh but strict parity still reports historical drift

Observed behavior:

- Fresh post-deploy deep parity snapshots were captured.
- The deep comparison failed with:

```text
status: critical_drift
critical_drift_count: 2
business_drift_count: 0
```

Affected tables:

- `offers`
- `offer_publication_states`

Additional checks:

- Both production databases had the same offer status counts.
- Both production databases had `ACTIVE=0` offers at the time of inspection.
- The drift appears tied to terminal/historical offer/publication records rather than active market offers.
- `users`, `customer_relations`, `invitations`, `notifications`, and `trades` had no business mismatch in the fresh parity report.

Impact:

- The bot tier1 customer invite feature is not directly blocked because its required tables and sync queues were clean.
- Strict global parity alerting cannot be enabled safely until this historical drift is either repaired or explicitly classified with an accepted policy.

Evidence path:

- `tmp/production-postdeploy-parity-20260630T0928/postdeploy-parity-deep.json`
- `tmp/production-postdeploy-parity-20260630T0928/postdeploy-foreign-parity-deep.json`
- `tmp/production-postdeploy-parity-20260630T0928/postdeploy-iran-parity-deep.json`

Required follow-up:

- Build a dry-run repair/cleanup plan for historical terminal `offers` and `offer_publication_states`.
- Do not mutate production data until the repair plan is reviewed.
- Decide whether terminal historical offer publication drift should be repaired, archived, or policy-exempted.

### 6. `/api/sync/health` still reports stale parity status after local post-deploy comparison

Observed behavior:

- Fresh parity artifacts were created under `tmp/production-postdeploy-parity-20260630T0928`.
- `make sync-health` and `make sync-health-iran` still reported an older stale comparison from `2026-06-30T05:43:29Z`.

Impact:

- Operators can have fresh local parity evidence while runtime `/api/sync/health` still reports stale evidence.
- This weakens the operational value of `parity_status` during production release review.

Required follow-up:

- Ensure the post-deploy parity comparison is published to the runtime parity status endpoint when appropriate.
- If publishing is intentionally manual, document the command and make the release script print the exact artifact path plus publication status.
- Keep artifact metadata complete enough to avoid `artifact_metadata_complete=false` for retained release evidence.

### 7. Release logs contain noisy pip root-user warnings

Observed behavior:

- Wheel-cache preparation printed repeated pip warnings about running as root.

Impact:

- This did not block the release.
- It adds noise around the artifact build/cache stage.

Required follow-up:

- Either pass the appropriate pip root-user action flag for this controlled container/server context, or move wheel-cache generation into a virtual environment.
- Keep the warning visible if it can signal a real host-level packaging risk.

## Bot Invite Review Follow-Up Actions

The Claude review in `tmp/claude/bot-tier1-customer-invite-production-review.md` was compared against the current code after the Redis fallback fix.

### 8. Live bot invite gate failed because the bot runtime did not initialize the shared Redis singleton

Observed behavior:

- A real bot invite attempt returned:

```text
همگام‌سازی دو سرور کامل نیست. کمی بعد دوباره تلاش کنید.
```

- Production sync health was clean on both servers.
- Direct runtime probing inside the bot container showed:

```text
RuntimeError: Redis client not initialized. Call init_redis() first.
```

Impact:

- The customer-invite sync gate failed before it could check Iran health.
- This was a false negative, not a real cross-server sync failure.

Status:

- Fixed in `375cbb88`.
- The fix keeps the change narrow: when the Redis singleton is not initialized in the bot runtime, the customer-invite gate creates a temporary Redis client from the configured pool, performs the read-only queue check, and closes the temporary client.
- Regression coverage was added for the uninitialized-singleton bot runtime path.

Required follow-up:

- Production release is required for the live bot to load this fix.
- After release, verify `check_customer_invite_sync_ready(wait_seconds=0)` inside the bot container returns `ready=True` while queues are clean.

### 9. Internal customer-invite endpoint needs additional trust-boundary regression tests

Accepted review finding:

- The endpoint implementation is layered correctly, but tests should lock the trust boundary more explicitly.

Required follow-up:

- Add tests for source-server mismatch between payload and `X-Source-Server`.
- Add tests for calling the internal endpoint on a non-Iran server.
- Add tests for bad tier, bad `account_name`, and bad idempotency key.
- Add tests for invalid owner states:
  - owner not found
  - owner inactive
  - owner is a customer
  - owner is an accountant
- Add tests for Redis lock contention (`409`) and Redis lock unavailability (`503`).

### 10. Iran internal endpoint should re-assert the allowed owner role list

Accepted review finding:

- The bot already limits the feature to standard users, middle admins, and superadmins.
- Iran currently rejects deleted/inactive owners, customers, and accountants, but does not explicitly re-check the same owner role allow-list.

Impact:

- The HMAC-signed channel currently has only the bot as caller, so this is not an immediate exploit path.
- If another internal caller is added later, it could bypass the bot-side role allow-list.

Required follow-up:

- Add an explicit Iran-side role allow-list for:
  - `STANDARD`
  - `MIDDLE_MANAGER`
  - `SUPER_ADMIN`
- Return `403` for all other roles.
- Add endpoint tests for allowed and disallowed roles.

### 11. Bot onboarding acknowledgement should not escalate existing users into a required tutorial state through crafted callbacks

Accepted review finding:

- Existing users are not locked by the middleware.
- However, if a user with `bot_onboarding_required_step=0` manually sends an old/crafted offer-tutorial acknowledgement callback, the handler can raise `required_step` to the current required step and leave the user partially completed.

Impact:

- Low risk and self-inflicted, because normal users only receive these callbacks after Join Request onboarding is started.
- Still worth hardening because it is a small state-machine edge case.

Required follow-up:

- If `bot_onboarding_required_step < 1`, acknowledgement callbacks should not raise the required step.
- Add regression coverage that a legacy/existing user cannot become newly blocked by a crafted offer-tutorial acknowledgement callback.

### 12. Duplicate customer invitation protection should eventually get a database backstop

Accepted in principle, but requires careful design:

- Current no-migration release uses:
  - pre-check
  - Redis lock
  - post-lock pre-check
  - existing service validation
- This is acceptable for the current release.

Design constraint:

- A raw unique index on `invitations.mobile_number` or `invitations.account_name` is not acceptable; previous migrations intentionally removed those unique indexes so users can be re-invited after expiry.
- Any database uniqueness backstop must preserve re-invite-after-expiry behavior and customer lifecycle semantics.

Required follow-up:

- Design a partial uniqueness strategy for active/non-deleted pending customer invitations/relations.
- Run a read-only duplicate audit before migration.
- Add an `IntegrityError` mapping path that returns the existing `already_pending` behavior instead of a 500.
- Do not ship this migration without a dedicated closed-market review.

### 13. Customer-invite sync gate is intentionally strict, but needs observability before any relaxation

Review finding:

- Claude correctly noted that requiring Iran outbound backlog to be exactly zero can cause fail-closed UX blocks after recent Iran writes.

Decision:

- Do not relax this gate immediately.
- The current product policy is that bot-based customer invitation should only happen when the two-server sync state is normal and fully clean for the customer-invite tables.

Required follow-up:

- Add structured metrics or log aggregation for customer-invite gate reject reasons:
  - `foreign_queue_dirty`
  - `iran_sync_dirty`
  - `iran_health_unreachable`
  - `missing_observability_key`
  - `redis_unavailable`
- Review production reject frequency before changing the gate.
- If false negatives are frequent, propose a product decision separately:
  - keep exact-zero strictness
  - widen grace time
  - use freshness thresholds
  - or split safety-required checks from display-freshness checks

### 14. Runtime parity status staleness does not weaken the bot invite gate

Accepted clarification:

- The invite gate reads:
  - Redis queue state
  - Iran `/api/sync/health`
  - `unsynced_by_table` for required tables
  - `redis_ok`
- It does not authorize invites based on global `parity_status`.

Required follow-up:

- Keep issue 6 as an operator observability problem.
- Do not block bot invite solely because global parity evidence is stale if required invite tables and queues are clean.

### 15. Historical offer/publication drift must be confirmed terminal-only before any policy exemption

Accepted review clarification:

- The current evidence suggests the drift is terminal/historical, but that must be proven before repair or exemption.

Required follow-up:

- Before any parity policy exemption, run a read-only report that classifies every drifted `offers` and `offer_publication_states` identity by active/terminal state.
- No production data mutation is allowed until the report is reviewed.

### 16. Telegram channel message repair emits non-blocking `400 Bad Request` noise for old channel messages

Observed behavior:

- During the post-release log review for `e1faae92`, the foreign bot restarted cleanly and the offer publication worker began its reconciliation cycle.
- Telegram returned several non-blocking `400 Bad Request` responses for `editMessageText` and `editMessageReplyMarkup`.
- The worker completed the cycle with `status=ok`, `channel_state_applied=6`, and `channel_state_failed=1`.

Likely cause:

- These failures are consistent with old channel messages that were deleted manually, are no longer editable by Telegram, or have stale publication state.
- The bot runtime handled the errors without crashing and without blocking the release.

Impact:

- This does not affect the Telegram link-token sync-race fix or production health.
- It adds log noise and leaves at least one stale publication state that should be classified and handled deliberately.

Required follow-up:

- Add a closed-market read-only report for `offer_publication_states` rows whose Telegram edit/update fails with non-retryable `Bad Request`.
- Classify each failed row as active, terminal, deleted-message, or unknown before any mutation.
- For confirmed terminal/deleted-message rows, design an idempotent cleanup or archived-state transition so the publication worker stops retrying impossible edits.
- Keep active-offer publication repair strict; do not suppress active-offer Telegram publication failures without explicit operator visibility.

Additional evidence from the 2026-06-30 `a1a1aa2f` production release:

- Release log: `tmp/production-release-logs/production-release-20260630T193710Z.log`
- Foreign runtime log: `tmp/production-release-logs/foreign-runtime-after-release.log`
- The foreign bot started the admin broadcast worker successfully:

```text
core.telegram_admin_broadcast_worker - Telegram admin broadcast worker started
```

- The offer Telegram publication worker still emitted repeated `400 Bad Request` responses after restart. In this release log window, the saved foreign runtime log contains 30 `400 Bad Request` Telegram responses.
- The publication worker stayed alive and completed cycles with `status=ok`. It eventually reached a final observed cycle with `channel_state_processed=1`, `channel_state_applied=1`, and `channel_state_failed=0`, but earlier cycles still showed repeated failed channel-state applications.

### 17. Telegram publication worker can hit Telegram `429 Too Many Requests` after restart

Observed behavior during the 2026-06-30 `a1a1aa2f` production release:

- The foreign runtime log captured 115 Telegram `429 Too Many Requests` responses from `offer_telegram_publication` shortly after the bot restarted.
- The worker logged the HTTP responses as INFO and continued running; production health and sync checks passed.
- The observed cycles were still marked `job_result=success`, but the retry pressure was high enough to be operationally suspicious.

Evidence path:

- `tmp/production-release-logs/foreign-runtime-after-release.log`

Impact:

- This did not block the release.
- Repeated rate limits can delay channel-state convergence after restart and can hide whether the worker is pacing Telegram edits conservatively enough.
- The behavior is adjacent to issue 16 but distinct: `400 Bad Request` is likely stale/deleted-message state, while `429 Too Many Requests` indicates send/edit pacing pressure.

Required follow-up:

- Review `core.offer_publication_worker` pacing around Telegram edit/update calls, especially immediately after bot startup.
- Add structured aggregation for Telegram publication response classes per cycle: `2xx`, `400`, `429`, other `4xx`, `5xx`, transport errors.
- Honor Telegram `retry_after` when available and add bounded backoff or per-cycle cooldown so restart reconciliation does not repeatedly hit rate limits.
- Keep active-offer publication failures visible; do not silently suppress active-offer Telegram failures.

### 18. Generic PII redaction can over-redact UUID/run_id fields as national IDs

Observed behavior during the 2026-06-30 `a1a1aa2f` production release:

- Several structured bot logs showed `run_id` values partially redacted as `REDACTED_NATIONAL_ID`, for example:

```text
"run_id":"838ee21e-90ab-46e0-8ed2-ac[REDACTED_NATIONAL_ID]"
```

- The same foreign runtime log contains 12 occurrences of `REDACTED_NATIONAL_ID`.
- Telegram Bot API token redaction worked correctly and appeared as `bot[REDACTED]`.

Evidence path:

- `tmp/production-release-logs/foreign-runtime-after-release.log`

Impact:

- This does not leak sensitive data.
- It reduces operational usefulness of structured correlation IDs and makes release log analysis harder.

Required follow-up:

- Refine structured-log redaction so UUID-like fields such as `run_id`, `request_id`, and other known correlation IDs are not treated as national IDs.
- Keep real national-id/mobile/Telegram-token redaction strict.
- Add regression tests covering:
  - Telegram Bot API URL token redaction;
  - Persian/national-id redaction in free-text payloads;
  - no redaction of UUID correlation IDs in structured fields.

### 19. Iran connectivity monitor logs recurring `404 Not Found` for the foreign root URL

Observed behavior during the 2026-06-30 `bcefb3ac` production release:

- Iran runtime logs repeatedly showed the connectivity monitor/httpx probe calling the foreign root URL:

```text
HTTP Request: GET https://coin.362514.ir "HTTP/1.1 404 Not Found"
```

- At the same time, both Iran and foreign `/api/sync/health` checks reported:
  - `status=ok`
  - `redis_ok=true`
  - `unsynced_change_log_count=0`
  - `sync:outbound=0`
  - `sync:retry=0`
  - `publication_reconciliation.status=ok`

Impact:

- This did not block the release and does not indicate a sync failure by itself.
- It makes production logs noisier and can confuse operational review because the root URL is not the same as the actual sync/health receiver path.

Required follow-up:

- Audit the connectivity-monitor URL selection for production Iran.
- Prefer an explicit health/sync endpoint for reachability checks, or classify the foreign root `404` as an expected non-error only if it is intentionally used as a coarse reachability probe.
- Keep foreign WebApp/API public-surface blocking intact; do not make the foreign root or WebApp API public just to silence this log.

## Closed-Market Remediation Order

1. Add a pre-mutation release decision gate for `IRAN_CONNECTIVITY_MODE` and `IRAN_SHARED_DATA_MODE`.
2. Move Iran shared-data classification ahead of service mutation or make existing-data production mode explicitly resolve to `skip`.
3. Clean duplicate Iran Nginx staging config.
4. Improve healthcheck retry messages around app startup.
5. Add parity-status publication or explicit artifact-status reporting to the production release path.
6. Prepare a dry-run-only repair report for historical `offers` and `offer_publication_states` drift.
7. Add customer-invite trust-boundary tests and Iran-side role allow-list defense-in-depth.
8. Harden bot onboarding acknowledgement against crafted callbacks from users who were never required to onboard.
9. Add customer-invite gate reject-reason observability before considering any gate relaxation.
10. Design the database uniqueness backstop for duplicate customer invitations in a dedicated migration roadmap.
11. Reduce pip warning noise if it can be done without hiding real packaging problems.
12. Classify non-retryable Telegram `Bad Request` publication-state rows and stop impossible terminal/deleted-message retries safely.
13. Add Telegram publication-worker rate-limit pacing and response aggregation for `429 Too Many Requests`.
14. Refine log redaction so UUID correlation IDs are not partially redacted as national IDs.
15. Replace or explicitly classify the Iran connectivity-monitor foreign root probe that logs recurring `404 Not Found`.

## Validation Required After Remediation

- Run release script in dry-run/preflight mode and confirm missing release decisions fail before deploying foreign.
- Run release with existing Iran shared data and `IRAN_SHARED_DATA_MODE=skip`; confirm no shared data is reset or seeded.
- Confirm Nginx config test passes without duplicate `staging.gold-trade.ir` warnings.
- Confirm production healthcheck logs startup retries clearly and still fails on real timeout.
- Capture fresh quick/deep parity artifacts and confirm `/api/sync/health` reflects either fresh published status or an explicit unpublished-artifact state.
- Confirm bot tier1 customer invite still rejects when required sync queues/tables are not clean and succeeds when they are clean.
- Confirm the live bot runtime no longer rejects a clean sync state because the shared Redis singleton is uninitialized.
- Confirm the internal endpoint rejects non-Iran execution, source mismatch, invalid owner states, and invalid tier/account/idempotency payloads.
- Confirm existing bot users cannot be forced into onboarding by crafted acknowledgement callbacks.
- Confirm Telegram publication-state cleanup keeps active-offer failures visible while preventing repeated retries for confirmed terminal/deleted-message rows.
- Confirm Telegram publication-worker restart reconciliation no longer causes repeated `429 Too Many Requests` bursts.
- Confirm structured logs preserve UUID correlation IDs while still redacting real secrets and PII.
- Confirm Iran connectivity-monitor reachability logs are either successful against an explicit endpoint or clearly classified as expected non-error probes.

## Remediation Progress

### 2026-06-30 Telegram publication worker and redaction hardening

Status:

- Code remediation was deployed to production in the `bcefb3ac` release.

Completed for issues 16 and 17:

- `core.services.telegram_offer_channel_service.apply_offer_channel_state_with_result()` now returns a classified Telegram channel-state result while the existing `apply_offer_channel_state()` boolean contract remains available for current callers.
- Terminal/history channel edits now require both text update and button-removal update to succeed before reporting success; a failed button-removal call is no longer hidden behind a successful text edit.
- If `editMessageText` is rate-limited, the service stops before issuing the second Telegram edit call for the same post.
- `core.offer_publication_worker` now aggregates Telegram channel-state response classes per cycle, including `2xx`, `400`, `429`, `4xx`, `5xx`, `transport`, and `unknown` style classes.
- The worker honors Telegram `retry_after` when present, otherwise applies a bounded configured cooldown, and stops the current channel-state cycle on `429` instead of continuing to apply pressure.
- Confirmed terminal/deleted-message-like `400 Bad Request` failures are remembered per offer-state signature in memory so repeated cycles do not keep retrying the same impossible terminal edit.
- Active-offer `400 Bad Request` failures are intentionally not remembered; they stay visible and retryable because active-offer publication failures must not be silently suppressed.

Remaining limitation for issue 16:

- The non-retryable terminal `400` skip is in-memory only. A future closed-market maintenance task can add an operator-reviewed dry-run report and durable archive/cleanup policy for historical deleted-message publication rows if production logs still show unacceptable one-time restart noise.

Completed for issue 18:

- Generic PII redaction boundaries now avoid matching UUID/correlation-id substrings as mobile, card, or national-id values.
- Regression tests preserve structured `run_id` and `request_id` UUID values while still redacting standalone Persian national IDs, mobile numbers, card numbers, Telegram Bot API URLs, and other existing sensitive patterns.

Validation run:

- `python3 -m py_compile core/services/telegram_offer_channel_service.py core/offer_publication_worker.py core/log_redaction.py core/config.py`
- `python3 -m unittest tests.test_telegram_offer_channel_service tests.test_offer_publication_worker tests.test_logging_foundation`
- `python3 -m unittest tests.test_telegram_gateway_policy tests.test_job_logging`
- `python3 -m unittest tests.test_offer_expiry tests.test_market_transition_service tests.test_telegram_offer_channel_service tests.test_offer_publication_worker`
- `git diff --check`

### 2026-06-30 Claude follow-up: active publication/send-cycle pacing

Review source:

- `tmp/claude/telegram-publication-worker-hardening-review.md`

Accepted finding:

- The first hardening pass handled Telegram channel-state edit pacing and cooldown, but the active-offer publication/send repair cycle still did not expose classified `429` results to the worker.

Completed for issue 17:

- Added a classified `TelegramOfferSendResult` path for Telegram offer-channel sends while keeping the existing `send_offer_to_channel()` public callback backward-compatible as `Optional[int]`.
- Added `send_offer_to_channel_with_result()` for the worker so active publication repair can see Telegram response class, status code, `retry_after`, and error code without breaking existing direct callers.
- `publish_offer_to_telegram_channel_once()` now preserves classified send failures such as `telegram_rate_limited` instead of collapsing them into generic `telegram_send_empty_result`.
- `reconcile_offer_publications()` now stops the current active-publication repair batch on `429`, returns `telegram_rate_limited`, `telegram_retry_after_seconds`, and response-class counts, and reports `processed` as the actual processed finding count when the batch is stopped early.
- `core.offer_publication_worker` now maps active-publication `429` reports into the same bounded cooldown used for channel-state edits and logs `publication_rate_limited`, `publication_cooldown_seconds`, and `publication_response_counts`.
- Added configurable active send spacing via `offer_publication_worker_channel_send_spacing_seconds` with the same conservative default as edit spacing.

Validation run:

- `python3 -m py_compile api/routers/offers.py core/services/telegram_offer_publication_service.py core/services/offer_publication_reconciliation_service.py core/offer_publication_worker.py core/config.py`
- `python3 -m unittest tests.test_telegram_offer_publication_service tests.test_offer_publication_reconciliation_service tests.test_offer_publication_worker tests.test_offers_router_helpers`
- `python3 -m unittest tests.test_telegram_offer_channel_service tests.test_offer_publication_worker tests.test_logging_foundation tests.test_offer_expiry tests.test_market_transition_service tests.test_telegram_gateway_policy tests.test_job_logging tests.test_telegram_offer_publication_service tests.test_offer_publication_reconciliation_service tests.test_offers_router_helpers`
- `python3 -m unittest tests.test_bot_trade_create_confirm_success_wholesale tests.test_bot_trade_create_confirm_success_retail tests.test_bot_trade_create_text_offer_confirm_success tests.test_bot_trade_create_confirm_telegram_error tests.test_bot_trade_create_text_offer_failure_cancel tests.test_bot_trade_create_confirm_unexpected_error tests.test_bot_trade_create_text_offer_warning_confirm tests.test_bot_trade_create_text_offer_warning_flow_integration tests.test_offer_limit_cross_surface_smoke`
- `git diff --check`

Production release evidence:

- Release log: `tmp/production-release-logs/production-release-20260630T203445Z.log`
- Foreign runtime log: `tmp/production-release-logs/foreign-runtime-after-release-current.log`
- Iran runtime log: `tmp/production-release-logs/iran-runtime-after-release-current.log`
- `make production-release` completed with exit code `0`.
- Foreign Docker services were up after release: `app`, `bot`, `sync_worker`, `db`, and `redis`.
- Iran Docker services were up after release: `app`, `sync_worker`, `db`, and `redis`.
- `make sync-health` and `make sync-health-iran` both returned `status=ok`, `unsynced_change_log_count=0`, empty sync queues, and `publication_reconciliation.status=ok`.
- The first foreign offer-publication cycle after restart observed one Telegram `429` and applied a `37.0` second cooldown:

```text
channel_state_rate_limited=1
channel_state_cooldown_seconds=37.0
channel_state_response_counts={"2xx":8,"429":1,"transport":2}
```

- The next observed cycles converged without a persistent blocker; the third observed cycle reached:

```text
channel_state_processed=2
channel_state_applied=2
channel_state_failed=0
channel_state_response_counts={"2xx":2}
```

- A later recent-log snapshot showed no continuing Telegram `400 Bad Request` or `429 Too Many Requests` lines; only expected `foreign_surface.blocked` warnings remained on the foreign public surface.
- The old stale runtime parity status remains an operator-observability follow-up under issue 6; it did not block the release because live sync queues and publication reconciliation were clean on both servers.

### 2026-07-01 production release observation: Telegram publication restart noise persists but is bounded

Release:

- `8f9d2c21` - `Harden Telegram notification outbox retries`

Observed during post-release monitoring:

- `make production-release` completed with exit code `0`.
- `make production-online-health` passed after the release.
- Foreign services were up: `app`, `bot`, `sync_worker`, `db`, and `redis`.
- Iran services were up: `app`, `sync_worker`, `db`, and `redis`.
- Production data hygiene passed on both servers with `finding_count=0`.
- Recent filtered logs on both servers showed no `ERROR`, `CRITICAL`, `Traceback`, or unhandled exception entries.
- The foreign bot registered `TelegramNotificationOutbox` listeners and started the notification outbox worker successfully.
- Sync health samples on foreign reported clean queues and `publication_reconciliation_status=ok`.

Suspicious but non-blocking Telegram publication evidence:

- The foreign bot still emitted repeated Telegram `400 Bad Request` responses for `editMessageText` and `editMessageReplyMarkup` shortly after restart.
- The first observed cycle also hit one Telegram `429 Too Many Requests`.
- The worker handled the cycle without crashing and reported `job_result=success`.
- The cycle-level aggregation showed bounded behavior rather than an unhandled failure:

```text
channel_state_processed=11
channel_state_applied=9
channel_state_failed=2
channel_state_rate_limited=1
channel_state_retryable_failed=2
channel_state_cooldown_seconds=40.0
channel_state_response_counts={"2xx":9,"429":1,"transport":1}
```

Required follow-up:

- Keep issue 16 open: classify old/deleted/stale publication rows so impossible terminal edits do not create repeated restart noise.
- Keep issue 17 open: continue observing Telegram restart pacing and confirm `429` bursts stay bounded after future releases.
- Do not treat this release observation as a production blocker because service health, sync queues, publication reconciliation, and data hygiene were clean.
