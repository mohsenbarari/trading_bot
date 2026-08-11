# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-11 | Stage 6 separates Admin landing from independently gated directory/detail, PII/profile, and authority work. Directory search stays auth-scoped volatile state—never URL/history/storage—because server search may match mobile, name, or account; only normalized `scroll` is route context. Reason: avoid PII persistence while retaining safe return context.
- 2026-08-11 | Owner-relation DELETE requires locked-state `expected_action` (`cancel-pending`, `delete-relation`, or `delete-account`); terminal detail reads support recovery while lists stay live-only. Reason: prevent stale destructive escalation.
- 2026-08-11 | Customer/Accountant state is canonical `q/filter/scroll/tab`; query-only changes retain one route root and scroll owner. Reason: prevent remount races and lost context.
- 2026-08-08 | UI/UX V2 is mobile-first at `360/375/390/414/430`, widening only complex workspaces; persistent content must aid a decision, action, essential state, or risk prevention.
- 2026-08-09 | `ui-*` and Design System V2 are canonical; legacy workspace primitives use adapters until their workflow migrates. Reason: avoid drift and premature behavior changes.
- 2026-08-08 | Figma is primary; source-bind Figma/browser evidence and keep each stage's Sites preview owner-only. Reason: provenance and rollback isolation.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale caches and deep-link bypass.

## Constraints

- Do not broaden Stage 6 directory work into public-profile PII, Messenger discovery, or self/same-level authority; each needs a server-enforced contract.
- Treat Market, Messenger, and Home Market as protected; shared changes need guarded source/behavior/visual equivalence or explicit disposition.
- Simplification preserves authority, roles, privacy, validation, recovery, and security; invalid destinations stay inert and sensitive actions give local feedback.
- Acceptance: WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow or obscured CTA.
- Async work is generation/identity-safe, aborts stale requests, preserves loaded-empty state, and reconciles only its initiating relation/draft.
- Each stage is test/hash-bound and rollback-safe; lint/format reports compare base/current and disclose inherited debt.
- Sites is private evidence, not product deployment. Opaque-cookie registration cutover is atomic/maintenance-bound or version-gated reload; never restore raw-token compatibility for loaded JavaScript.
- Server-authoritative Web Push allows one or two identical rebinds; more than two fails.
