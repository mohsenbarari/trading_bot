# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-20 | Owner required one native language on all webapp pages. V2 target is 48px and brand maps to the same amber as `--ds-primary-*`. Auth/profile/directory leftover 44px chrome joined. Market feed stays frozen.
- 2026-08-20 | Owner required AdminMessages, TradingSettings, and Jalali to join the same native 48px language. Historical Stage 4/6 hashes stay readable via `native-app-admin-messages-visual-v1` and `native-app-trading-settings-visual-v1`. Calendar confirm and Market feed stay frozen.
- 2026-08-20 | Integrity pass unified leftover non-Market chrome to 48px native rows/`AppInput`. Live messenger stays on `native-app-messenger-visual-v1`.
- 2026-08-20 | Native App Standardization on `candidate/webapp-native-controls-v1` covers every live webapp surface except Market. Messenger frontend is fully free; Stage 8 hashes stay read-only via `native-app-messenger-visual-v1`. Legacy default and rollback stay. First control commit: `c8239d6c`.
- 2026-08-19 | UIUX V3 fast-forwarded to `main` at `e74964f3` and deployed only to both staging roles (167/167 runtime, 169/1959 tests). Surfaces: 38 aligned, 6 frozen, 1 inactive. Invalid public IDs fail closed. Production/Sites stay unauthorized; Figma DRAFT; Mini App excluded.
- 2026-08-18 | Market keeps 44px two-tap cards, meter/hourglass, and compact traded/expired history. Today trades are identity/private. Completions refresh Today/self-history via private events, receipts, and one 5s toast. Bot uses ☀️/📆 and hides the customer route.
- 2026-08-15 | Stage 8 closed on Market A+C `main` (960/830/130, 270 access, zero deferred). Authority is Stage 8 UI/UX only, not push/deploy/Sites. Pre-Market Gate A v3 is non-promotable.
- 2026-08-14 | Invitation stays `410`; pre-auth Telegram uses shared runtime; overtime lives at `/settings`; Market/Telegram inventories use exact overlays. Stage 7 closed NONE-only copy/keyboard/live-region/reduced-motion; protected routes keep 200ms; cross-boundary 12/12. Stage 6: server-authoritative admin/profile; shared dialogs; PII server-side; cancel/Escape never mutate.
- 2026-08-11 | Owner-relation DELETE locks `expected_action`; Customer/Accountant query changes keep one root and scroll owner.
- 2026-08-09 | Current-user authority is token-bound; owner-only routes use guards, not visibility.

## Constraints

- Market A+C stays frozen. Home may restyle only around `home-market-widget`. Messenger frontend restyle is free under `native-app-messenger-visual-v1`; album rules and legacy default stay. TradingSettings calendar confirm stays. New CSS must not introduce `--ui-v2-*` unless catalog V2. Product fields use `--ds-control-*` (48px, inset, shared radius/focus).
- Acceptance: WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow or obscured CTA, identity-safe stale requests.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence; Web Push allows one or two identical server-authoritative rebinds.
