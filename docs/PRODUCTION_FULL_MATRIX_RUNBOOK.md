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
