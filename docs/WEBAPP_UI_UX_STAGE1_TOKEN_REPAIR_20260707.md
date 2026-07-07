# WebApp UI/UX Stage 1 Token Repair - 2026-07-07

## Scope

This file records Stage 1 of `docs/WEBAPP_UI_UX_UNIFICATION_ROADMAP_20260707.md`.

Stage 1 is limited to token repair and guard ergonomics. It does not change Market DOM, OffersList DOM, trade request behavior, offer rendering logic, customer visibility, notifications, sync behavior, or backend code.

Branch:

- `candidate/webapp-ui-ux-unification`

## Token Repair

The 22 undefined `--ds-*` tokens found in Stage 0 are now defined in `frontend/src/assets/main.css`.

Mapping policy:

- add missing scale tokens only when they fit the existing canonical scale;
- map old/semantic names to canonical tokens instead of creating a third token family;
- preserve current visual intent as much as possible by matching existing fallback values where present.

| Missing token | Stage 1 mapping |
| --- | --- |
| `--ds-accent` | `var(--ds-primary-700)` |
| `--ds-bg-disabled` | `var(--ds-bg-hover)` |
| `--ds-bg-soft` | `var(--ds-bg-inset)` |
| `--ds-bg-subtle` | `#f8fafc` |
| `--ds-bg-surface` | `var(--ds-bg-card)` |
| `--ds-border` | `var(--ds-border-medium)` |
| `--ds-border-subtle` | `var(--ds-border-light)` |
| `--ds-color-danger-700` | `var(--ds-danger-700)` |
| `--ds-color-info-700` | `var(--ds-info-700)` |
| `--ds-danger-300` | `#fca5a5` |
| `--ds-font-mono` | system monospace stack |
| `--ds-primary` | `var(--ds-primary-500)` |
| `--ds-primary-800` | `#92400e` |
| `--ds-primary-soft` | `var(--ds-primary-50)` |
| `--ds-primary-strong` | `var(--ds-primary-700)` |
| `--ds-surface` | `var(--ds-bg-card)` |
| `--ds-surface-soft` | `var(--ds-bg-inset)` |
| `--ds-surface-subtle` | `var(--ds-bg-subtle)` |
| `--ds-text-disabled` | `#9ca3af` |
| `--ds-text-strong` | `var(--ds-text-primary)` |
| `--ds-text-tertiary` | `#64748b` |
| `--ds-warning-700` | `#b45309` |

## Guard Changes

`frontend/scripts/check-ui-ux-guards.mjs` now supports stage-specific checks:

```bash
npm run guard:ui
npm run guard:ui:tokens
npm run guard:ui:trade-colors
npm run guard:ui:modal-overlays
```

This keeps the global guard hard-failing while allowing Stage 1 to prove that token drift has been fixed independently from Stage 2 trade-color cleanup.

## Tailwind v4 Decision

Current frontend setup:

- `frontend/src/assets/main.css` imports `tailwindcss`;
- `frontend/postcss.config.js` uses `@tailwindcss/postcss`;
- `frontend/tailwind.config.js` still exists, but Stage 1 does not need to alter Tailwind config.

Decision for this stage:

- keep CSS design tokens in `main.css` as the current canonical runtime source;
- do not migrate Tailwind config values in Stage 1;
- revisit Tailwind `@theme`/config cleanup only after shared primitives and visual regression evidence are stable.

## Validation

Commands run:

```bash
npm run guard:ui:tokens
npm run guard:ui
npm run guard:ui:modal-overlays
npm run build
npx playwright test e2e/non-messenger-visual-baseline.spec.ts --project=chromium --list
```

Results:

- `npm run guard:ui:tokens`: pass.
- `npm run guard:ui`: expected fail only because 9 hardcoded trade-side color findings remain for Stage 2.
- `npm run guard:ui:modal-overlays`: pass.
- `npm run build`: pass.
- Visual baseline spec listing: pass, 26 tests.

## Screenshot Evidence Status

Full screenshot execution is still pending. The current managed sandbox rejects the Playwright web server with:

```text
Error: listen EPERM: operation not permitted 127.0.0.1:5173
```

The harness is ready, but real screenshot capture must run in an environment that can bind the frontend dev/preview server.

## Remaining Stage 1 Risk

Token aliases can slightly change surfaces that previously relied on CSS fallback values. This is expected for undefined-token repair, but route-by-route visual review should still happen before larger visual refactors.

No Market or OffersList DOM/source refactor was performed in this stage.
