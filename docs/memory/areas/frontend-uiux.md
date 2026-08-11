# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-11 | Stage 6 Phase 1–3 are recorded as delivered scope; broader Stage 6 remains partial/deferred and scoped evidence is not a full freeze or Sites input. Reason: prevent slice closure from authorizing deployment or unfinished roadmap work.
- 2026-08-11 | Ordinary peers get server-masked mobile only; address, presence, membership and trade detail are omitted. Self/admin retain needed fields; self/super-peer actions are read-only; Messenger/Forward discovery is unchanged. Reason: no client-only PII or authority.
- 2026-08-11 | Public-profile routes are ID-only across direct, notification, toast and browser entries; canonicalize legacy query before navigation. Reason: URL/history exposes PII.
- 2026-08-11 | Stage 6 gates landing, directory, PII/profile and authority separately. Raw directory search is auth-volatile, never URL/history/storage; only `scroll` is route context. Reason: prevent PII persistence.
- 2026-08-11 | Owner-relation DELETE requires locked `expected_action` (`cancel-pending`, `delete-relation`, `delete-account`); terminal details are readable while lists are live-only. Reason: prevent stale destructive escalation.
- 2026-08-11 | Customer/Accountant state is `q/filter/scroll/tab`; query-only changes retain one root and scroll owner. Reason: prevent remount races.
- 2026-08-08 | V2 is mobile-first at `360/375/390/414/430`; wider layouts serve complex workspaces and persistent content serves a decision, action, state or risk prevention.
- 2026-08-09 | `ui-*`/Design System V2 are canonical; legacy workspace primitives remain adapters until their workflow migrates. Reason: avoid drift.
- 2026-08-08 | Figma is primary; source-bind Figma/browser evidence and keep Sites previews owner-only. Reason: provenance and rollback isolation.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Market, Messenger, and Home Market are protected; shared changes need guarded source/behavior/visual parity or disposition.
- Simplification preserves authority, privacy, validation, recovery and security; invalid destinations are inert and sensitive actions have local feedback.
- Acceptance: WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow or obscured CTA.
- Async work is generation/identity-safe, aborts stale requests, preserves loaded-empty state and reconciles only its initiator.
- Stages are test/hash-bound and rollback-safe; lint/format separates inherited and new debt.
- Sites is private evidence, not deployment; opaque-cookie registration is atomic/maintenance-bound or version-gated reload.
- Server-authoritative Web Push permits one or two identical rebinds, never more.
