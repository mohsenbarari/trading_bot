# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-21 | `candidate/webapp-native-controls-v1` aligns all 29 non-Market routes and Messenger M01–M14: RTL/Vazirmatn, 48px controls, grouped lists, safe-area and shared focus/dialog/sheet UX. Exact visual guards fail closed. Evidence: 167/167 routes, 13/13 Messenger, 8/8 boundary viewports and 169/1962 tests. It is not owner/deploy authority.
- 2026-08-21 | Market is owner-frozen: direct source matches `main` and 390/1440 production captures are pixel-identical. Shared chrome needs explicit Market compatibility scope; Market feed/actions/meter/overtime/history, calendar confirm and delivery interiors retain accepted behavior.
- 2026-08-20 | Native means installed PWA: one 48px back control, bottom primary CTA, shared destructive dialog, keyboard-safe forms and grouped account/profile/operations/admin/auth surfaces. Today trades remains a horizontal row.
- 2026-08-15 | Stage 8 closed for UI/UX only (Market A+C: 960/830/130; access 270; zero deferred). It is not merge/deploy/Sites authority.
- 2026-08-14 | Invitation is `410`; Telegram Mini App is retired. Overtime preference lives at `/settings`. Stage 6/7 preserve server-authoritative roles/PII, shared dialogs, cancellation without mutation, reduced motion, and protected-region hashes.

## Constraints

- Messenger restyle may not change schema/WebSocket/upload/cache, `album_id + album_index`, permissions, legacy default or rollback. Generic chrome uses amber; Telegram-connect may remain blue.
- Current-user authority is token-bound; owner routes use guards. Owner-relation DELETE locks `expected_action`.
- New CSS uses `--ds-*`; `--ui-v2-*` only belongs to catalog V2. Product inputs use `--ds-control-*`.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/Escape return, reduced motion, 200% zoom, no horizontal overflow/unnamed or nested controls/obscured CTA, stable async geometry, identity-safe stale requests, source hashes, and production-build browser evidence.
