# Frontend UI/UX

Load for Vue/PWA UI/UX refactoring, Design System V2, Figma/Sites evidence, or shared UI work that can touch protected surfaces.

## Decisions

- 2026-08-08 | Webapp UI/UX V2 is mobile-first at `360/375/390/414/430`; desktop is adaptive and widens only for complex workspaces. The language is modern financial—precise, fast, structured, and sparse—under purposeful minimalism. Reason: mobile dominates, and persistent content must support a decision, action, essential state, or risk prevention.
- 2026-08-09 | `ui-*` and Design System V2 are canonical; legacy workspace primitives migrate through compatible adapters until their owning workflow explicitly changes them. Reason: avoid component/CSS drift and premature behavior changes.
- 2026-08-08 | Figma is the primary design source; external-assistant quorum is non-blocking unless requested. Keep implementation and closure separate, and bind browser/Figma evidence plus a private, owner-only, stage-isolated Sites preview to the implementation source. Reason: immutable provenance, rollback isolation, and preservation of prior evidence.
- 2026-08-09 | Current-user authority is centralized in a token-bound, revision-safe loader; owner-only destinations require route guards, not visibility alone. Reason: prevent stale cross-account cache races and deep-link bypass.

## Constraints

- Treat Market, Messenger, and the Home Market region as protected; shared shell, style, or component changes require guard-backed source, behavior, and visual equivalence or explicit disposition.
- UI simplification must preserve backend authority, roles, privacy, validation, recovery, and security. Never expose raw route, backend, or server metadata; keep invalid destinations non-interactive and sensitive-action confirm, busy, and outcome feedback local.
- Acceptance includes WCAG 2.2 AA, keyboard and visible focus, reduced motion, 200% zoom, no horizontal overflow, and no obscured CTA.
- Each stage must be independently test-bound, hash-bound, and rollback-safe. Evaluate lint and format as a base/current delta; disclose inherited debt instead of claiming blanket cleanliness.
- Sites is private evidence, not product deployment. Opaque-cookie registration cutover must be atomic or under maintenance, or use a version-gated forced reload; never restore raw-token compatibility for already-loaded legacy JavaScript.
- For Web Push server-authoritative rebind, one or two identical requests are valid for sequential consumers; more than two is a failure.
