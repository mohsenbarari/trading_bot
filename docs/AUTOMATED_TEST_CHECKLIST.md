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
- [x] Phase 3 - API router integration coverage.
- [x] Phase 4 - Bot handler and FSM coverage.
- [x] Phase 5 - Frontend unit/component baseline.
- [x] Phase 6 - Frontend E2E expansion.
- [x] Phase 7 - Models, migrations, startup, and deployment smoke coverage.
- [x] Phase 8 - Load, sync, resilience, and regression hardening.

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
- [x] Cover `api/routers/offers.py` successful create/home-server behavior.
- [x] Cover `api/routers/trades.py` read endpoints (`/my`, `/{id}`, `/with/{user}`).
- [x] Cover `api/routers/trades.py` forwarding and internal-execute wrapper paths.
- [x] Cover `api/routers/trades.py` authoritative execute guard/error branches.
- [x] Cover `api/routers/trades.py` successful authoritative execution and side effects.
- [x] Cover `api/routers/chat.py` group management endpoints.
- [x] Cover `api/routers/chat.py` channel management endpoints.
- [x] Cover `api/routers/chat.py` room send/read/message-list endpoints.
- [x] Cover `api/routers/chat.py` direct conversations/search/history/typing/read/poll endpoints.
- [x] Cover `api/routers/chat.py` direct send/edit/delete/reaction endpoints.
- [x] Cover `api/routers/chat.py` media/file flows.
- [x] Cover `api/routers/sessions.py` verify/active/terminate/logout runtime contracts.
- [x] Cover `api/routers/sessions.py` login-request approval/poll contracts.
- [x] Cover `api/routers/notifications.py` read/mutation/stream runtime contracts.
- [x] Cover `api/routers/sync.py` signature verification, parse helpers, and `resync` runtime contracts.
- [x] Cover `api/routers/sync.py` `_apply_item` merge/defer/delete branches and baseline `receive` success/error contracts.
- [x] Cover `api/routers/sync.py` sequence repair, unread/cache-refresh edge cases, and foreign-offer publish branches.
- [x] Cover `api/routers/users_public.py` public search and read endpoints.
- [x] Cover `api/routers/trading_settings.py` get/update/reset flows.
- [x] Cover `api/routers/blocks.py` status/list/block/unblock/check/search flows.
- [x] Cover `api/routers/commodities.py` request-source helper, commodity CRUD, and alias mutation flows.
- [x] Cover `main.py` lifespan, public config, frontend serving, and root/config surface.
- [x] Cover `api/routers/realtime.py` helper, SSE, Redis-listener, publish, and WebSocket guard/runtime flows.
- [x] Cover `api/routers/users.py` helper utilities and notification helpers.
- [x] Cover `api/routers/users.py` read/update/delete endpoint flows.

## Phase 4 - Bot Handler and FSM Coverage

- [x] Cover account-link onboarding handlers in `bot/handlers/link_account.py`.
- [x] Cover onboarding and trade-confirmation handlers in `bot/handlers/start.py`.
- [x] Cover offer management handler in `trade_manage.py`.
- [x] Cover trade history keyboard/query/format/navigation and export callback flows in `trade_history.py`.
- [x] Cover trade history file-generation helpers in `trade_history.py`.
- [x] Cover offer execution and suggestion handlers in `trade_execute.py`.
- [x] Cover trade keyboard/helper builders in `trade_utils.py`.
- [x] Cover early FSM creation handlers in `trade_create.py` through type/commodity/quantity/lot selection.
- [x] Cover price/notes/preview handlers and pre-submit confirm guards in `trade_create.py`.
- [x] Cover successful channel publish/persistence flow in `trade_create.py`.
- [x] Cover text-offer parse/confirm/cancel handlers in `trade_create.py`.
- [x] Cover invitation admin flows in `admin.py`.
- [x] Cover `admin_commodities.py` helpers, view rendering, entry handlers, and alias-add flow.
- [x] Cover remaining commodity edit/delete/add/cancel flows in `admin_commodities.py`.
- [x] Cover `admin_users.py` helpers, user-list/profile navigation, and search entry/cancel flows.
- [x] Cover `admin_users.py` search-processing, settings menu, and block/unblock/unlimit flows.
- [x] Cover `admin_users.py` role, bot-access, and delete flows.
- [x] Cover remaining admin user limitation and block-setting flows in `admin_users.py`.
- [x] Cover unauthorized fallback flow in `bot/handlers/default.py`.
- [x] Cover user block-management flows in `block_manage.py`.
- [x] Cover user-control flows in `panel.py`.

