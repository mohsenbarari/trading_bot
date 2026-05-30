# Messenger Refactoring Roadmap

> Date: 2026-05-30  
> Status: Stage 3 controller contracts implemented; legacy Messenger remains the default
> Scope: In-app Messenger frontend and the minimum backend/runtime seams needed to make it fast, stable, standard, dynamic, and user-friendly.

## Goal

Rebuild the Messenger experience into a standard, cohesive, responsive, and polished product surface while preserving every existing business feature. The refactor must be reversible at each phase, test-gated, and isolated from unrelated areas such as Market, Profile, Admin, trading flows, customer/accountant rules, and bot behavior unless a phase explicitly lists that surface as an integration point.

## Implementation Progress

- Stage 1 executive phase is implemented: the reversible feature-flag shell, baseline performance mark utility, legacy-default route switch, and focused Vitest coverage are in place.
- Stage 2 executive phase is implemented for the frontend refactor surface: additive baseline metrics, Messenger-local design tokens, reduced-motion guardrails, build-size baseline capture, and focused Vitest coverage are in place.
- Stage 3 executive phase is implemented for the first controller boundary: route query normalization, selection batch handling, album context resolution, and grouped timeline shaping now live behind typed, focused-tested pure helpers while `ChatView.vue` still owns runtime side effects.
- No real Messenger UI replacement is active by default. The existing `ChatView.vue` path remains the production path unless `messenger_ui_version=refactor` or `VITE_MESSENGER_REFACTOR_ENABLED=true` explicitly enables the shell.

## Current Baseline

Verified during the planning review:

- Current chat API latency on the development server is acceptable at idle: conversations around 108 ms, poll around 88 ms, room messages around 39 ms.
- The development server is limited, but app and DB were not the dominant idle bottleneck during the snapshot. The tile server can still compete for CPU.
- The Messenger frontend is large and complex: `ChatView.vue` is the central orchestrator, `ChatMessageItem.vue` renders many message types and gestures, and the Messenger build chunk is large.
- Stage 2 build baseline from `npm run build` on 2026-05-30: Vite transformed 1970 modules in 22.22s; `mini_app_dist` is 4.3M; `MessengerView-DlRMZph0.js` is 633.97 kB minified / 143.17 kB gzip; `MessengerView-B72QOB6q.css` is 141.55 kB / 29.82 kB gzip; `chatDocumentDownloadBackground-DwxR8CUz.js` is 38.52 kB / 11.10 kB gzip; `useChatFileHandler-x1Bi-bFv.js` is 36.48 kB / 11.82 kB gzip. The largest lazy helper remains `heic2any-C-2qH2Cj.js` at 1,352.97 kB / 341.28 kB gzip.
- `@tanstack/vue-virtual` is installed but not used in `frontend/src`, so the message timeline and conversation list are not truly virtualized.
- Existing optimizations such as pagination, `v-memo`, stable wrapper caches, deferred media hydration, background uploads, and shared visibility observers must be preserved unless replaced by measured better behavior.

## Stage 2 Baseline Contract

The Stage 2 instrumentation is intentionally additive and removable. It stores bounded runtime entries on `window.__messengerStage2Metrics` and keeps the legacy Messenger path as the default UI. Current instrumented surfaces:

- Route/current-user bootstrap: `MessengerView.vue` measures authenticated current-user fetches, records a route-ready DOM snapshot, and starts a short frame-budget probe after the Messenger surface is ready.
- Conversation list: first rendered list count, DOM snapshot, and conversation long-press/menu open marks.
- Chat opening: message-history request duration, first-paint mark from either warm cache or server response, DOM snapshot, and scoped load-error count.
- Timeline movement: scroll-to-bottom requested distance, scroll-to-message duration, and scroll-to-message distance.
- Action surfaces: message context-menu open, selection-mode enter/exit, and selected-count metrics.
- Media surfaces: lightbox open duration plus post-overlay DOM snapshot.
- Upload pipeline: background-upload handoff persistence duration, upload bytes, and first-progress timing.

