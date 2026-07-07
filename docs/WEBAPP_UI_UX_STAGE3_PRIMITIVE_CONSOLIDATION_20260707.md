# WebApp UI/UX Stage 3 - Primitive Family Consolidation

Date: 2026-07-07
Branch: `candidate/webapp-ui-ux-unification`

## Scope

Stage 3 reduces visual drift between the older `ds-*` workspace primitive family and the newer `ui-*` primitive family.

This stage does not touch Market request flow, Offer interaction flow, Telegram behavior, backend behavior, or production/staging runtime configuration.

## Canonical Direction

The canonical visual primitive family for new WebApp UI work is `ui-*`.

`WorkspaceShell` remains as a workspace layout primitive because it owns a distinct page contract:

- title/eyebrow/description header;
- optional back button event;
- optional toolbar;
- optional action slot;
- stack/split workspace body with aside support.

The smaller workspace primitives now render through `ui-*` primitives while preserving their existing `ds-*` public classes for backward compatibility:

- `WorkspaceSection` wraps `AppSectionCard`;
- `WorkspaceStatTile` wraps `AppMetricCard`;
- `WorkspaceActionTile` wraps `AppActionCard`;
- `WorkspaceNotice` wraps the generalized `AppToast`;
- `WorkspaceDangerZone` wraps `AppDangerZone`.

This keeps existing workspace pages stable while making future visual changes flow through the same primitive family.

## AppToast Generalization

`AppToast` now supports:

- optional `message`;
- optional default slot;
- optional `icon` slot;
- `role="status" | "alert" | "note"`;
- computed `aria-live` defaults.

Existing live app toasts continue to pass a title, message, tone, click behavior, close behavior, and swipe behavior through `AppToasts.vue`.

## New Primitive Decision

No new primitive was added in this stage.

The roadmap listed potential future primitives (`AppChip`, `AppDisclosure`, `AppSkeleton`, a data-list/table primitive, and `AppOfferCard`) only if they remove real duplication. Current Stage 3 work found enough existing primitives for the workspace consolidation goal. Adding new primitives now would expand scope without reducing current duplication.

`AppOfferCard` remains explicitly deferred until the Market/OffersList test-hook hardening stage is complete.

## Layout Contract

Use these layout choices for new non-Messenger WebApp work:

- Narrow mobile-first pages: `AppPage narrow` or `AppWorkspace narrow`, max width from `--ds-workspace-narrow-width`.
- Workspace/master-detail pages: `WorkspaceShell layout="split"` or `AppMasterDetail`, max width from `--ds-workspace-max-width`.
- Admin pages: use `AppWorkspace` / `AppSectionCard` / `AppToolbar` and avoid bespoke page shells unless a missing primitive is documented first.
- Market: do not reuse workspace primitives for Market cards or offer lists until the Market protected contract allows DOM-level refactor. Market remains under the Market Protected Surface Rule.

## Backward Compatibility

Existing `ds-*` public classes remain on workspace primitive roots, so current tests and page-level selectors continue to work:

- `.ds-workspace-section`
- `.ds-stat-tile`
- `.ds-action-tile`
- `.ds-workspace-notice`
- `.ds-danger-zone`

The same roots also expose their canonical `ui-*` classes, making the primitive family choice testable.

## Validation

Executed locally:

- `npm run guard:ui`
- `npm run test:unit:run -- src/components/ui/AppPrimitives.test.ts src/components/workspace/WorkspacePrimitives.test.ts`

Both passed before this document was written.

Full build and broader impacted tests must still be run before the Stage 3 commit is finalized.

## Deferred Items

- Do not add `AppOfferCard` in Stage 3.
- Do not remove legacy `ds-*` selectors yet; that belongs to Stage 9 cleanup after all dependent pages have migrated.
- Do not refactor Market/OffersList DOM in Stage 3.
