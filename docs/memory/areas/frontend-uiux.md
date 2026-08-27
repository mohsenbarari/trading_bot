# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-27 | Native Feel V2 phases 1–11 on `candidate/webapp-native-app-v2` from `951ca9f0`. Verdict: READY FOR INDEPENDENT NATIVE UI REVIEW. Live non-market UI uses `AppInsetGroup` / grouped `AppListItem` / wrapping `AppButton` / `--ds-*`; `--ui-v2-*` only in catalog V2 or test-locked hooks. Market hero hash `f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860` and calendar confirm stay frozen. Messenger is visual-only; albums stay `album_id`+`album_index`; rollout stays legacy-default. Session V2 sheet cannot Escape-dismiss; leftover unscoped session stays for protected routes. Profile trades are inset (`mini-trade-card` class stays). Operation danger pages dropped repeated essays; confirm dialogs keep locked legal copy. Remaining: dual-token BottomNav, `ui-v2-workspace-*` hooks, invite `.copy-btn`, market/channel HelpPopover, `Owner*ManagerModal` off the live path. `vue-tsc` remains pre-existing (includes Market).
- 2026-08-22 | Market is owner-frozen; feed, meter, overtime, calendar confirm, and delivery interiors stay unchanged without explicit owner scope.
- 2026-08-21 | Native standardization V1 receipts on `main` `428dd1a0` stay historical. Production/Sites stay untouched.
- 2026-08-20 | Native means installed PWA: 48px back, bottom primary CTA, shared destructive dialog, keyboard-safe forms, and grouped lists. Today trades stay a horizontal row.

## Constraints

- Messenger restyle may not change schema, WebSocket, upload, cache, `album_id + album_index`, permissions, legacy default, or rollback.
- Current-user authority is token-bound; owner-relation DELETE locks `expected_action`.
- New product CSS uses `--ds-*`. Product inputs use `--ds-control-*` or `App*`.
- Acceptance: WCAG 2.2 AA, keyboard/Escape, reduced motion, 200% zoom, no overflow/unnamed/nested/obscured CTA. Do not declare owner-approved or production-ready.