The Stage 2 design budget is also local to Messenger:

- `frontend/src/styles/messenger-design-tokens.css` defines Messenger-only colors, density, radii, touch-target sizes, shadows, semantic states, z-indexes, and motion durations under `.messenger-page` and `.messenger-refactor-shell`.
- `prefers-reduced-motion: reduce` disables expensive transitions and smooth scrolling inside the Messenger surface.
- No global reset, backend contract, database shape, or production default path changed in this phase.

## Stage 3 Controller Contract

The Stage 3 slice reduces `ChatView.vue` without changing rendering behavior or the production route default. Current controller seams:

- `frontend/src/utils/messengerStage3Controllers.ts` owns pure route-query normalization, conversation-query rebuilding, message-id normalization, selection-batch toggling, album metadata parsing, album context-menu id resolution, and timeline grouping.
- `frontend/src/types/chat.ts` now exposes typed timeline contracts: `ChatSelectionPurpose`, `ChatAlbumTimelineItem`, `ChatTimelineItem`, and `ChatTimelineGroup`.
- `ChatView.vue` delegates grouped timeline construction to `groupMessengerMessages(...)` and clears the extracted timeline cache through `clearMessengerTimelineCache(...)` on room switch.
- The extracted timeline helper preserves the previous stable album-wrapper and stable day-group reference behavior, so the existing `v-memo`/album rendering optimizations remain intact.
- The slice intentionally leaves websocket, media, composer, overlay back-stack, pinned-message, and search side effects in the legacy orchestrator until their focused controller tests are added in later phases.

Rollback remains local: revert the Stage 3 utility/tests and the small `ChatView.vue` delegation patch; no backend, API, schema, or feature-flag default changed.

## Non-Negotiable Safety Rules

- No big-bang rewrite.
- No unrelated product changes in Messenger refactor commits.
- No API contract changes unless additive and guarded.
- No destructive migration for UI refactor phases.
- Keep the current Messenger path available until the new path passes all gates.
- Every phase must have a rollback plan before implementation starts.
- Every phase must have focused tests before moving to the next phase.
- Performance changes must be measured on a weak or mid-range mobile profile, not only desktop.
- Persian/Jalali, RTL, account-status, customer, accountant, group/channel, notification, and upload contracts are part of the Messenger feature surface and must not regress.

## Existing Feature Inventory To Preserve

The refactor must preserve at least these current capabilities:

- Direct chats, group rooms, optional channels, and mandatory system channel.
- Direct/group/channel routing through `/chat?user_id=...` including negative room ids for rooms.
- Text, sticker, emoji, image, video, album, voice, document, file, and location messages.
- Reply, forward, multi-target forward, share target receive, selection mode, copy, edit, delete, and pinned messages.
- Reactions, mentions, unread mention badges, muted room notification behavior, typing/upload activity, read receipts, and realtime message delivery.
- Background/resumable upload behavior, route-change upload survival, media cache, document cache, share/open/download flows, and upload cancellation.
- Public-profile navigation, relation-aware accountant labels, customer visibility restrictions, owner-resolution metadata, and blocked/customer access rules.
- Search-in-chat, global/in-room search UI, scroll-to-reply/search target, sticky date separators, Jalali/Tehran timestamp display.
- PWA recovery behavior, browser notification routing, unread badge policy, and session/account-status runtime guards.

## Rollback Strategy

The rollback model is designed so an unliked UI direction can be disabled without disturbing other project work.

### Code Isolation

- Build new Messenger UI slices behind a version gate, for example `messengerUiVersion = 'legacy' | 'refactor'`.
- Keep legacy `ChatView.vue` behavior intact until the final acceptance phase.
- Prefer new files under a dedicated Messenger refactor boundary, for example `frontend/src/components/messenger-v2/` or `frontend/src/features/messenger/`, while reusing existing API/composable contracts through adapter functions.
- Avoid moving shared files used by Market/Profile/Admin unless the phase explicitly requires it and tests cover all consumers.

