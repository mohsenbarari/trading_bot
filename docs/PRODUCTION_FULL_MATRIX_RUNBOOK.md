# Production Full Matrix Runbook

Date: 2026-06-24

Purpose: define the safe operating contract for a large production full matrix
run while real users are temporarily blocked from the Iran WebApp and only the
owner-approved test cohort can use production.

This runbook covers isolation, dry-run, cleanup, and the production preparation
sequence. It does not authorize production deploys and it must not be used to
change Telegram credentials.

## Safety Contract

- Run from `main` only, with a clean worktree and the currently deployed commit
  known.
- Production WebApp isolation must be enabled before any synthetic production
  user, offer, trade, notification, or receipt is created.
- The isolation allowlist must include only the owner-approved real operator
  account and synthetic test prefixes.
- Current approved operator account: `mohsen`.
- Current approved synthetic account prefixes: `PFM_`, `PRODTEST_`, `FMX_`.
- Every synthetic row must be identifiable by one exact run prefix.
- The run prefix must start with `PFM_`, `PRODTEST_`, or `FMX_`, must be at
  least 8 characters, and must not contain wildcard characters.
- Do not print or store Telegram bot tokens in artifacts or logs.
- Run cleanup dry-run on both production databases before the matrix starts.
- Run cleanup dry-run again before hard delete after the matrix finishes.
- Hard delete is allowed only for the exact run prefix and only with both the
  command flag and the confirmation environment variable.
- Run cleanup on both servers. Deleting only one side is not enough because the
  product tables are cross-server synced.
- Keep production isolation enabled until cleanup verification is complete.
- If the matrix is interrupted, keep isolation enabled, collect artifacts, then
  run dry-run cleanup and hard delete for the exact prefix on both servers.
- Disable isolation only after the prefixed production data is removed or the
  owner explicitly accepts leaving the system isolated for follow-up work.

## Current Runtime Scope

The large automated matrix can use real production databases, real production
server placement, real sync, real offer/trade services, and aiogram Dispatcher
for Telegram-side business logic.

The huge automated run must not require thousands of real Telegram accounts.
For high-volume synthetic Telegram paths, the safest boundary remains an
in-process bot/Dispatcher simulation or a fake Telegram transport. A small
owner-led manual Telegram E2E pass remains required for real Bot API/channel
evidence after the automated matrix.

## Isolation Commands

Check current status on Iran:

```bash
ssh root@87.107.3.22 'cd /srv/trading-bot/current && docker-compose exec -T app python scripts/production_test_isolation.py status'
```

Enable production full-matrix isolation:

```bash
ssh root@87.107.3.22 'cd /srv/trading-bot/current && docker-compose exec -T app python scripts/production_test_isolation.py enable --allow-account-name mohsen --allow-account-prefix PFM_ --allow-account-prefix PRODTEST_ --allow-account-prefix FMX_ --reason production_full_matrix_YYYYMMDD --yes-production'
```

Disable isolation after cleanup and verification:

```bash
ssh root@87.107.3.22 'cd /srv/trading-bot/current && docker-compose exec -T app python scripts/production_test_isolation.py disable'
```

Expected isolation behavior:

- Existing real user sessions are not revoked.
- Blocked users should not need to log in again after isolation is disabled.
- HTTP requests from blocked users receive a temporary unavailable response.
- WebSocket connections from blocked users are rejected during isolation.
- WebApp notifications and realtime events for blocked users are suppressed
  during isolation.
- Allowed users and synthetic prefixed accounts continue to receive WebApp
  notifications and realtime events.

## Dry-Run Cleanup

Use the exact run prefix. Example:

```bash
PREFIX='PFM_20260624_180000_'
```

Foreign dry-run:

```bash
docker compose exec -T app python scripts/trading_core_probe_worker.py cleanup --prefix "$PREFIX" --dry-run --artifact "/tmp/${PREFIX}foreign-core-cleanup-dry-run.json"
```

Iran dry-run:

```bash
ssh root@87.107.3.22 "cd /srv/trading-bot/current && docker-compose exec -T app python scripts/trading_core_probe_worker.py cleanup --prefix '$PREFIX' --dry-run --artifact '/tmp/${PREFIX}iran-core-cleanup-dry-run.json'"
```

The dry-run must be captured before and after the matrix. Before the matrix,
the planned counts should normally be zero for a new prefix. After the matrix,
the planned counts define exactly what will be deleted.

## Hard Delete Cleanup

