# Automated Test Checklist

This file is the repository checklist for systematic automated test coverage.

Scope rules:
- Target full coverage over time for executable product behavior, not blind line-count chasing.
- Static, generated, bundled, binary, and third-party artifact directories are validated with build/smoke checks rather than per-line unit tests.
- A phase is checked only after its focused validation command passes.

## Phase Status

- [x] Phase 0 - Inventory, baseline commands, and repository-wide checklist created.
- [x] Phase 1 - Critical backend routing/forwarding baseline.
- [x] Phase 2 - Core service layer expansion.
- [ ] Phase 3 - API router integration coverage.
- [ ] Phase 4 - Bot handler and FSM coverage.
- [ ] Phase 5 - Frontend unit/component baseline.
- [ ] Phase 6 - Frontend E2E expansion.
- [ ] Phase 7 - Models, migrations, startup, and deployment smoke coverage.
- [ ] Phase 8 - Load, sync, resilience, and regression hardening.

## Phase 0 - Inventory and Baseline

- [x] Inventory current backend tests under `tests/`.
- [x] Inventory current frontend E2E tests under `frontend/e2e/`.
- [x] Confirm backend baseline command works: `python -m unittest tests.test_manual_offer_validation tests.test_user_deletion_service tests.test_sync_coverage`.
- [x] Create this repository checklist file.
- [x] Define the coverage policy for static/build directories.

## Phase 1 - Critical Backend Routing/Forwarding Baseline

- [x] Add unit tests for `core/server_routing.py` host/server resolution.
- [x] Add unit tests for `core/trade_forwarding.py` signature validation.
- [x] Add unit tests for `core/trade_forwarding.py` success/error forwarding behavior.
- [x] Run focused validation for the new routing/forwarding tests.
- [x] Mark Phase 1 complete only after the focused validation passes.

## Phase 2 - Core Service Layer Expansion

- [x] Cover `core/services/trade_service.py` quantity, price, lot helpers, retail-lot availability, competitive-price, and race-recovery logic.
- [x] Cover `core/services/session_service.py` limits, approval, revoke, suspend, and revive logic.
- [x] Cover `core/services/chat_service.py` pure reaction and mutation helpers.
- [x] Cover `core/services/chat_service.py` direct-chat create/member-sync rules.
- [x] Cover `core/services/chat_service.py` direct-message query builders and lookup bridging.
- [x] Cover `core/services/chat_service.py` edit/reaction/delete precondition guards.
- [x] Cover `core/services/chat_service.py` direct-chat send/read/search/mutation orchestration rules.
- [x] Cover `core/services/chat_room_service.py` event payload and broadcast helpers.
- [x] Cover `core/services/chat_room_service.py` active membership/admin access guards.
- [x] Cover `core/services/chat_room_service.py` group member add/reactivate/limit rules.
- [x] Cover `core/services/chat_room_service.py` group admin promote/demote invariants.
- [x] Cover `core/services/chat_room_service.py` group remove/leave membership mutations.
- [x] Cover `core/services/chat_room_service.py` channel bulk-add and member-update mutations.
- [x] Cover `core/services/chat_room_service.py` channel/group send and read-state orchestration helpers.
- [x] Cover `core/services/chat_room_service.py` optional-channel admin read-model helpers.
- [x] Cover `core/services/chat_room_service.py` group/channel conversation, member, and room-message query helpers.
- [x] Cover `core/services/chat_room_service.py` room lookup, count/list-active, create/update, and remaining helper rules.
- [x] Cover `core/services/user_deletion_service.py` rollback and negative-path behavior beyond existing tests.
- [x] Cover `core/services/block_service.py` invariants.
- [x] Cover `core/services/chat_backfill_service.py` invariants.
- [x] Cover `core/server_routing.py` edge cases.
- [x] Cover `core/notifications.py` edge cases.
- [x] Cover `core/sync_push.py` edge cases.
- [x] Cover `core/offer_expiry.py` edge cases.
- [x] Cover `core/sync_worker.py` edge cases.

## Phase 3 - API Router Integration Coverage

