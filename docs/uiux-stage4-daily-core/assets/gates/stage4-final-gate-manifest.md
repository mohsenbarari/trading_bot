# Stage 4 final gate manifest (read-only preparation)

Snapshot worktree: `/tmp/trading-bot-webapp-uiux-redesign-v2`

- Comparison base: `9dfa961000832c830729ce67e8a54357915c716a`
- Comparison tree: `1540c2534d8052a3a8cfcffcdc2f65e4b85fc874`
- Current product delta: 67 paths = 60 tracked modifications + 7 untracked additions
- Current 67-path sorted pathset SHA-256: `25a5773b2e3ca1f6e45bbf48800dcac4ce3cd8e8125f1913fee674529720739f`
- Current 67-path path+content composite SHA-256: `517ae0b1d3d630f6fa086cdc208905fabb9a532035cec539f61f9cd5f67af35e`
- Present intended Git set: 74 paths = 67 product paths + 7 ignored Stage 4 docs
- Present 74-path sorted pathset SHA-256: `0c7bebe03ba959ea8d9a6679746ec8f69c98381b48e1ed97aa2deb990d91edeb`
- Present 74-path path+content composite SHA-256: `8e0ca550daf7e9e15e6a4153f93d3a1ead6904c771ae7001782d898825214ba0`

These working-tree hashes are a preparation snapshot, not the final post-evidence or post-documentation hashes.

## 1. Authoritative frontend Vitest union

Run serially from `frontend`:

```bash
npm exec vitest run -- \
  --pool=forks --maxWorkers=1 --minWorkers=1 --no-file-parallelism \
  src/components/AppAuthenticatedShell.test.ts \
  src/components/AppToasts.test.ts \
  src/components/BottomNav.test.ts \
  src/components/PWAInstallOverlay.test.ts \
  src/components/ui/AppPrimitives.test.ts \
  src/components/workspace/WorkspacePrimitives.test.ts \
  src/composables/chat/useChatFileHandler.test.ts \
  src/composables/useNotificationRuntime.test.ts \
  src/composables/useStorageCacheMetrics.test.ts \
  src/router/index.test.ts \
  src/router/uiRouteContract.test.ts \
  src/services/telegramLink.test.ts \
  src/services/webPush.test.ts \
  src/stores/notifications.test.ts \
  src/styles/designSystemV2.test.ts \
  src/types/notifications.test.ts \
  src/utils/auth.test.ts \
  src/utils/browserNotifications.test.ts \
  src/utils/currentUser.test.ts \
  src/utils/notificationUi.test.ts \
  src/utils/pushNotificationsServiceWorker.test.ts \
  src/utils/pwaInstall.test.ts \
  src/utils/routeRequest.test.ts \
  src/utils/securityLayerState.test.ts \
  src/views/AccountHubView.test.ts \
  src/views/AccountantWorkspaceView.test.ts \
  src/views/CustomerWorkspaceView.test.ts \
  src/views/DashboardView.test.ts \
  src/views/LoginView.test.ts \
  src/views/MarketView.test.ts \
  src/views/MessengerView.test.ts \
  src/views/NotificationsView.test.ts \
  src/views/OperationsView.test.ts \
  src/views/SettingsView.test.ts
```

Current collection-only inventory: **34 files / 450 tests**. This supersedes the earlier 34/448 run: the current source has two additional regression tests. Exact per-file counts:

```text
4   src/components/AppAuthenticatedShell.test.ts
11  src/components/AppToasts.test.ts
14  src/components/BottomNav.test.ts
10  src/components/PWAInstallOverlay.test.ts
10  src/components/ui/AppPrimitives.test.ts
5   src/components/workspace/WorkspacePrimitives.test.ts
19  src/composables/chat/useChatFileHandler.test.ts
14  src/composables/useNotificationRuntime.test.ts
4   src/composables/useStorageCacheMetrics.test.ts
9   src/router/index.test.ts
7   src/router/uiRouteContract.test.ts
16  src/services/telegramLink.test.ts
11  src/services/webPush.test.ts
23  src/stores/notifications.test.ts
12  src/styles/designSystemV2.test.ts
9   src/types/notifications.test.ts
33  src/utils/auth.test.ts
5   src/utils/browserNotifications.test.ts
13  src/utils/currentUser.test.ts
1   src/utils/notificationUi.test.ts
10  src/utils/pushNotificationsServiceWorker.test.ts
6   src/utils/pwaInstall.test.ts
6   src/utils/routeRequest.test.ts
1   src/utils/securityLayerState.test.ts
11  src/views/AccountHubView.test.ts
10  src/views/AccountantWorkspaceView.test.ts
12  src/views/CustomerWorkspaceView.test.ts
24  src/views/DashboardView.test.ts
43  src/views/LoginView.test.ts
33  src/views/MarketView.test.ts
7   src/views/MessengerView.test.ts
24  src/views/NotificationsView.test.ts
9   src/views/OperationsView.test.ts
24  src/views/SettingsView.test.ts
```

`MarketView` and `MessengerView` are the explicit protected behavior regressions; PWA overlay, authenticated shell and BottomNav are the explicit shell regressions. All changed unit-test files are in the union. Playwright specs are covered by the separate browser acceptance binding, not Vitest.

## 2. Guard self-tests and runtime guards

```bash
cd frontend
npm exec vitest run -- \
  --pool=forks --maxWorkers=1 --minWorkers=1 --no-file-parallelism \
  scripts/design-system-v2-guard.test.mjs \
  scripts/stage3-protected-region-guard.test.mjs \
  scripts/stage4-protected-surface-guard.test.mjs
npm run guard:ui
node scripts/check-stage4-protected-surfaces.mjs --list
```

Current inventory: **3 files / 55 tests** = 43 + 4 + 8.

## 3. Exact backend API baseline

Run from repository root in the already-used safe dummy-env mirror (literal worktree has no ignored `.env`):

```bash
python3 -m unittest \
  tests.test_auth_router_current_user_contract \
  tests.test_sessions_router_runtime \
  tests.test_notifications_preferences \
  tests.test_notifications_router_mutations \
  tests.test_notifications_router_reads \
  tests.test_notifications_router_stream \
  tests.test_web_push \
  tests.test_trades_router_reads \
  tests.test_commodities_router_read_all \
  tests.test_users_public_project_users \
  tests.test_telegram_link_token_service
```

Expected inventory: **11 modules / 69 tests** (3 + 6 + 3 + 5 + 5 + 2 + 16 + 17 + 2 + 4 + 6).

## 4. Type, build, guard and diff gates

```bash
cd frontend
npm exec vue-tsc -- --noEmit --pretty false
npm run build
npm run guard:ui
cd ..
git diff --check 9dfa961000832c830729ce67e8a54357915c716a -- frontend
```

After staging, also run `git diff --cached --check`.

## 5. ESLint and Prettier delta contract

Exact current quality scope is the 67 frontend paths in section 7. ESLint scope is 64 paths: exclude only `frontend/package.json`, `frontend/src/design-system-v2/scope-manifest.json`, and `frontend/src/styles/design-system-v2.components.css`. Baseline ESLint scope is 57 paths (the same tracked paths as they exist at the comparison base). Prettier current/base scopes are 67/60 paths.

Use the already-prepared isolated comparison-base mirror referenced by the audit script (it has a `node_modules` symlink to the current dependency install):

```bash
BASE=9dfa961000832c830729ce67e8a54357915c716a
BASE_DIR=/tmp/stage4-quality-base.GWB2Bg
test -d "$BASE_DIR/frontend"
test "$(readlink "$BASE_DIR/frontend/node_modules")" = "$PWD/frontend/node_modules"
```

If that temporary mirror is ever recreated at a different path, update the `baseFrontend` constant in the temporary `/tmp/stage4-quality-audit.mjs` copy before using it; do not change repository files.

Derive lists from Git, not from a stale handwritten list:

```bash
git diff --name-status "$BASE" -- frontend > /tmp/stage4-modified.tsv
git ls-files --others --exclude-standard -- frontend > /tmp/stage4-untracked.txt
node /tmp/stage4-quality-audit.mjs
```