Hard delete requires both safeguards:

- `--allow-production-hard-delete`
- `PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data`

Foreign hard delete:

```bash
docker compose exec -T -e PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data app python scripts/trading_core_probe_worker.py cleanup --prefix "$PREFIX" --allow-production-hard-delete --artifact "/tmp/${PREFIX}foreign-core-cleanup-hard-delete.json"
```

Iran hard delete:

```bash
ssh root@87.107.3.22 "cd /srv/trading-bot/current && docker-compose exec -T -e PRODUCTION_TEST_CLEANUP_CONFIRM=hard-delete-test-data app python scripts/trading_core_probe_worker.py cleanup --prefix '$PREFIX' --allow-production-hard-delete --artifact '/tmp/${PREFIX}iran-core-cleanup-hard-delete.json'"
```

After hard delete, run dry-run again on both servers. The expected result is
zero planned rows for users, offers, trades, notifications, delivery receipts,
offer requests, publication states, relations, sessions, and Redis runtime
keys.

## Stage L Fixture Warning

`scripts/load_fixture_worker.py cleanup` belongs to the Stage L realistic load
fixture path. It is prefix-scoped, but it currently does not provide the same
production hard-delete confirmation contract as
`scripts/trading_core_probe_worker.py cleanup`.

Do not mix Stage L fixture data into the production Bot/WebApp full matrix
unless a separate dry-run/production guard is added for that fixture cleanup
path or the owner explicitly approves the risk for one exact prefix.

## Production Full Matrix Preparation

Generate a side-effect-free command plan:

```bash
make production-full-matrix-plan ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --json"
```

The generated plan must be stored in the artifact directory before execution.
It includes:

- git/worktree preflight commands,
- production service status commands,
- isolation status and enable commands,
- pre-run dry-run cleanup commands for both servers,
- static scenario catalog commands,
- the human-readable scenario catalog at
  `docs/PRODUCTION_FULL_MATRIX_SCENARIO_CATALOG.md`,
- the runner-consumable manifest contract at
  `docs/PRODUCTION_FULL_MATRIX_MANIFEST.md`,
- the generated JSON manifest artifact from
  `scripts/build_production_full_matrix_manifest.py`,
- the manifest-driven run-plan artifact from
  `scripts/run_production_full_matrix.py`,
- post-run dry-run and hard-delete cleanup commands for both servers,
- isolation disable command.

The plan generator does not mutate production. It is intentionally separate
from the eventual executor so the operator can inspect the commands before any
production action.

The scenario catalog is part of the safety contract. A production full matrix
that does not cover the catalog axes, policy-unsupported negative cases,
outage classes, customer-chain paths, notification recipients, and cleanup
verification must be treated as incomplete even if the load runner exits
successfully.

The generated manifest is the bridge between the catalog and the eventual
runner. The runner must consume the manifest and report pass/fail/skip evidence
by `manifest_id`; hand-built scenario lists are not enough for a production
full matrix.

Current runner status: `scripts/run_production_full_matrix.py` consumes the
manifest, applies filters/sharding, and writes a run plan. It does not perform
production writes yet. This fail-closed behavior is intentional until the
two-server production drivers are implemented and separately reviewed.

The same runner can execute a live non-mutating preflight with a separate
confirmation variable:

```bash
PRODUCTION_FULL_MATRIX_PREFLIGHT_CONFIRM=run-production-preflight \
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode preflight --execute --output /tmp/production-full-matrix-preflight-result.json"
```

This preflight is allowed to read live service status and run cleanup dry-run
queries. It must not create or mutate production market data.

Generate the guarded execution command plan:

```bash
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode execution-plan --output /tmp/production-full-matrix-execution-plan.json"
```

This command still does not mutate production. It produces:

- per-scenario command groups for implemented production drivers;
- explicit `driver_gaps` for unimplemented production drivers;
- separate `prepare_and_distribute`, concurrent `role_workers`, and
  `collect_merge_finalize` groups for two-server role-worker scenarios;
- role-worker commands using `--patch-external-side-effects`, not
  `--patch-boundaries`.

For the production release gate, require complete command-driver coverage:

```bash
make production-full-matrix-run ARGS="--prefix PFM_YYYYMMDD_HHMMSS_ --mode execution-plan --require-full-driver-coverage --output /tmp/production-full-matrix-execution-plan.json"
```

This command is still side-effect free. It must exit with status
`blocked_driver_gaps` and exit code `2` while any selected scenario lacks a
production driver. A real full-matrix execution must not start unless this
coverage gate passes for the selected scope.