- [x] Cover `api/routers/auth.py` refresh and setup-password flows.
- [x] Cover `api/routers/auth.py` invitation registration OTP/request/verify/complete flows.
- [x] Cover `api/routers/auth.py` direct login OTP request/resend/verify flows.
- [x] Cover `api/routers/auth.py` dev-login and web-login/session approval flows.
- [x] Cover `api/routers/invitations.py` create/reuse/expire/validate flows.
- [x] Cover `api/routers/offers.py` list/my-offers/parse flows.
- [x] Cover `api/routers/offers.py` expire endpoint and related limit/error flows.
- [x] Cover `api/routers/offers.py` create endpoint guard and validation branches.
- [ ] Cover remaining `api/routers/offers.py` successful create/home-server behavior.
- [ ] Cover `api/routers/trades.py` execute/internal execute/forwarding/error paths.
- [x] Cover `api/routers/chat.py` group management endpoints.
- [x] Cover `api/routers/chat.py` channel management endpoints.
- [x] Cover `api/routers/chat.py` room send/read/message-list endpoints.
- [x] Cover `api/routers/chat.py` direct conversations/search/history/typing/read/poll endpoints.
- [x] Cover `api/routers/chat.py` direct send/edit/delete/reaction endpoints.
- [x] Cover `api/routers/chat.py` media/file flows.
- [ ] Cover `api/routers/sessions.py`, `api/routers/notifications.py`, and `api/routers/sync.py` runtime contracts.
- [ ] Cover remaining routers: `users.py`, `users_public.py`, `blocks.py`, `commodities.py`, `realtime.py`, `trading_settings.py`, `config` surface via `main.py`.

## Phase 4 - Bot Handler and FSM Coverage

- [ ] Cover onboarding handlers in `bot/handlers/start.py` and `bot/handlers/link_account.py`.
- [ ] Cover offer creation/execution/management handlers in `trade_create.py`, `trade_execute.py`, `trade_manage.py`, `trade_history.py`, and `trade_utils.py`.
- [ ] Cover admin flows in `admin.py`, `admin_users.py`, and `admin_commodities.py`.
- [ ] Cover user-control flows in `panel.py`, `block_manage.py`, and `default.py`.

## Phase 5 - Frontend Unit/Component Baseline

- [ ] Add a runnable Vitest command in `frontend/package.json`.
- [ ] Cover `frontend/src/utils/auth.ts` token refresh, reconnect, and storage behavior.
- [ ] Cover `frontend/src/router/index.ts` auth guard and route recovery behavior.
- [ ] Cover `frontend/src/stores/notifications.ts` and notification normalization helpers.
- [ ] Cover selected composables under `frontend/src/composables/` with deterministic logic first.
- [ ] Add component tests for key stateful views/components such as `LoginView.vue`, `ProfileView.vue`, and focused non-chat UI slices.

## Phase 6 - Frontend E2E Expansion

- [ ] Preserve current Playwright coverage in `frontend/e2e/notifications.spec.ts`, `frontend/e2e/lot-suggestion.spec.ts`, and `frontend/e2e/channel-media.spec.ts`.
- [ ] Add login/auth E2E coverage.
- [ ] Add market/offer creation E2E coverage.
- [ ] Add direct chat E2E coverage beyond channel/group/share-target paths.
- [ ] Add admin E2E smoke coverage for invitations, users, settings, and optional channels.

## Phase 7 - Models, Migrations, Startup, and Deployment Smoke Coverage

- [ ] Cover behaviorful model helpers in `models/`.
- [ ] Add migration/alembic smoke validation for `alembic/` and `migrations/`.
- [ ] Add startup/import smoke checks for `main.py`, `run_bot.py`, and `manage.py`.
- [ ] Add smoke validation for deploy/build surfaces: `Dockerfile*`, `docker-compose*.yml`, `deploy.sh`, `Makefile`, `nginx.conf`.

## Phase 8 - Load, Sync, Resilience, and Regression Hardening

- [ ] Normalize and retain nonfunctional scripts in `tests/api_load_test.py`, `tests/load_test.py`, `tests/live_simulation.py`, and `tests/debug_trade.py` as explicit non-regression tools.
- [ ] Add focused regression tests for every future production bugfix.
- [ ] Add coverage gates/reporting once the repository has meaningful breadth across backend, bot, and frontend.

## Directory Coverage Checklist

- [ ] Root runtime files: `main.py`, `run_bot.py`, `manage.py`, `schemas.py`, `deploy.sh`, `Makefile`.
- [ ] `api/`
- [ ] `bot/`
- [ ] `core/`
- [ ] `models/`
- [ ] `tests/`
- [ ] `frontend/src/`
- [ ] `frontend/e2e/`
- [ ] `alembic/` and `migrations/`
- [ ] `scripts/`
- [ ] Smoke/build validation only: `mini_app_dist/`, `templates/`, `fonts/`, `map_data/`, `pip_packages/`, root `src/`, generated caches, and browser/build artifacts.