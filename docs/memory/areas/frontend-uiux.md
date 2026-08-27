# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-27 | Native Feel V2 on `candidate/webapp-native-app-v2` stays `BLOCKED`: `vue-tsc` still fails only inside frozen `MarketView.vue`. Product sheets use `--ds-*`; catalog CSS keeps `--ui-v2-*`. E2E is fail-closed. Market and messenger behavior stay frozen; no merge/deploy.
- 2026-08-22 | Market is owner-frozen; feed, meter, overtime, calendar confirm, and delivery interiors stay unchanged without explicit owner scope.
- 2026-08-21 | Native standardization V1 receipts on `main` `428dd1a0` stay historical. Production/Sites stay untouched.
- 2026-08-20 | Native means installed PWA: 48px back, bottom primary CTA, shared destructive dialog, keyboard-safe forms, and grouped lists. Today trades stay a horizontal row.

## Constraints

- Messenger restyle may not change schema, WebSocket, upload, cache, `album_id + album_index`, permissions, legacy default, or rollback.
- Current-user authority is token-bound; owner-relation DELETE locks `expected_action`.
- New product CSS uses `--ds-*`. Product inputs use `--ds-control-*` or `App*`.
- Acceptance: WCAG 2.2 AA, keyboard/Escape, reduced motion, 200% zoom, no overflow/unnamed/nested/obscured CTA. Do not declare owner-approved or production-ready.
