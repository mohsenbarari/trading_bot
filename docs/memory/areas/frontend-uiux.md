# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-14 | Invitation validation stays `410`; pre-auth Telegram uses shared runtime; overtime preference lives at `/settings`; Market/Telegram inventories use exact overlays.
- 2026-08-15 | Stage 8 closed on Market A+C `main` (960/830/130, 270 access, zero deferred). Authority is Stage 8 UI/UX only, not push/deploy/Sites; pre-Market Gate A v3 is non-promotable.
- 2026-08-14 | Stage 7 closed: NONE-only copy, keyboard, live-region, reduced-motion; protected routes keep 200ms. Cross-boundary evidence passed 12/12.
- 2026-08-14 | Stage 6 closed: admin/profile projections stay server-authoritative; shared dialogs replace in-scope confirms. PII stays server-side; cancel/Escape never mutate; FULL/MIXED/CreateChannel stay protected.
- 2026-08-11 | Owner-relation DELETE locks `expected_action`; Customer/Accountant query changes keep one root and scroll owner.
- 2026-08-18 | Market keeps 44px two-tap active cards, meter/hourglass, and compact top-separated traded/expired history. Today trades are identity/private. Completed trades refresh Today/self-history via private events plus receipts and one five-second toast. Bot uses ☀️/📆 and hides customer route.
- 2026-08-09 | Current-user authority is token-bound; owner-only routes use guards, not visibility.
- 2026-08-19 | UIUX V3 merged by fast-forward into `main` at `e74964f3` and deployed only to both staging roles after 167/167 strict runtime and 169/1959 tests. Coverage remains 38 aligned, 6 frozen, 1 inactive; invalid public IDs fail closed. Production/Sites authority remains false; Figma stays DRAFT and Mini App excluded.

## Constraints

- Protected Market/Messenger/Home changes need source/behavior/visual guards. Market A+C is visual/interaction only; overtime stays under Account/Settings. UIUX V3 must not edit hash-frozen Messenger runtime (including ShareReceive) or the TradingSettings calendar confirm; new CSS must not introduce `--ui-v2-*` markers unless it is catalog V2.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
