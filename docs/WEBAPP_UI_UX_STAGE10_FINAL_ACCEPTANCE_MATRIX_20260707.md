# WebApp UI/UX Stage 10 Final Acceptance Matrix - 2026-07-07

## Scope

Stage 10 is the final acceptance gate for the non-Messenger WebApp UI/UX unification roadmap.

This document is not production-release approval. It records the acceptance matrix, the local evidence already collected, and the runtime screenshot checks that must still be executed in staging or another environment where Playwright can start the WebApp server.

## Covered Surfaces

The final matrix covers these active non-Messenger surfaces:

- Dashboard: `/`
- Market: `/market`
- Operations hub: `/operations`
- Customer workspace: `/operations/customers`
- Accountant workspace: `/operations/accountants`
- Account hub: `/account`
- Profile: `/profile`
- Notifications: `/notifications`
- Admin users: `/admin/users`
- Admin commodities: `/admin/commodities`
- Login: `/login`
- Register: `/register`
- Invite landing: `/i/uiux-baseline`
- Share receive: `/share-receive`

Messenger internals are not part of the visual unification scope, but shared navigation changes must keep Messenger FAB/unread behavior intact.

## Required Viewports

- Mobile narrow: `390x844`
- Desktop: `1440x900`
- Responsive smoke matrix from `frontend/e2e/non-messenger-viewport.spec.ts`:
  - `360x740`
  - `375x812`
  - `390x844`
  - `414x896`
  - `430x932`
  - `768x1024`
  - `1024x768`
  - `1440x900`

## Required State Coverage

- Persian RTL text.
- Mixed Persian/Latin content.
- Empty states.
- Loading states.
- Error states.
- Disabled controls.
- Success/warning/danger/neutral notification states.
- Market open/closed navigation chrome.
- Market active/expired/traded offer states.
- Customer/accountant/admin visibility surfaces.
- PWA install prompt position.
- Share receive loading/error/send result states.
- Keyboard focus and accessible names for visible interactive controls where the baseline harness enables `UI_UX_A11Y=1`.

## Local Evidence Collected

2026-07-07 local gates:

- Passed full frontend unit suite: `npm run test:unit:run`
  - `127` test files passed.
  - `1101` tests passed.
- Passed Stage 9 active-market gate: `npm run test:unit:run -- src/views/MarketView.test.ts src/components/OffersList.test.ts src/composables/useTradingSort.test.ts`
  - `49` tests passed.
- Passed Stage 9 shell gate: `npm run test:unit:run -- src/components/BottomNav.test.ts src/views/DashboardView.test.ts src/views/OperationsView.test.ts src/views/AccountHubView.test.ts`
  - `29` tests passed.
- Passed Stage 8 navigation/PWA gates:
  - `npm run test:unit:run -- src/components/BottomNav.test.ts src/components/PWAInstallOverlay.test.ts src/components/AppAuthenticatedShell.test.ts`
  - `npm run test:unit:run -- src/views/MarketView.test.ts src/components/BottomNav.test.ts`
  - `npm run test:unit:run -- src/views/ShareReceiveView.test.ts src/components/PWAInstallOverlay.test.ts src/components/BottomNav.test.ts`
- Passed: `npm run guard:ui`.
- Passed: `npm run build`.
- Passed: `git diff --check`.
- Passed visual-baseline discovery: `npx playwright test e2e/non-messenger-visual-baseline.spec.ts --project=chromium --list`
  - `26` screenshot tests discovered across mobile and desktop.
- Passed Stage 8 market/viewport discovery: `npx playwright test e2e/market-mutation-ux.spec.ts e2e/non-messenger-viewport.spec.ts --project=chromium --list`
  - `10` Chromium tests discovered.

Known non-fatal unit stderr is expected from tests that deliberately exercise failure branches, including cache-clear failure, upload retry/failure paths, unavailable browser APIs in jsdom, and invalid-date fallback tests.

## Runtime Screenshot Gate

The roadmap is not visually complete until screenshots are captured and reviewed.

Run in staging or another environment where Playwright can bind the WebApp server:

```bash
cd frontend
UI_UX_BASELINE=1 UI_UX_A11Y=1 npm run test:e2e:ui-baseline
```

Expected screenshot count from discovery:

- `26` Chromium screenshot checks.

Run viewport and protected-market smoke checks:

```bash
cd frontend
npx playwright test e2e/non-messenger-viewport.spec.ts e2e/market-mutation-ux.spec.ts --project=chromium
```

Recommended single-host role/trading follow-up checks:

```bash
STAGING_ENABLE_BOT=1 STAGING_FOREIGN_PUBLIC_SURFACE_GUARD=0 scripts/deploy_staging.sh deploy
scripts/run_staging_role_trading_e2e_gate.sh
```

This gate is intentionally wrapped by `scripts/run_staging_role_trading_e2e_gate.sh` instead of running the specs directly. Deploy staging with `STAGING_ENABLE_BOT=1` first so the app, foreign app, bot, and sync worker profiles are all recreated from the same image before the gate runs. Keep `STAGING_FOREIGN_PUBLIC_SURFACE_GUARD=0` only for this single-host, single-domain staging WebApp surface where the Iran-mode `app` service is running locally and must serve `/api/config`. The wrapped runner fail-closes unless the target is an explicit staging app container, the Redis container is explicit staging Redis, the backend URL points at staging, and the staging mutation confirmation environment is present. It also runs scoped pre/post cleanup for the Playwright fixture prefixes. Do not run the role/trading specs directly on a host that also has production containers.

For the already-provisioned real two-server staging topology, use the existing
two-server runner instead of the single-host compose gate:

```bash
STAGING_ENABLE_BOT=1 \
STAGING_FOREIGN_ONLY=1 \
STAGING_FOREIGN_PUBLIC_SURFACE_GUARD=1 \
scripts/deploy_staging.sh deploy
```

The real foreign-only host does not run an Iran-mode `app` service. Its public
surface guard must remain enabled; disabling it routes public `/api/*` requests
to the absent Iran app and makes the staging health probe fail with `502`.

```bash
set -a
source .env.staging
set +a
STAGING_EXPECTED_RELEASE_SHA="$(git rev-parse --short=12 HEAD)" \
STAGING_IRAN_SSH_PORT=37067 \
STAGING_OBSERVABILITY_API_KEY="$OBSERVABILITY_API_KEY" \
python3 scripts/run_staging_two_server_full_matrix.py \
  --mode preflight \
  --expected-branch candidate/webapp-ui-ux-unification \
  --run-id S2FM-UIUX-PREFLIGHT-$(date -u +%Y%m%dT%H%M%SZ)
```

Mutating execution remains guarded by
`STAGING_TWO_SERVER_FULL_MATRIX_CONFIRM=execute-staging-two-server-full-matrix`
and should only run after preflight passes on the deployed two-server staging
release SHA.

When reviewing two-server artifacts, treat `parity_status=non_business_difference`
as acceptable only when `business_drift_count=0`, `critical_drift_count=0`,
queues/backlog are zero, and the local-only differences are explicitly listed
as non-business staging residue. The `S2FM-CURRENT-BRANCH-EXECUTE2-20260708`
artifact had two Iran-local offer rows in this category before and after the
driver run; that class should not be confused with pending sync or market data
drift.

## Local Browser Blockers

Actual browser execution is blocked in the current local sandbox:

- `npm run dev -- --host 127.0.0.1 --strictPort --port 5173` fails with `listen EPERM: operation not permitted 127.0.0.1:5173`.
- The environment rejected the required unsandboxed Playwright execution request.
- Some role/trading E2E specs also import Docker-backed helpers at module load time and fail discovery in the sandbox with `spawnSync docker EPERM`.

These are environment limitations, not application test failures. Runtime browser evidence must be gathered on staging or another approved host.

## Acceptance Rules

The roadmap can be considered visually accepted only when all of the following are true:

1. Full frontend unit suite remains green.
2. `npm run guard:ui` remains green.
3. `npm run build` remains green.
4. The 26-route screenshot baseline either passes or produces intentional, reviewed diffs.
5. Mobile viewport checks show no horizontal overflow and no bottom-chrome overlap.
6. Market offer creation, request, recent-offer, expired/traded history, and role visibility checks pass.
7. Customer/accountant visibility checks pass.
8. Any remaining inconsistency is documented as intentional or deferred with a specific follow-up.

## Current Status

Local automated gates are green. Runtime browser validation also passed on staging through a temporary Docker Compose Playwright runner using `mcr.microsoft.com/playwright:v1.59.1-noble` against `https://staging.362514.ir`.

Final staging Chromium evidence:

- Visual baseline: `26/26` passed.
- Viewport and market mutation: `10/10` passed.
- Total Stage 10 staging browser matrix: `36/36` passed.

Artifacts:

- `tmp/stage10-staging-playwright/summary.md`
- `tmp/stage10-staging-playwright/runner-visual-baseline-20260707T1554Z/`
- `tmp/stage10-staging-playwright/runner-viewport-market-20260707T1654Z/`

The local Codex sandbox still cannot launch Chromium directly, but the approved containerized runner completed the real staging gate. The broader role/trading e2e follow-up suite remains a required pre-production gate on a Docker-capable approved host.

## Final Branch Revalidation - 2026-07-10

The final `candidate/webapp-ui-ux-unification` validation was repeated after the
real Iran/foreign staging transport, market-history, and sequence-parity fixes.
Production was not released.

Results:

- Real two-server staging matrix: `4/4` drivers passed with zero request errors,
  clean parity, clean sync health, and zero cleanup residue.
- Isolated staging role/trading browser gate: `22/22` passed, including Tier 2
  direct/mediated execution, customer/accountant visibility, market schedule,
  realtime trade notifications, exports, block policy, and offer mutations.
- Deterministic visual baseline with accessibility smoke: `26/26` passed on a
  second compare-only run.
- Viewport and market mutation gate: `10/10` passed across `360` through `1440`
  pixel widths.
- Full frontend unit suite: `127/127` files and `1101/1101` tests passed.
- UI guard, production frontend build, `git diff --check`, and `45/45`
  sequence/deployment surface tests passed.

The first fresh visual capture exposed that no screenshot baselines were stored
in the repository and that sparse pages could falsely compare within the old
2% pixel tolerance while the boot loader was visible. The harness now waits for
the app mount marker, boot-loader removal, and route-specific ready content,
uses a 0.5% pixel tolerance, and stores the reviewed Linux Chromium baselines in
`frontend/e2e/non-messenger-visual-baseline.spec.ts-snapshots/`.

Artifacts:

- `tmp/staging-two-server-full-matrix/UIUX_FINAL_FIXED_20260710T110500Z/`
- `tmp/staging-role-trading-e2e/UIUX_FINAL_PASS_20260710T115000Z/`
- `tmp/stage10-staging-playwright/UIUX_FINAL_20260710T120500Z/`
- `tmp/claude/full_matrix_logs/UIUX_FINAL_FIXED_20260710T110500Z/`
- `tmp/chatgpt/full_matrix_logs/UIUX_FINAL_FIXED_20260710T110500Z/`
