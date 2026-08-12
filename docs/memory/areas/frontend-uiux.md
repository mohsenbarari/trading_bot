# Frontend UI/UX

Load for Vue/PWA refactoring, Design System V2, Figma/Sites evidence, or shared UI work.

## Decisions

- 2026-08-12 | Local storage-cache clear on `/account/storage` uses a body-teleported confirmation; cancel/Escape leave files and size unchanged, and local failures keep the dialog with fixed safe copy. Reason: inline confirm lacked Escape/portal, and raw cache-clear causes must not leak.
- 2026-08-12 | Account-security session mutations accept only exact HTTP 200 receipts; confirmation copy stays generic without device names and failures retain the session list. Reason: raw server detail and device names must not leak from the `/account/security` dialog.
- 2026-08-12 | Admin user deletion and terminate-all accept only exact HTTP 200 receipts; confirmation copy stays generic and failures retain context. Reason: account_name/mobile and raw server detail must not leak from the admin user dialog.
- 2026-08-12 | Admin commodity/alias mutations require exact status/identity receipts; body-teleported deletion failures retain context. Reason: malformed/raw responses cannot mutate state or leak details.
- 2026-08-12 | Workspace relation mutations accept matching `revoked`/`deleted` receipts; failures retain context with fixed safe copy. Reason: prevent raw-detail leaks and stale reconciliation.
- 2026-08-12 | Workspace account deletion uses a body-teleported V2 portal, typed name/acknowledgement, locked `expected_action`, receipt checking and fixed safe failure copy. Reason: prevent raw details, stale receipts and clipped overlays.
- 2026-08-12 | Customer/Accountant session termination accepts only exact `terminated_session_id`; failures retain session/route/relation with fixed safe copy. Reason: raw API details and malformed receipts must not leak or mutate local state.
- 2026-08-12 | Stage 6 invitations are copy-only/no-total/no-store, reconcile 400/404, clear sensitive state on 403, and use Teleported confirm. PublicProfile block/unblock reuses it; cancel cannot mutate and only `{success:true}` flips state. Reason: prevent bearer/detail leakage and clipped destructive actions.
- 2026-08-11 | Stage 6 Phase 1–3 are delivered but broader work is partial. Peer data is server-masked, public-profile URLs are ID-only, directory search is auth-volatile and never URL/history/storage, and sensitive admin authority is server-enforced. Reason: no client-only PII/authority or accidental closure.
- 2026-08-11 | Owner-relation DELETE uses locked `expected_action`; Customer/Accountant query-only changes retain one root and scroll owner. Reason: prevent stale destructive escalation and remount races.
- 2026-08-08 | V2 is mobile-first at `360/375/390/414/430`; `ui-*`/DS V2 are canonical, Figma/browser evidence is source-bound, and Sites previews stay owner-only. Reason: purposeful, rollback-safe design evolution.
- 2026-08-09 | Current-user authority is token-bound/revision-safe; owner-only routes use guards, not visibility. Reason: prevent stale cache and deep-link bypass.

## Constraints

- Protected Market/Messenger/Home changes need guarded source/behavior/visual parity; simplification preserves authority, privacy, validation, recovery and local feedback.
- Acceptance requires WCAG 2.2 AA, keyboard/focus/reduced-motion, 200% zoom, no horizontal overflow/obscured CTA, and identity-safe stale-request handling.
- Stages are test/hash-bound and rollback-safe; Sites is private evidence, not deployment; Web Push permits only one or two identical server-authoritative rebinds.
