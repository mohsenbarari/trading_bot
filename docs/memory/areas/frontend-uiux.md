# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-11 | Stage 6 Phase 1 is Admin landing only: remove duplicate metadata/accordions while retaining authorized actions/navigation; defer invites, directory/detail, and PII/profile to gated slices. Reason: authority/privacy changes need cross-layer contracts.
- 2026-08-11 | Owner-relation DELETE requires locked-state `expected_action` (`cancel-pending`, `delete-relation`, or `delete-account`); terminal detail reads support recovery/audit while lists stay live-only. Reason: prevent stale destructive escalation.
- 2026-08-11 | Customer/Accountant state is canonical `q/filter/scroll/tab`; query-only changes retain one route root, history, and its scroll owner. Reason: avoid remount races, blank/duplicate UI, and lost context.
- 2026-08-08 | UI/UX V2 is mobile-first at `360/375/390/414/430`, widening only complex workspaces. Use sparse, modern-financial purposeful minimalism: persistent content must aid a decision, action, essential state, or risk prevention.
- 2026-08-09 | `ui-*` and Design System V2 are canonical; legacy workspace primitives use adapters until their workflow migrates. Reason: avoid component/CSS drift and premature behavior changes.
- 2026-08-08 | Figma is primary; external quorum is optional. Separate implementation/closure and source-bind Figma/browser evidence plus a stage-isolated owner-only Sites preview. Reason: immutable provenance and rollback isolation.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache races and deep-link bypass.

## Constraints

- Treat Market, Messenger, and Home Market as protected; shared changes need guard-backed source/behavior/visual equivalence or explicit disposition.
- Simplification preserves backend authority, roles, privacy, validation, recovery, and security; hide raw metadata, keep invalid destinations inert, and give sensitive actions local feedback.
- Acceptance: WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow, and no obscured CTA.
- Async work is generation/identity-safe, aborts stale requests where possible, preserves loaded-empty state, and reconciles only its initiating relation/draft.
- Each stage is test/hash-bound and rollback-safe; report lint/format as base/current delta and disclose inherited debt.
- Sites is private evidence, not product deployment. Opaque-cookie registration cutover is atomic/maintenance-bound or forces version-gated reload; never restore raw-token compatibility for loaded JavaScript.
- For server-authoritative Web Push rebind, one or two identical requests are valid; more than two fails.
