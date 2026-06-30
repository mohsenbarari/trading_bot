# Production Release Follow-Up Issues - 2026-06-30

## Context

This document records the operational issues observed while releasing the Telegram tier1 customer invite flow to production on 2026-06-30.

The release eventually completed successfully and production health checks passed, but several release-path problems should be fixed during a closed-market maintenance window before relying on this flow for repeated low-risk production releases.

Released commit:

- `4fc10df7` - `Implement Telegram tier1 customer invite flow`

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

## Closed-Market Remediation Order

1. Add a pre-mutation release decision gate for `IRAN_CONNECTIVITY_MODE` and `IRAN_SHARED_DATA_MODE`.
2. Move Iran shared-data classification ahead of service mutation or make existing-data production mode explicitly resolve to `skip`.
3. Clean duplicate Iran Nginx staging config.
4. Improve healthcheck retry messages around app startup.
5. Add parity-status publication or explicit artifact-status reporting to the production release path.
6. Prepare a dry-run-only repair report for historical `offers` and `offer_publication_states` drift.
7. Reduce pip warning noise if it can be done without hiding real packaging problems.

## Validation Required After Remediation

- Run release script in dry-run/preflight mode and confirm missing release decisions fail before deploying foreign.
- Run release with existing Iran shared data and `IRAN_SHARED_DATA_MODE=skip`; confirm no shared data is reset or seeded.
- Confirm Nginx config test passes without duplicate `staging.gold-trade.ir` warnings.
- Confirm production healthcheck logs startup retries clearly and still fails on real timeout.
- Capture fresh quick/deep parity artifacts and confirm `/api/sync/health` reflects either fresh published status or an explicit unpublished-artifact state.
- Confirm bot tier1 customer invite still rejects when required sync queues/tables are not clean and succeeds when they are clean.