### Runtime Kill Switch

- Add a frontend-only feature flag before visual rollout, for example `VITE_MESSENGER_REFACTOR_ENABLED=false` by default.
- Optionally add a local developer override such as `localStorage.messenger_ui_version = 'refactor'` during test rollout.
- Production rollout should default to legacy until the browser matrix and manual approval pass.

### Commit and Release Shape

- Each phase should be one or more small commits with a clear scope.
- Do not mix Messenger refactor commits with customer/accountant/market/admin changes.
- If a phase is rejected before database changes, rollback is simply turning the flag off or reverting the phase commits.
- If a future phase needs additive backend/schema support, keep the old columns/contracts alive for at least one release and only remove legacy code after explicit acceptance.

## Recommended Package Policy

Prefer existing dependencies before adding new ones.

### Use Existing Packages

- `@tanstack/vue-virtual`: primary candidate for timeline and conversation-list virtualization.
- `@vueuse/core`: use for resize, media query, visibility, intersection, pointer/gesture helpers where it simplifies code without hiding critical behavior.
- `radix-vue`: use selectively for accessible dialog/popover primitives if it reduces custom overlay bugs.
- `lucide-vue-next`: use for consistent icon buttons instead of custom SVGs or text-only controls.
- `localforage`: keep for cache surfaces already using IndexedDB-style persistence.
- `photoswipe`, `cropperjs`, `fabric`, `heic2any`, `leaflet`: keep lazy-loaded or route-local; do not pull them into initial Messenger boot.

### Avoid Unless A Measured Gap Appears

- A full UI framework replacement.
- A new gesture library for core chat scrolling/swiping before Pointer Events and existing VueUse utilities are exhausted.
- A heavy animation framework for the message timeline.
- More global state libraries; Pinia already exists and should be enough.

### Optional Dev-Only Additions

- `rollup-plugin-visualizer` or equivalent bundle analyzer if bundle regression tracking needs a repeatable report.
- `@sentry/vue` or OpenTelemetry browser instrumentation only if production monitoring is approved; it should be introduced as a separate observability phase, not bundled into UI refactor work.

## Phase 0 - Baseline And Instrumentation

### Objective

Create a measurable baseline before any visual or architectural change.

### Work Items

- Add lightweight performance marks around:
  - Messenger route mount.
  - conversation list first render.
  - chat open request start/end.
  - first message paint.
  - scroll-to-bottom and scroll-to-reply.
  - context menu open.
  - selection-mode enter/exit.
  - lightbox open.
  - media upload handoff and first progress event.
- Capture bundle/chunk sizes for Messenger-related chunks.
- Record DOM node counts for conversation list and loaded timeline.
- Record scroll FPS/jank on a weak mobile profile and a desktop profile.
- Add backend timing logs or metrics for `/api/chat/conversations`, direct message history, room message history, `/api/chat/poll`, and upload finalize paths.

### Validation Gate

- A baseline report exists with before numbers.
- No product behavior changes.
- Existing Messenger unit and Playwright tests remain green.

### Rollback

- Instrumentation must be gated or removable without touching Messenger behavior.

## Phase 1 - Feature Flag And Compatibility Harness

### Objective

Create a reversible shell for the new Messenger UI without replacing the current one.

### Work Items

- Add the Messenger UI version flag.
- Introduce an adapter boundary that exposes the current Messenger data contract to both legacy and refactor views.
- Build a minimal `MessengerRefactorShell` that can render either a placeholder or read-only conversation list from the same data.
- Add smoke tests proving legacy remains the default.

### Validation Gate

- Legacy Messenger route renders exactly as before when the flag is off.
- Flag-on shell can mount without breaking auth, bottom nav, notification runtime, or route guards.

### Rollback

- Turn the flag off. No backend or data migration involved.

## Phase 2 - Design System And Motion Budget

### Objective

Define one visual language before rewriting components.