## Phase 5 - Frontend Unit/Component Baseline

- [x] Add a runnable Vitest command in `frontend/package.json`.
- [x] Cover `frontend/src/utils/auth.ts` token refresh, auth-guard, and storage/logout behavior.
- [x] Expand `frontend/src/utils/auth.ts` coverage to `apiFetch` reconnect and 401/403 handling.
- [x] Cover `frontend/src/router/index.ts` auth guard and route recovery behavior.
- [x] Cover `frontend/src/stores/notifications.ts` and notification normalization helpers.
- [x] Cover selected composables under `frontend/src/composables/` with deterministic logic first.
- [x] Add focused component tests for non-chat notification UI slices such as `AppToasts.vue` and `NotificationsView.vue`.
- [x] Add component tests for `LoginView.vue` OTP and approval-required flows.
- [x] Add component tests for `ProfileView.vue` and remaining key stateful non-chat views.

## Phase 6 - Frontend E2E Expansion

- [x] Preserve current Playwright coverage in `frontend/e2e/notifications.spec.ts`, `frontend/e2e/lot-suggestion.spec.ts`, and `frontend/e2e/channel-media.spec.ts`.
- [x] Add login/auth E2E coverage.
- [x] Add market/offer creation E2E coverage.
- [x] Add direct chat E2E coverage beyond channel/group/share-target paths.
- [x] Add admin E2E smoke coverage for invitations, users, settings, and optional channels.
- [x] Re-sync messenger Playwright suites with the current conversation-list, room-header, and context-menu UI after the post-rollback messenger redesign drift.
- [x] Revalidate `frontend/e2e/channel-media.spec.ts`, `frontend/e2e/direct-chat.spec.ts`, and `frontend/e2e/mandatory-channel.spec.ts` serially with a clean green pass (`49/49`).
- [x] Revalidate `frontend/e2e/notifications.spec.ts` serially against the current auth/realtime runtime (`4/4`).
- [x] Re-sync `frontend/e2e/admin-smoke.spec.ts` and `frontend/e2e/auth.spec.ts` with the current channel-manager entry flow and profile/settings routing.
- [x] Revalidate the full frontend Playwright suite serially with a clean green pass (`64/64`).
- [x] Expand the full frontend Playwright matrix beyond Chromium with serial Firefox (`64/64`) and WebKit (`64/64`) passes after the current cross-browser selector/runtime hardening and `MarketView.vue` manual-create restoration.
- [x] Expose routine frontend browser commands for Chromium / Firefox / WebKit via `npm run test:e2e`, serial `npm run test:e2e:firefox`, serial `npm run test:e2e:webkit`, and `npm run test:e2e:matrix` so the checklist path is executable without ad hoc shell commands and keeps the proven cross-browser worker profile.

### Messenger Detailed Coverage Map

Current messenger coverage already locked by Playwright:

- [x] Direct chat text composer send/edit flow through the real UI in `frontend/e2e/direct-chat.spec.ts`.
- [x] Direct chat reaction and own-message delete flow through the real context menu in `frontend/e2e/direct-chat.spec.ts`.
- [x] Chat notification toast, unread badge increment, and deep-link back into the messenger in `frontend/e2e/notifications.spec.ts`.
- [x] Mandatory messenger room visibility/open flow and fresh-activation rollout in `frontend/e2e/mandatory-channel.spec.ts`.
- [x] Channel composer gating for admin vs member, route-query restore after reload, live unread/read reset, and live reactions in `frontend/e2e/channel-media.spec.ts`.
- [x] Direct-to-room forward coverage for text/document/image/video paths into writable groups/channels in `frontend/e2e/channel-media.spec.ts`.
- [x] Share Receive routing matrix across direct/group/channel targets for text, document, image, video, voice, and multi-target fanout in `frontend/e2e/channel-media.spec.ts`.
- [x] Group backend-to-messenger room coverage for create/list/detail/admin APIs, room send/load/read/react flows, conversation-list projection, and realtime unread/live-append behavior in `frontend/e2e/channel-media.spec.ts`.
- [x] Current messenger entry-point smoke for optional-channel creation/member invite from the admin-facing UI in `frontend/e2e/admin-smoke.spec.ts`.