The execution-plan artifact includes `driver_gap_summary` and
`driver_gap_roadmap`. The roadmap groups missing production drivers into
implementation buckets so the next work item can be chosen by remaining count
and difficulty rather than by raw `manifest_id` lists.

Current execution-plan limitation:

- implemented: user-to-user stable hot-offer paths for the base trade shape
  section and hot-offer stress overlays, across WebApp/WebApp, WebApp/Telegram,
  Telegram/WebApp, and Telegram/Telegram quadrants;
- implemented: duplicate replay stress paths for user-to-user stable scenarios
  across WebApp and Telegram request surfaces, WebApp and Telegram offer
  origins, both offer types, and all current offer shapes;
- implemented: manual-expiry/trade-race stress paths for user-to-user stable
  scenarios across WebApp and Telegram request surfaces, WebApp and Telegram
  offer origins, both offer types, and all current offer shapes;
- implemented: time-expiry/trade-race stress paths for user-to-user stable
  scenarios across WebApp and Telegram request surfaces, WebApp and Telegram
  offer origins, both offer types, and all current offer shapes;
- implemented: read-during-write stress paths for user-to-user stable scenarios
  with concurrent trade writes plus WebApp/Iran and Telegram/foreign read
  probes against the same offer;
- implemented: thirteen Iran/WebApp negative-guard probes:
  `own_offer_request`, `invalid_request_amount`, `retail_lot_unavailable`,
  `already_completed_offer`, `manually_expired_offer`, `time_expired_offer`,
  `market_closed`, `inactive_offer_owner`, `inactive_requester`,
  `trading_restricted_user`, `watch_role_market_action`,
  `accountant_market_action`, and `tier2_offer_creation`;
- current whole-manifest command-plannable count: `173` of `5555` scenarios;
- not implemented yet: customer/accountant actor-pair production drivers,
  short/medium outage orchestration, targeted delivery join production driver,
  and the remaining negative business guard production driver cases.

Do not treat a run as a full production pass while `driver_gap_count > 0`.
Those gaps are intentionally emitted and summarized by section and reason so
missing coverage cannot be hidden. Use `--require-full-driver-coverage` as the
machine-enforced gate for release readiness.

Current driver-gap roadmap for the full manifest:

1. `negative_guard_driver`: `586` gaps. Add explicit production reject-path
   probes and no-partial-mutation assertions.
2. `market_behavior_driver`: `228` gaps. Port the comprehensive market matrix
   to production-safe two-server execution.
3. `delivery_contract_driver`: `204` gaps. Assert delivery receipts and
   notifications after real trade evidence exists.
4. `targeted_join_driver`: `204` gaps. Convert the targeted join matrix from
   staging/patched execution to production two-server execution.
5. `outage_orchestration_driver`: `320` gaps. Add reversible short/medium
   outage control with distinct expected outcomes.
6. `customer_accountant_actor_driver`: `3840` gaps. Add production fixtures and
   assertions for customer, owner, accountant, same-owner, and different-owner
   actor pairs.

## Matrix Evidence Required

The production run should collect at least:

- production isolation status before, during, and after the run,
- foreign and Iran compose status,
- public Iran `/api/config` smoke result,
- sanitized Telegram runtime identity hash, never the token,
- sync health before and after,
- pre-run cleanup dry-run artifacts,
- scenario catalog artifacts,
- generated production full-matrix manifest artifact,
- generated production full-matrix run-plan artifact,
- generated production full-matrix preflight-plan artifact,
- generated production full-matrix execution-plan artifact,
- execution artifacts for offer creation, concurrent trade, non-concurrent
  trade, manual expiry, time expiry, history/detail views, and trade delivery,
- post-run cleanup dry-run artifacts,
- hard-delete cleanup artifacts,
- post-delete zero-count cleanup dry-run artifacts,
- app, bot, sync worker, DB, Redis, and Nginx logs for the run window.

## Stop Conditions

Stop the run and keep isolation enabled if any of these happens:

- isolation is disabled unexpectedly,
- a real non-allowed user can access the WebApp,
- real non-test users receive WebApp notifications,
- cleanup dry-run matches rows outside the exact prefix contract,
- foreign serves WebApp/frontend routes,
- Iran attempts a Telegram network action,
- sync backlog grows without recovery,
- offers or trades are created without the run prefix,
- hard-delete verification does not return zero prefixed rows after cleanup.
