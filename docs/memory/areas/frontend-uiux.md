# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-12 | Workspace account deletion uses a body-teleported V2 portal with exact name + acknowledgement, locked `expected_action=delete-account`, receipt identity/status checking, and fixed safe failure copy. Reason: destructive actions must stay visible/accessibly trapped while raw server detail, stale receipts, and clipped route-local overlays cannot cause unsafe state changes.
- 2026-08-12 | Live Customer/Accountant workspace session termination keeps the existing body-teleported confirmation and accepts only an exact `terminated_session_id`; all failure paths retain displayed session/route/relation with fixed safe copy. Reason: raw API details and malformed receipts must not leak or mutate local workspace state.
- 2026-08-12 | Stage 6 invitations are copy-only/no-total/no-store, reconcile 400/404, clear sensitive state on 403, and use Teleported confirm. PublicProfile block/unblock uses that confirm; cancel cannot mutate, only `{success:true}` flips state, and receipts/errors never expose names or server payloads. Reason: prevent bearer/detail leakage and clipped destructive actions.
- 2026-08-11 | Stage 6 Phase 1–3 are delivered but broader work remains partial. Peer profile data is server-masked and public-profile URLs are ID-only; raw directory search is auth-volatile and never URL/history/storage, while sensitive admin authority is server-enforced. Reason: no client-only PII/authority or accidental closure.
- 2026-08-11 | Owner-relation DELETE uses locked `expected_action`; Customer/Accountant query-only changes retain one root and scroll owner. Reason: prevent stale destructive escalation and remount races.
- 2026-08-08 | V2 is mobile-first at `360/375/390/414/430`; `ui-*`/DS V2 are canonical, Figma/browser evidence is source-bound, and Sites previews stay owner-only. Reason: purposeful, rollback-safe design evolution.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Protected Market/Messenger/Home changes need guarded source/behavior/visual parity; simplification preserves authority, privacy, validation, recovery and local feedback.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA, and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