### Work Items

- Define Messenger-local design tokens:
  - background layers.
  - bubble colors.
  - list row density.
  - unread/mention/pinned states.
  - danger/warning/success treatments.
  - spacing and touch target sizes.
- Define motion rules:
  - timeline scrolling should not animate layout shifts.
  - message send, reaction, selection, context menu, and overlay transitions may animate.
  - large lists should avoid `v-auto-animate` by default.
  - respect `prefers-reduced-motion` and low-end mode.
- Standardize icon use through `lucide-vue-next`.
- Reduce expensive `backdrop-filter`, oversized shadows, and nested glass cards on hot paths.

### Validation Gate

- Visual tokens are documented and used in new components only.
- No legacy CSS reset or global layout churn.
- Design review screenshots cover mobile and desktop.

### Rollback

- New tokens live under Messenger refactor styles and can be unused by switching the flag off.

## Phase 3 - State And Controller Decomposition

### Objective

Reduce `ChatView.vue` responsibility before changing rendering behavior.

### Work Items

- Extract or formalize controllers for:
  - room selection and route sync.
  - timeline loading and grouping.
  - overlays and back-stack.
  - selection and batch actions.
  - search/navigation state.
  - pinned-message state.
  - composer state.
- Keep existing composables (`useChatMessages`, `useChatWebSocket`, `useChatMedia`, `useChatScroll`) stable until tests cover the new boundary.
- Add typed contracts for conversation rows, timeline items, album wrappers, and message action payloads.

### Validation Gate

- Focused Vitest tests cover each controller.
- Existing direct/group/channel E2E smoke tests pass on legacy and refactor flag paths where applicable.

### Rollback

- Controller extraction must preserve legacy behavior; if unstable, revert only the extracted controller commits.

## Phase 4 - Message Renderer Split

### Objective

Replace the monolithic per-message renderer with type-specific components.

### Work Items

- Split `ChatMessageItem.vue` into small renderers:
  - text/sticker renderer.
  - image/video renderer.
  - album renderer.
  - voice renderer.
  - document renderer.
  - location renderer.
  - system/recovery renderer.
- Keep a thin `MessageItemFrame` for shared concerns:
  - row alignment.
  - sender label.
  - timestamp/read state.
  - reply preview.
  - reaction chips.
  - mention click routing.
  - action event emission.
- Keep interactive logic out of media-heavy renderers unless needed for that type.
- Preserve `v-memo` invariants documented in repo memory: use item object reference for memo keys, not only message id.

### Validation Gate

- Unit tests cover every renderer with its message type.
- Existing direct media, document, voice, location, reaction, mention, edit/delete, and selection tests remain green.
- Manual check: opening a long media chat should use fewer active computed/watchers than before.

### Rollback

- Keep the legacy `ChatMessageItem.vue` import path available until all renderer tests pass and the flag path is approved.

## Phase 5 - Conversation List Refactor

### Objective

Make the conversation list faster, calmer, and easier to scan.

### Work Items

- Replace heavy card/glass visuals with flatter rows and clearer hierarchy.
- Keep mandatory channel ordering, pinned ordering, unread/mention, mute, hide/unfollow, and long-press action semantics.
- Use `@tanstack/vue-virtual` if real conversation counts justify it; otherwise keep a simple list but remove expensive effects from row hot paths.
- Standardize avatar, status, preview, badge, and action-menu behavior.

### Validation Gate

- Conversation action Playwright suite passes.
- List render timing and DOM count are equal or better than baseline.
- Visual review confirms active/unread/muted/pinned states are clear.

### Rollback

- New list is behind the Messenger refactor flag and does not replace legacy list until accepted.

## Phase 6 - Virtualized Timeline

### Objective

Introduce real windowing for the message timeline while preserving chat-specific invariants.

### Work Items

