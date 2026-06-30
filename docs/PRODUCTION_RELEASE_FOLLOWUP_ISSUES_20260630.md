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
