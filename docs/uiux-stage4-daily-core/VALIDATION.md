# اعتبارسنجی Stage 4 Daily Core

وضعیت کلی: **`stage4_complete`**

## ۱. baseline قفل‌شده پیش از پیاده‌سازی

comparison base: `9dfa961000832c830729ce67e8a54357915c716a`

### Frontend core — ۲۱ فایل / ۱۷۷ تست

| فایل                                                   | تست پایه |
| ------------------------------------------------------ | -------: |
| `src/views/DashboardView.test.ts`                      |       17 |
| `src/views/OperationsView.test.ts`                     |        5 |
| `src/views/AccountHubView.test.ts`                     |        7 |
| `src/views/SettingsView.test.ts`                       |       15 |
| `src/views/NotificationsView.test.ts`                  |       12 |
| `src/components/workspace/WorkspacePrimitives.test.ts` |        3 |
| `src/composables/useNotificationRuntime.test.ts`       |       10 |
| `src/stores/notifications.test.ts`                     |       19 |
| `src/services/webPush.test.ts`                         |        2 |
| `src/services/telegramLink.test.ts`                    |        2 |
| `src/composables/chat/useChatFileHandler.test.ts`      |       19 |
| `src/router/index.test.ts`                             |        9 |
| `src/router/uiRouteContract.test.ts`                   |        7 |
| `src/views/CustomerWorkspaceView.test.ts`              |       12 |
| `src/views/AccountantWorkspaceView.test.ts`            |       10 |
| `src/utils/currentUser.test.ts`                        |       10 |
| `src/utils/routeRequest.test.ts`                       |        6 |
| `src/utils/browserNotifications.test.ts`               |        4 |
| `src/types/notifications.test.ts`                      |        6 |
| `src/utils/notificationUi.test.ts`                     |        1 |
| `src/utils/securityLayerState.test.ts`                 |        1 |

```bash
cd frontend
npm exec vitest run -- \
  src/views/DashboardView.test.ts \
  src/views/OperationsView.test.ts \
  src/views/AccountHubView.test.ts \
  src/views/SettingsView.test.ts \
  src/views/NotificationsView.test.ts \
  src/components/workspace/WorkspacePrimitives.test.ts \
  src/composables/useNotificationRuntime.test.ts \
  src/stores/notifications.test.ts \
  src/services/webPush.test.ts \
  src/services/telegramLink.test.ts \
  src/composables/chat/useChatFileHandler.test.ts \
  src/router/index.test.ts \
  src/router/uiRouteContract.test.ts \
  src/views/CustomerWorkspaceView.test.ts \
  src/views/AccountantWorkspaceView.test.ts \
  src/utils/currentUser.test.ts \
  src/utils/routeRequest.test.ts \
  src/utils/browserNotifications.test.ts \
  src/types/notifications.test.ts \
  src/utils/notificationUi.test.ts \
  src/utils/securityLayerState.test.ts
```

### Shell/PWA invariant — ۴ فایل / ۳۱ تست

| فایل                                           | تست پایه |
| ---------------------------------------------- | -------: |
| `src/components/PWAInstallOverlay.test.ts`     |       10 |
| `src/utils/pwaInstall.test.ts`                 |        6 |
| `src/components/AppAuthenticatedShell.test.ts` |        4 |
| `src/components/BottomNav.test.ts`             |       11 |

```bash
npm exec vitest run -- \
  src/components/PWAInstallOverlay.test.ts \
  src/utils/pwaInstall.test.ts \
  src/components/AppAuthenticatedShell.test.ts \
  src/components/BottomNav.test.ts
```

### Guard self-test

baseline inherited Stage 3:

- `scripts/design-system-v2-guard.test.mjs`: 42
- `scripts/stage3-protected-region-guard.test.mjs`: 3
- جمع baseline: ۲ فایل / ۴۵ تست

Stage 4 افزوده است:

- `scripts/stage4-protected-surface-guard.test.mjs`: ۸ تست source-observed؛ در closure همراه دو guard دیگر پاس شد.

```bash
npm exec vitest run -- \
  scripts/design-system-v2-guard.test.mjs \
  scripts/stage3-protected-region-guard.test.mjs \
  scripts/stage4-protected-surface-guard.test.mjs
npm run guard:ui
```

### Backend API baseline — ۱۱ ماژول / ۶۹ تست

