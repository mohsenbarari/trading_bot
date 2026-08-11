# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-11 | Owner-relation DELETE requires semantic `expected_action` (`cancel-pending`, `delete-relation`, or `delete-account`), validated against locked current state before effects. Detail reads may return terminal relations for recovery/audit; lists stay live-only. Reason: stale UI state must not escalate or misreport destructive scope.
- 2026-08-11 | Customer/Accountant state is canonical `q/filter/scroll/tab`; query-only changes preserve one route root, history, and the correct scroll owner. Reason: full-path remounts can race transitions, blank/duplicate UI, and lose context.
- 2026-08-08 | UI/UX V2 is mobile-first at `360/375/390/414/430`, adaptively widening only complex workspaces. Use sparse, structured modern-financial purposeful minimalism; persistent content must support a decision, action, essential state, or risk prevention.
- 2026-08-09 | `ui-*` and Design System V2 are canonical; legacy workspace primitives use adapters until their workflow migrates. Reason: avoid component/CSS drift and premature behavior changes.
- 2026-08-08 | Figma is primary; external-assistant quorum is non-blocking unless requested. Separate implementation from closure and source-bind browser/Figma evidence plus a stage-isolated, owner-only Sites preview. Reason: immutable provenance and rollback isolation.
- 2026-08-09 | Current-user authority uses a token-bound, revision-safe loader; owner-only destinations require route guards, not visibility alone. Reason: prevent cross-account cache races and deep-link bypass.

## Constraints

- Treat Market, Messenger, and Home Market as protected; shared changes require guard-backed source, behavior, and visual equivalence or explicit disposition.
- Simplification preserves backend authority, roles, privacy, validation, recovery, and security. Hide raw route/backend/server metadata; keep invalid destinations inert and sensitive-action feedback local.
- Acceptance includes WCAG 2.2 AA, keyboard and visible focus, reduced motion, 200% zoom, no horizontal overflow, and no obscured CTA.
- Async workspace work is generation/identity-safe, aborts stale requests where possible, preserves loaded-empty state, and reconciles only its initiating relation/draft.
- Each stage is test/hash-bound and rollback-safe. Report lint/format as base/current delta and disclose inherited debt.
- Sites is private evidence, not product deployment. Opaque-cookie registration cutover is atomic/maintenance-bound or forces version-gated reload; never restore raw-token compatibility for loaded legacy JavaScript.
- For Web Push server-authoritative rebind, one or two identical requests are valid for sequential consumers; more than two is a failure.
