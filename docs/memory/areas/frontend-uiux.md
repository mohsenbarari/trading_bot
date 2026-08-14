# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-14 | Main/UIUX integration keeps raw invitation validation at unconditional `410`, sends pre-auth Telegram replies through the shared runtime, and exposes eligible overtime preference at `/settings`. Market and Telegram inventory use exact integration overlays, not rewritten baselines. Reason: preserve security/UIUX and newer main behavior fail-closed.
- 2026-08-14 | Stage 8 stays open: 270 expected / 0 full / 12 partial / 163 non-counting / `acceptanceAuthority=false`. Official local Gate A is clean-worktree-only and source-bound (`/settings` renders `SettingsView`; local PWA is `pwa-simulation`). Gate A v2 on `3fa04eb7` is blocked by real semantic failures; receipt `fb69a47b` stays non-promotable. No push, staging, or owner acceptance yet.
- 2026-08-14 | Stage 7 is closed: NONE-only copy, keyboard, live-region and reduced-motion changes; protected routes retain 200ms. Follow-up cross-boundary evidence passed 12/12 with zero request failures.
- 2026-08-14 | Stage 6 is closed: admin/profile projections remain server-authoritative; shared dialogs replace in-scope confirms. PII stays server-side, cancel/Escape never mutate, and FULL/MIXED plus the CreateChannel overlay remain protected.
- 2026-08-11 | Owner-relation DELETE uses locked `expected_action`; Customer/Accountant query-only changes retain one root and scroll owner. Reason: prevent stale destructive escalation and remount races.
- 2026-08-08 | V2 is mobile-first at `360/375/390/414/430`; `ui-*`/DS V2 are canonical, Figma/browser evidence is source-bound, and Sites previews stay owner-only. Reason: purposeful, rollback-safe design evolution.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Protected Market/Messenger/Home changes need guarded source/behavior/visual parity; simplification preserves authority, privacy, validation, recovery and local feedback.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA, and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