| ماژول                                          | تست |
| ---------------------------------------------- | --: |
| `tests.test_auth_router_current_user_contract` |   3 |
| `tests.test_sessions_router_runtime`           |   6 |
| `tests.test_notifications_preferences`         |   3 |
| `tests.test_notifications_router_mutations`    |   5 |
| `tests.test_notifications_router_reads`        |   5 |
| `tests.test_notifications_router_stream`       |   2 |
| `tests.test_web_push`                          |  16 |
| `tests.test_trades_router_reads`               |  17 |
| `tests.test_commodities_router_read_all`       |   2 |
| `tests.test_users_public_project_users`        |   4 |
| `tests.test_telegram_link_token_service`       |   6 |

```bash
cd ..
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

## ۲. گیت‌های فنی لازم

```bash
cd frontend
npx vue-tsc --noEmit --pretty false
npm run build
npm run guard:ui
npx eslint --format json <exact-stage4-source-and-test-pathset>
npx prettier --check <exact-stage4-source-and-test-pathset>
npx prettier --check --parser html public/uiux-v2-brand-mark.svg
```

lint و format باید در برابر comparison base به‌صورت delta سنجیده شوند. raw exit code به‌تنهایی نتیجه Stage 4 نیست.

## ۳. caveatهای inherited که باید صادقانه حفظ شوند

- ESLint baseline Stage 3 روی scope خام ۶۶ فایل exit `1` با `184 error + 1 warning` داشت؛ Stage3-new diagnostic برابر صفر بود. Stage 4 باید `new=0` را اثبات کند و نباید clean blanket ادعا کند.
- Prettier baseline Stage 3 روی ۷۷ فایل exit `2` داشت: ۱۴ فایل legacy dirty و parser inference برای `frontend/public/uiux-v2-brand-mark.svg`. Stage3-new hunk برابر صفر بود؛ SVG با parser صریح HTML پاس شد.
- spelling meta در `frontend/index.html` یک استثنای intentional privacy contract است و نباید مکانیکی عوض شود.
- build بسته Stage 3 برابر ۲۱۶۰ module، Vite `39.88s` و PWA `166 entries / 4178.09 KiB` فقط baseline تاریخی بود؛ build مستقل Stage 4 پایین ثبت شده است.
- typecheck Stage 3 فقط baseline بود؛ typecheck مستقل Stage 4 پاس شده است.
- نخستین diagnostic backend Stage 4 با `sqlite+aiosqlite` به‌علت نبود dependency محیطی discard شد. rerun معتبر با PostgreSQL driver نصب‌شده و DSNهای dummy غیرمحرمانه `69/69` پاس شد، بدون اتصال DB؛ دو `DeprecationWarning` ارثی `schemas.py` ثبت شدند.

## ۴. هویت Git-bound closure

```text
branch = condidate/webapp-ui-ux-redesign-v2
comparisonBaseCommit = 9dfa961000832c830729ce67e8a54357915c716a
comparisonBaseTree = 1540c2534d8052a3a8cfcffcdc2f65e4b85fc874
implementationCommit = 007f94d170cb02cd69911d9e1f122b83fbacd535
implementationTree = 807a01c76c93489ccce1e5b72cea9c214fd52d31
implementationParent = 9dfa961000832c830729ce67e8a54357915c716a
exactPathCount = 67
pathSetSha256 = 25a5773b2e3ca1f6e45bbf48800dcac4ce3cd8e8125f1913fee674529720739f
pathContentSha256 = 517ae0b1d3d630f6fa086cdc208905fabb9a532035cec539f61f9cd5f67af35e
implementationCommitStatus = verified_exact_head_parent_tree_and_67_path_delta
```

source پیش از commit frozen بود. browser source binding دقیقاً `398` فایل با SHA-256 `1f8858264f0c52479c227bb84822a6c109f9b4fadb968500df596126acf099bf` داشت و mismatch بایت/mtime پس از گیت‌ها صفر ماند.

## ۵. ledger فنی نهایی

| گیت | شاهد نهایی | وضعیت |
| --- | --- | --- |
| frontend serial | `34` فایل / `450/450` تست؛ artifact `assets/gates/stage4-final-vitest.json`، SHA-256 `c2e4d8be51b88ebb7d7ab75c903ee60452fb03c0db6b9b317ead66a0bcd6a9fa` | `passed` |
| guard | `3` فایل / `8` suite / `55/55` تست؛ `guard:ui` و protected list پاس | `passed` |
| backend | `11` ماژول / `69/69` تست با DSNهای dummy PostgreSQL؛ بدون اتصال DB | `passed_with_environmental_diagnostic_disclosed` |
| typecheck | `vue-tsc --noEmit --pretty false`؛ exit صفر | `passed` |
| build | `2162` module، `31.50s`، PWA `161 entries / 4190.04 KiB` | `passed_with_browserslist_and_chunk_advisories` |
| diff-check | exit صفر | `passed` |
| ESLint delta | current `64` فایل و `121 = 110E + 11W`؛ base `57` فایل و `167 = 155E + 12W`؛ inherited `121`، added `0`، removed `46` | `passed_delta_clean_only` |
| Prettier delta | current `67` فایل / dirty `22`؛ base `60` / dirty `35`؛ inherited `22`، added `0`، removed `13` | `passed_delta_clean_only` |
| residue | unexpected process/env/untracked/database residue صفر؛ فقط ignored build output `mini_app_dist` | `passed` |

summary نهایی در `assets/gates/stage4-final-gates-summary.json` با SHA-256 `f5f1b32ef85d010aa2134b3531f628ac941f91f9dc4adfb58ee46cfa39a86ac2` و manifest در `assets/gates/stage4-final-gate-manifest.md` با SHA-256 `ae5da32b7eec554cb25c3e167f9e17b80d63d69dd9bde812cc6fc89c817907af` ثبت شده‌اند. raw ESLint/Prettier exit غیرصفر به‌دلیل debt ارثی blanket-pass نشده است.

## ۶. browser acceptance

- run: `uiux-stage4-browser-20260809T180340666Z`؛
- status: `passed` و `promotable=true`؛
- assertion: `49/49`؛ suite: `9`؛ viewport: `8`؛ screenshot: `22`؛
- expected HTTP failure: `16`؛ expected console error: `17`؛ unexpected page/console/HTTP/request/API/external/WS diagnostic: `0`؛
- metrics: `assets/browser-evidence/stage4-browser-acceptance-metrics.json` با SHA-256 `83445d91bd78fd0903f49833a5b72c5d49345d517d9e5ae05e2fdd42954cd01f`؛
- final binding: `assets/browser-evidence/stage4-final-source-binding.json` با SHA-256 `04f5c126cae096c0de3b6f738108aae18f239aae0310d119b0bb870e6f9e856b`؛
- source/plan/harness/env/protected identity پیش و پس اجرا برقرار و source drift صفر بود.

## ۷. Figma و local evidence

Figma authored snapshot روی file `z8jgJxST4O2APzWnlyP9gv`، page `283:18`، root `283:19` و provenance `291:554` است. direct audit با شش section، شش canonical screen، `66` linked instance، detached صفر، یک protected Market image و error صفر پاس شد. سیزده export مستقیم `1118391` بایت و aggregate SHA-256 `46e329154d226cc0ed6fb302b4c33b0215b29280a25d9d8abccfe1e6a266774a` دارند.

local evidence run `stage4-local-588243cd033dce300388` برابر `26/26` است. pre/post DOM و audit یکسان، post-capture remeasurement برقرار، شش PNG nonblank و console/page/request error صفر بودند. بسته frozen:

```text
fileCount = 70
bytes = 5863416
aggregateSha256 = 8c123a1eeb717f799c0449443f2d8ea76f201a0ae2c31e062b1cff09584a7971
evidenceManifestSha256 = 7a1a4a7da5c82f7c3744fba2f94adf0402dc6e6d5b47944234d2c0b266efdda8
```

این ۷۰ فایل شامل evidence/FIGMA manifests، HTML، capture harness و همه assets است و در docs/Sites closure byte-identical باقی ماند.

## ۸. protected final diff

Market `19` فایل، Messenger `85` فایل، Home market شش‌بخشی، `AdminMessagesView.vue` و `TradingSettings.vue` با hashهای قفل‌شده برابر comparison base ماندند. route protection برابر `4 full/off + 3 mixed` و manifest/runtime `7/7` است. unauthorized source/behavior/visual drift، pathset drift و snapshot update بدون disposition همگی صفر هستند.

## ۹. Sites و closure

Sites project `appgprj_6a78cd05d74c8191a9c5e095f15c6381` از source commit `b55e221cf4fe363a12e2bf6b0f6a212a9adcd2ae`، version شماره `1` و deployment `appgdep_6a78ce229a0081919ae50975911e9e7d` با status `succeeded` منتشر شد. access سفارشی owner-only است؛ anonymous root/evidence هر دو `HTTP/2 401 + no-store + no-referrer`، environment entries و error log هر دو صفرند. جزئیات hash-bound در [SITES_PROVENANCE](SITES_PROVENANCE.json) با SHA-256 `3197cac8f90dcc1abfc2d52ee4fa4d87059e34863250a8042709230eb1bde1f8` ثبت است.

Stage 4 `complete` است. `nextAuthorizedRuntimeStage=null`، `stage5RuntimeImplementationAuthorized=false` و `stage5RuntimeWorkStarted=false`؛ roadmap تغییر نکرده و کار طبق دستور کاربر متوقف است.
