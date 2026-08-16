# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-14 | Main/UIUX integration keeps raw invitation validation at unconditional `410`, sends pre-auth Telegram replies through the shared runtime, and exposes eligible overtime preference at `/settings`. Market and Telegram inventory use exact integration overlays, not rewritten baselines. Reason: preserve security/UIUX and newer main behavior fail-closed.
- 2026-08-15 | Stage 8 closed: official local V2 on merged Market A+C `main` passed 960/830/130 after evidence/fixture fixes (270 access; 0 harness-deferred). Owner aesthetic approval recorded. `acceptanceAuthority=true` for Stage 8 UI/UX only. No push/staging/Sites/deploy. Pre-Market Gate A v3 remains non-promotable.
- 2026-08-14 | Stage 7 is closed: NONE-only copy, keyboard, live-region and reduced-motion changes; protected routes retain 200ms. Follow-up cross-boundary evidence passed 12/12 with zero request failures.
- 2026-08-14 | Stage 6 is closed: admin/profile projections remain server-authoritative; shared dialogs replace in-scope confirms. PII stays server-side, cancel/Escape never mutate, and FULL/MIXED plus the CreateChannel overlay remain protected.
- 2026-08-11 | Owner-relation DELETE uses locked `expected_action`; Customer/Accountant query-only changes retain one root and scroll owner. Reason: prevent stale destructive escalation and remount races.
- 2026-08-16 | Market/dashboard: 44px cards retain two-tap, meter/hourglass and green-traded/gray-expired history. Today trades are identity-bound: ungrouped ID, server-Jalali time, counterparty before time, authorized-party description; offer/customer/path columns absent. Customers get privacy-scoped trade/Market history. Bot rows use ☀️ cash/📆 tomorrow, omit customer route for customer/head, and end ID/time; bot PDF omits counterparty and ends description.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Protected Market/Messenger/Home changes need guarded source/behavior/visual parity; simplification preserves authority, privacy, validation, recovery and local feedback. Market A+C is visual/interaction only; overtime stays under Account/Settings.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA, and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