Then run:

```bash
cd frontend
mapfile -t CURRENT_LINT < /tmp/stage4-eslint-current-files.txt
npx eslint --format json "${CURRENT_LINT[@]}" > /tmp/stage4-eslint-current.json || test $? -eq 1
mapfile -t CURRENT_PRETTIER < /tmp/stage4-prettier-current-files.txt
npx prettier --list-different "${CURRENT_PRETTIER[@]}" | sort > /tmp/stage4-prettier-current.txt || test $? -eq 1

cd "$BASE_DIR/frontend"
mapfile -t BASE_LINT < /tmp/stage4-eslint-base-files.txt
npx eslint --format json "${BASE_LINT[@]}" > /tmp/stage4-eslint-base.json || test $? -eq 1
mapfile -t BASE_PRETTIER < /tmp/stage4-prettier-base-files.txt
npx prettier --list-different "${BASE_PRETTIER[@]}" | sort > /tmp/stage4-prettier-base.txt || test $? -eq 1

node /tmp/stage4-quality-audit.mjs --analyze
comm -12 /tmp/stage4-prettier-base.txt /tmp/stage4-prettier-current.txt > /tmp/stage4-prettier-inherited.txt
comm -13 /tmp/stage4-prettier-base.txt /tmp/stage4-prettier-current.txt > /tmp/stage4-prettier-added.txt
comm -23 /tmp/stage4-prettier-base.txt /tmp/stage4-prettier-current.txt > /tmp/stage4-prettier-removed.txt
```

Acceptance: ESLint `addedCount=0`; Prettier added set empty. Raw nonzero exits are inherited baseline diagnostics and must not be called blanket-clean.

Last stored audit is stale by one newly changed test path and must be rerun. It recorded current 121 diagnostics (110 errors, 11 warnings), base 167 (155 errors, 12 warnings), Stage4-new 0, removed 46; Prettier current/inherited 22, base 35, added 0, removed 13. Do not reuse its JSON hashes for final closure.

## 6. Protected hashes and browser binding

Current `node scripts/check-stage4-protected-surfaces.mjs --list` passes:

- Home Market interior: 6 sections / 4553 bytes / `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860`
- Market runtime: 19 files / 137246 bytes / pathset `37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589` / composite `162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058`
- Messenger runtime: 85 files / 1312405 bytes / pathset `f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58` / composite `f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248`
- AdminMessages: `5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a`
- TradingSettings: `509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa`
- Route policy: 4 full/off + 3 mixed, manifest/runtime 7/7

Final browser acceptance is now green:

- run `uiux-stage4-browser-20260809T180340666Z`
- 49/49 assertions, 9 suites, 22 screenshots, promotable
- source 398 files, before/after identical, source binding `1f8858264f0c52479c227bb84822a6c109f9b4fadb968500df596126acf099bf`
- protected before/after identical; unexpected request failures, external blocks, unexpected APIs and page errors all zero
- metrics SHA-256 `83445d91bd78fd0903f49833a5b72c5d49345d517d9e5ae05e2fdd42954cd01f`
- final binding SHA-256 `04f5c126cae096c0de3b6f738108aae18f239aae0310d119b0bb870e6f9e856b`

## 7. Exact current product/Git pathset (67)