- Prototype `@tanstack/vue-virtual` for variable-height rows.
- Preserve:
  - sticky date separators.
  - scroll-to-reply and scroll-to-search target.
  - around-message loading.
  - unread boundary and read marking.
  - album height stability.
  - media hydration near viewport.
  - selection-mode highlighting.
  - pinned-message jump/highlight.
- Use stable measured heights and avoid layout thrash.
- Keep a fallback non-virtual timeline path behind the same flag during rollout.

### Validation Gate

- Long chat scroll performance improves on weak mobile profile.
- Scroll position is stable when loading older messages.
- Search, reply jump, pinned jump, and album opening work after virtualization.
- Browser matrix passes for direct/group/channel core cases.

### Rollback

- Disable virtual timeline while keeping the split renderers if they are accepted independently.

## Phase 7 - Composer, Keyboard, And Attachment UX

### Objective

Make the input area feel stable, predictable, and native-like.

### Work Items

- Preserve the existing keyboard/sticker `env(keyboard-inset-height)` logic unless a measured replacement is proven better.
- Reduce composer layout jumps during keyboard, sticker, attachment, edit, reply, and selection transitions.
- Keep voice, attachment, emoji/sticker, edit prefill, reply preview, read-only channel banners, and selection action bar behavior.
- Keep attachment tabs purposeful and lightweight.
- Do not move heavy editor/cropper/map code into base composer boot.

### Validation Gate

- Vitest covers composer states.
- Playwright covers document send, album send, search mode, selection mode, and keyboard/picker smoke on supported browsers.
- Manual mobile check confirms no obvious jump when switching keyboard/sticker/attachment.

### Rollback

- Composer refactor remains inside the flagged Messenger path until approved.

## Phase 8 - Overlay And Action Surface Standardization

### Objective

Make context menus, forward modal, room managers, profile drawers, and lightbox feel like one product.

### Work Items

- Standardize overlay back-stack behavior.
- Standardize tap targets, iconography, danger actions, and close gestures.
- Use `radix-vue` only where it simplifies accessibility and focus management without bloating hot paths.
- Keep lightbox/media editor lazy-loaded.
- Keep context menu/reaction picker fast and avoid re-rendering the timeline behind it.

### Validation Gate

- Context menu, forward, room-manager, pinned-message, lightbox, and public-profile Playwright slices pass.
- Back button closes overlays in the correct order.
- No native browser long-press menu appears over message surfaces.

### Rollback

- Overlay components are replaceable independently behind the refactor shell.

## Phase 9 - Media, Cache, And Upload UX Hardening

### Objective

Preserve the advanced media pipeline while making progress states clearer and cheaper to render.

### Work Items

- Keep one authoritative EXIF-safe image path in `useChatMedia.ts`.
- Preserve edited-image passthrough for exact crop dimensions.
- Preserve background/resumable upload ownership and route-change survival.
- Keep album upload order, per-item progress, cancellation, and retry semantics.
- Keep document/media cache and share/open/download behavior.
- Avoid eager hydration of all media; hydrate near viewport.
- Improve visual clarity of upload/download progress rings without hiding media layout.

### Validation Gate

- Existing upload/background/resume tests pass across Chromium, Firefox, and WebKit where supported.
- Large album send does not block unrelated chat text sends.
- Cached media opens without refetch after returning to Messenger.

### Rollback

- Media pipeline changes should be separated from timeline/design changes so they can be reverted independently.

## Phase 10 - Realtime And Notification Runtime Review

### Objective

Keep realtime behavior correct and reduce unnecessary reloads or fanout costs.

### Work Items

- Preserve direct/group/channel `chat:message`, `chat:read`, `chat:reaction`, typing, upload activity, and notification contracts.
- Confirm muted conversations suppress browser/toast notifications but keep unread state.
- Keep relation-aware sender labels for accountant/customer contexts.
- Review Redis pubsub-per-WebSocket scale for production; document whether current approach is enough for expected concurrent users.
- Avoid client-side full conversation reloads on common realtime events.

### Validation Gate

- Notification, unread badge, muted-room, mention, relation-aware label, and deep-link tests pass.
- Backend realtime smoke tests pass if server code changes.