Remaining messenger-specific automated test backlog:

- [x] Conversation-list state/actions: long-press popover actions for pin/unpin, pin reorder, mute/unmute, manual unread, hide direct chats, optional-channel unfollow, and mandatory-channel ordering invariants.
- [ ] Pinned-message UX inside open rooms: pin/unpin action, banner rendering, navigation/jump behavior, and direct/group/channel persistence.
- [x] Messenger room-manager core flows: group/channel create/edit title and description, avatar upload/remove persistence, channel member add, header-open entry points, and back-stack dismissal in `ChatGroupManagerModal.vue` and `CreateChannelView.vue`.
- [ ] Remaining group/channel manager edge flows: admin-role mutations, leave/delete paths, and broader member-management permutations in `ChatGroupManagerModal.vue` and `CreateChannelView.vue`.
- [x] Messenger public-profile core flows: open self/other public profiles from header entry points, self avatar add/remove, and return-to-chat navigation.
- [ ] Remaining public-profile edge flows: member-row profile entry points and an explicit avatar-change regression path beyond the current self-avatar add/remove coverage.
- [ ] Direct-room rich composer/media UX: `AttachmentMenu.vue` image/video/document/voice/location/camera paths, crop/editor flow, background upload/download persistence, and cached file open/share/download behavior.
- [ ] Viewer/search/selection UX: `ChatLightbox.vue` toolbar and album navigation, global/in-chat search flows, `ChatNewConversationModal.vue`, selection-mode bulk actions, and overlay/back-button semantics.
- [ ] Messenger-focused unit/component baseline for high-churn chat UI pieces such as `ChatConversationList.vue`, `ChatHeader.vue`, `ChatContextMenu.vue`, `ChatLightbox.vue`, `ChatInputBar.vue`, `ChatGroupManagerModal.vue`, `CreateChannelView.vue`, and `PublicProfile.vue`.

Remaining completion estimate for full messenger test closure:

- [ ] 2 focused Playwright slices covering pinned-message UX and direct-room media/search/viewer UX.
- [ ] 2 focused Vitest slices covering deterministic messenger UI state that is cheaper to lock below Playwright.
- [ ] 1 full serial browser-matrix rerun on Chromium / Firefox / WebKit after the new messenger slices land.

## Phase 7 - Models, Migrations, Startup, and Deployment Smoke Coverage

- [x] Cover behaviorful model helpers in `models/`.
- [x] Add migration/alembic smoke validation for `alembic/` and `migrations/`.
- [x] Add startup/import smoke checks for `main.py`, `run_bot.py`, and `manage.py`.
- [x] Add smoke validation for deploy/build surfaces: `Dockerfile*`, `docker-compose*.yml`, `deploy.sh`, `Makefile`, `nginx.conf`.

## Phase 8 - Load, Sync, Resilience, and Regression Hardening

- [x] Normalize and retain nonfunctional scripts in `tests/api_load_test.py`, `tests/load_test.py`, `tests/live_simulation.py`, and `tests/debug_trade.py` as explicit non-regression tools.
- [x] Add a diff-based regression gate for future product changes via `scripts/report_test_matrix.py --check-diff --base-ref <ref>`.
- [x] Add repository-wide coverage reporting and breadth gates via `make test-report`, `make test-gate`, and `make test-diff-gate BASE=<ref>`.
- [x] Codify merge and pre-release CI gates for the frontend browser matrix (Chromium / Firefox / WebKit) plus repository governance checks.
- [x] Run focused validation for the Phase 8 governance and surface-smoke slice: `/bin/python3 -m unittest tests.test_report_test_matrix tests.test_schemas_smoke tests.test_scripts_surface_smoke tests.test_static_surface_smoke` and `make test-gate`.

## Directory Coverage Checklist

- [x] Root runtime files: `main.py`, `run_bot.py`, `manage.py`, `schemas.py`, `deploy.sh`, `Makefile`.
- [x] `api/`
- [x] `bot/`
- [x] `core/`
- [x] `models/`
- [x] `tests/`
- [x] `frontend/src/`
- [x] `frontend/e2e/`
- [x] `alembic/` and `migrations/`
- [x] `scripts/`
- [x] Smoke/build validation only: `mini_app_dist/`, `templates/`, `fonts/`, `map_data/`, `pip_packages/`, root `src/`, generated caches, and browser/build artifacts.