```text
frontend/e2e/auth.spec.ts
frontend/e2e/non-messenger-visual-baseline.spec.ts
frontend/e2e/notifications.spec.ts
frontend/package.json
frontend/public/push-notifications-sw.js
frontend/scripts/check-design-system-v2-guards.mjs
frontend/scripts/check-stage4-protected-surfaces.mjs
frontend/scripts/design-system-v2-guard.test.mjs
frontend/scripts/lib/design-system-v2-guard.mjs
frontend/scripts/lib/stage3-protected-region-guard.mjs
frontend/scripts/lib/stage4-protected-surface-guard.mjs
frontend/scripts/stage3-protected-region-guard.test.mjs
frontend/scripts/stage4-protected-surface-guard.test.mjs
frontend/src/components/AppAuthenticatedShell.test.ts
frontend/src/components/AppToasts.test.ts
frontend/src/components/AppToasts.vue
frontend/src/components/BottomNav.test.ts
frontend/src/components/BottomNav.vue
frontend/src/components/ui/AppFilterChips.vue
frontend/src/components/ui/AppPrimitives.test.ts
frontend/src/components/workspace/WorkspaceActionTile.vue
frontend/src/components/workspace/WorkspaceDangerZone.vue
frontend/src/components/workspace/WorkspaceNotice.vue
frontend/src/components/workspace/WorkspacePrimitives.test.ts
frontend/src/components/workspace/WorkspaceSection.vue
frontend/src/components/workspace/WorkspaceShell.vue
frontend/src/components/workspace/WorkspaceStatTile.vue
frontend/src/composables/useNotificationRuntime.test.ts
frontend/src/composables/useNotificationRuntime.ts
frontend/src/composables/useStorageCacheMetrics.test.ts
frontend/src/composables/useStorageCacheMetrics.ts
frontend/src/design-system-v2/scope-manifest.json
frontend/src/env.d.ts
frontend/src/router/index.test.ts
frontend/src/router/index.ts
frontend/src/router/uiRouteContract.test.ts
frontend/src/router/uiRouteContract.ts
frontend/src/services/telegramLink.test.ts
frontend/src/services/telegramLink.ts
frontend/src/services/webPush.test.ts
frontend/src/services/webPush.ts
frontend/src/stores/notifications.test.ts
frontend/src/stores/notifications.ts
frontend/src/styles/design-system-v2.components.css
frontend/src/styles/designSystemV2.test.ts
frontend/src/types/notifications.test.ts
frontend/src/types/notifications.ts
frontend/src/utils/auth.test.ts
frontend/src/utils/auth.ts
frontend/src/utils/browserNotifications.test.ts
frontend/src/utils/browserNotifications.ts
frontend/src/utils/currentUser.test.ts
frontend/src/utils/currentUser.ts
frontend/src/utils/localLogoutReceipt.ts
frontend/src/utils/pushNotificationsServiceWorker.test.ts
frontend/src/views/AccountHubView.test.ts
frontend/src/views/AccountHubView.vue
frontend/src/views/DashboardView.test.ts
frontend/src/views/DashboardView.vue
frontend/src/views/LoginView.test.ts
frontend/src/views/LoginView.vue
frontend/src/views/NotificationsView.test.ts
frontend/src/views/NotificationsView.vue
frontend/src/views/OperationsView.test.ts
frontend/src/views/OperationsView.vue
frontend/src/views/SettingsView.test.ts
frontend/src/views/SettingsView.vue
```

Ignored Stage 4 docs currently present and intentionally added with `git add -f` (7):

```text
docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE4_DAILY_CORE_CHECKPOINT_20260809.md
docs/uiux-stage4-daily-core/CONTENT_NECESSITY_MATRIX.md
docs/uiux-stage4-daily-core/PROTECTED_SURFACE_DIFF_MANIFEST.json
docs/uiux-stage4-daily-core/README.md
docs/uiux-stage4-daily-core/ROUTE_SURFACE_MANIFEST.json
docs/uiux-stage4-daily-core/RUNTIME_CONTRACT.md
docs/uiux-stage4-daily-core/VALIDATION.md
```

Staging contract after evidence/Figma/Sites documentation is finalized:

```bash
git add -- <the 67 frontend paths>
git add -f -- \
  docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE4_DAILY_CORE_CHECKPOINT_20260809.md \
  docs/uiux-stage4-daily-core
git diff --cached --name-status
git diff --cached --check
```

The closure files that do not yet exist (`FIGMA_SNAPSHOT_MANIFEST.json`, `EVIDENCE_MANIFEST.json`, `SITES_PROVENANCE.json`, evidence HTML/capture/assets) must be included under the same forced Stage 4 docs tree after creation. Therefore 74 is the exact present intended set, not the final closure count.
