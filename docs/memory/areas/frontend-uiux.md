# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-14 | Integration: invitation validation is always `410`; pre-auth Telegram uses the shared runtime; eligible overtime preference lives at `/settings`; Market/Telegram inventories use exact overlays. Reason: preserve fail-closed security/UIUX and newer main behavior.
- 2026-08-15 | Stage 8 closed on merged Market A+C `main`: official V2 passed 960/830/130, 270 access and zero deferred; owner aesthetics approved. Authority covers Stage 8 UI/UX only, not push/deploy/Sites; pre-Market Gate A v3 is non-promotable.
- 2026-08-14 | Stage 7 closed: NONE-only copy, keyboard, live-region and reduced-motion; protected routes retain 200ms. Cross-boundary evidence passed 12/12 with zero request failures.
- 2026-08-14 | Stage 6 closed: admin/profile projections stay server-authoritative; shared dialogs replace in-scope confirms. PII stays server-side, cancel/Escape never mutate, and FULL/MIXED/CreateChannel remain protected.
- 2026-08-11 | Owner-relation DELETE locks `expected_action`; Customer/Accountant query changes retain one root and scroll owner. Reason: prevent stale destructive escalation and remount races.
- 2026-08-18 | Market/dashboard keeps 44px two-tap cards, meter/hourglass and distinct traded/expired history. Today trades are identity/private: ungrouped ID, server-Jalali time, counterparty first, authorized description; offer/customer/path absent. Customers get scoped trade/Market history. Completed trades refresh loaded Today/self-history via private events plus durable receipts and show one five-second toast; granted-but-missing Push auto-restores without prompting. Bot uses ☀️/📆, hides customer route, ends ID/time; PDF omits counterparty and ends description.
- 2026-08-09 | Current-user authority is token-bound and revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Protected Market/Messenger/Home changes require source/behavior/visual guards and must preserve authority, privacy, validation, recovery and feedback. Market A+C is visual/interaction only; overtime stays under Account/Settings.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
