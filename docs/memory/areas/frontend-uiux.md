# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-26 | Native feel V2 process: commit only at phase end; start each phase from a clean worktree; review the staged diff so Market, backend, and unrelated dirty files stay out. `.cursor/settings.json` stays ignored; only `.cursor/rules` and `.cursor/skills` are tracked.
- 2026-08-26 | Native feel V2 starts on `candidate/webapp-native-app-v2` from local `main` `951ca9f0`. Owner scores at kickoff: unification 80, simplification 70, native feel below 50. V1 receipts stay historical; this track owns remaining native-feel gaps (inset grouped lists, 16px gutters, button overflow, copy diet, immersive messenger). Roadmap: `docs/uiux-native-app-v2/ROADMAP.md`.
- 2026-08-21 | Native standardization is on `main` at `428dd1a0`: 29 non-Market routes plus Messenger M01–M14 passed 167/167 browser cases, 13/13 Messenger, 8/8 boundary widths and 169/1962 tests. The identical 171-file frontend artifact is live on both staging roles; backend/bot were not restarted because inference publishing was off-hours `NO_ESTIMATED_COIN_RATES`. Production/Sites remain untouched.
- 2026-08-22 | Market is owner-frozen by default; exceptions require explicit owner scope plus an exact-file guard. The authorized terminal-history exception equalizes regular rows (140px mobile/83px desktop), strengthens only the top edge, and converges missed terminal events through no-store history reloads driven by realtime, reconnect and the existing active poll. Feed/actions/meter/overtime, calendar confirm and delivery interiors remain unchanged.
- 2026-08-20 | Native means installed PWA: one 48px back control, bottom primary CTA, shared destructive dialog, keyboard-safe forms and grouped account/profile/operations/admin/auth surfaces. Today trades remains a horizontal row.
- 2026-08-15 | Stage 8 closed for UI/UX only (Market A+C: 960/830/130; access 270; zero deferred). It is not merge/deploy/Sites authority.
- 2026-08-14 | Invitation is `410`; Telegram Mini App is retired. Overtime preference lives at `/settings`. Stage 6/7 preserve server-authoritative roles/PII, shared dialogs, cancellation without mutation, reduced motion, and protected-region hashes.

## Constraints

- Messenger restyle may not change schema/WebSocket/upload/cache, `album_id + album_index`, permissions, legacy default or rollback. Generic chrome uses amber; Telegram-connect may remain blue.
- Current-user authority is token-bound; owner routes use guards. Owner-relation DELETE locks `expected_action`.
- New CSS uses `--ds-*`; `--ui-v2-*` only belongs to catalog V2. Product inputs use `--ds-control-*`.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/Escape return, reduced motion, 200% zoom, no horizontal overflow/unnamed or nested controls/obscured CTA, stable async geometry, identity-safe stale requests, source hashes, and production-build browser evidence.
