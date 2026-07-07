# WebApp UI/UX Stage 0 Baseline - 2026-07-07

## Scope

This file records the Stage 0 baseline for `docs/WEBAPP_UI_UX_UNIFICATION_ROADMAP_20260707.md`.

Stage 0 adds measurement and guard infrastructure only. It intentionally does not change runtime UI source, trading behavior, Market DOM, OffersList DOM, customer visibility, notification delivery, or sync behavior.

Branch used for this work:

- `candidate/webapp-ui-ux-unification`

## Added Guard Commands

Run from `frontend/`:

```bash
npm run guard:ui
```

The command is hard-failing. It is not wired into the normal build yet because the current baseline has known violations that Stage 1 and Stage 2 must fix first.

The guard checks:

1. `var(--ds-*)` usage in non-Messenger frontend source against canonical definitions in `frontend/src/assets/main.css`.
2. Hardcoded trade-side color values in:
   - `frontend/src/views/DashboardView.vue`
   - `frontend/src/views/MarketView.vue`
   - `frontend/src/components/OffersList.vue`
3. New bespoke `modal-overlay` classes in Vue files outside the explicit allowlist:
   - `frontend/src/components/TradingView.vue`
   - `frontend/src/components/UserProfile.vue`
   - `frontend/src/components/PublicProfile.vue`

Messenger paths remain out of scope for this guard.

## Current Static Baseline

`npm run guard:ui` currently fails as expected.

Current undefined design tokens: 22

- `--ds-accent`
- `--ds-bg-disabled`
- `--ds-bg-soft`
- `--ds-bg-subtle`
- `--ds-bg-surface`
- `--ds-border`
- `--ds-border-subtle`
- `--ds-color-danger-700`
- `--ds-color-info-700`
- `--ds-danger-300`
- `--ds-font-mono`
- `--ds-primary`
- `--ds-primary-800`
- `--ds-primary-soft`
- `--ds-primary-strong`
- `--ds-surface`
- `--ds-surface-soft`
- `--ds-surface-subtle`
- `--ds-text-disabled`
- `--ds-text-strong`
- `--ds-text-tertiary`
- `--ds-warning-700`

Current hardcoded trade-side color findings: 9

- `frontend/src/components/OffersList.vue`
- `frontend/src/views/DashboardView.vue`
- `frontend/src/views/MarketView.vue`

Current modal overlay guard:

- pass

## Added Visual Baseline Harness

Run from `frontend/`:

```bash
npm run test:e2e:ui-baseline -- --update-snapshots
```

The screenshot harness is disabled by default and only runs when `UI_UX_BASELINE=1` is set by the npm script.

Routes covered by the harness:

- `/`
- `/market`
- `/operations`
- `/operations/customers`
- `/operations/accountants`
- `/account`
- `/profile`
- `/notifications`
- `/admin/users`
- `/admin/commodities`
- `/login`
- `/register`
- `/i/uiux-baseline`

Viewport coverage:

- `390x844`
- `1440x900`

Determinism controls:

- fixed timezone: `Asia/Tehran`
- fixed locale: `fa-IR`
- fixed time: `2026-07-07T08:30:00.000Z`
- CSS animations and transitions disabled
- timer/countdown-like elements hidden
- mocked non-Messenger API responses

Optional accessibility smoke check:

```bash
UI_UX_A11Y=1 npm run test:e2e:ui-baseline
```

This currently checks visible interactive controls for a basic accessible name. It is intentionally opt-in until each high-risk route is cleaned up.

## Known Limitations

- The `/i/:code` route uses a controlled mock code (`/i/uiux-baseline`), not a real invitation.
- The harness uses API mocks, so it is a visual regression harness, not an end-to-end production data test.
- Market route screenshots are baseline-only in Stage 0. Market source files must not be refactored before the protected Market stage in the roadmap.
- Current `guard:ui` failures are expected and are the input for Stage 1 and Stage 2.

## Stage 0 Exit Status

- Token drift is reproducible by `npm run guard:ui`.
- Undefined-token detection is a hard failing guard.
- Hardcoded trade-side color detection is a hard failing guard.
- New modal overlay detection is guarded with an allowlist.
- Screenshot baseline harness exists and is deterministic by default.
- No runtime UI source change was made in Stage 0.