### Rollback

- Realtime changes must be additive or isolated behind event handler adapters.

## Phase 11 - Backend And Server Production Readiness

### Objective

Separate true server bottlenecks from frontend jank and prepare production deployment.

### Work Items

- Run `EXPLAIN ANALYZE` on conversation, room list, unread/mention, and message-history queries with production-like row counts.
- Add missing indexes only when query plans prove the need.
- Review `chat_members` and `messages.mentions` query paths, especially JSON mention checks.
- Keep upload CPU work off the event loop.
- Consider moving `tileserver` off the app host or disabling it when map is not in use.
- Tune uvicorn worker count and DB pool size for the production server, not the current development VPS.
- Consider Nginx/static media optimization for `chat_files` downloads.

### Validation Gate

- Backend p95/p99 targets are recorded under load.
- No frontend visual rollout waits on speculative server changes unless metrics prove server is the bottleneck.

### Rollback

- Backend performance changes are separate commits and use additive indexes/config changes only.

## Phase 12 - Release, Acceptance, And Legacy Retirement

### Objective

Only switch users to the new Messenger after measured improvement and product approval.

### Work Items

- Run full focused Messenger unit suite.
- Run full Messenger Playwright browser matrix.
- Run frontend production build.
- Run backend chat/router tests if backend changed.
- Run manual acceptance on mobile:
  - open Messenger.
  - switch chats.
  - scroll long chat.
  - send text.
  - send image/video/document/voice.
  - open lightbox.
  - reply/forward/delete/edit.
  - group/channel flows.
  - notification deep-link.
- Keep legacy available for at least one accepted release window.
- Remove legacy only after explicit approval.

### Validation Gate

- New Messenger is measurably faster than baseline.
- User acceptance is explicit.
- Rollback switch has been tested.

### Rollback

- Flip runtime flag to legacy.
- If needed, revert only Messenger refactor commits.
- No unrelated project files should need rollback.

## Suggested Validation Commands

Use focused commands per phase. Examples:

```bash
cd frontend
npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/chat/ChatLightbox.test.ts
npm run test:e2e -- e2e/messenger-direct-room-ux.spec.ts --reporter=line
npx playwright test e2e/channel-media.spec.ts --project=chromium --workers=1 --reporter=line
npx playwright test e2e/notifications.spec.ts --project=webkit --workers=1 --reporter=line
npm run build
```

If backend chat behavior changes:

```bash
python -m unittest tests.test_chat_router_direct_reads tests.test_chat_router_direct_mutations tests.test_chat_router_rooms tests.test_chat_router_media
python -m unittest tests.test_chat_room_service_room_read_model tests.test_chat_service_query_builders
```

For broad release confidence:

```bash
make test-gate
cd frontend && npm run test:e2e:matrix
```

## Recommended Implementation Order

1. Phase 0: baseline metrics.
2. Phase 1: reversible feature-flag shell.
3. Phase 2: visual tokens and motion budget.
4. Phase 3: state/controller boundaries.
5. Phase 4: message renderer split.
6. Phase 5: conversation list cleanup.
7. Phase 6: virtualized timeline.
8. Phase 7: composer/keyboard/attachment polish.
9. Phase 8: overlays and action surfaces.
10. Phase 9: media/cache/upload UX hardening.
11. Phase 10: realtime/notification review.
12. Phase 11: server production readiness.
13. Phase 12: acceptance, rollout, and legacy retirement.

## Definition Of Done

The Messenger refactor is complete only when all of these are true:

- The new Messenger is behind a tested rollback switch until approved.
- Baseline and final performance numbers show improvement.
- Long-chat scrolling is smoother on weak mobile hardware.
- Initial Messenger boot does not pull unnecessary heavy chunks.
- Existing direct/group/channel/media/customer/accountant/notification features are preserved.
- Browser matrix passes for the focused Messenger suite.
- The user approves the new UX direction before legacy code is removed.