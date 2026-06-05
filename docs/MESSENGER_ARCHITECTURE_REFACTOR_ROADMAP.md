# Messenger Architecture Refactor Roadmap

> Date: 2026-06-04  
> Status: Planned. This roadmap starts after the Stage 12 release closure and is the next execution contract for making Messenger faster, cleaner, more stable, and visually more polished without losing any existing feature.  
> Scope: Frontend Messenger architecture, UI/UX consistency, local cache strategy, realtime/store ownership, media rendering stability, virtualization, and measured release gates.

## Purpose

The current Messenger is measurably better than the pre-refactor version, but it is still not a production-grade architecture for a fast, large-scale chat surface. The core issue is no longer one isolated slow path; it is the shape of the Messenger runtime:

- `ChatView.vue` is still a glue-layer monolith.
- The chat composables are tightly coupled to `ChatView.vue` internals.
- Critical Messenger state lives in local component refs instead of domain stores.
- Several helper files are named by historical refactor stage rather than business domain.
- Message rendering is not truly virtualized.
- Media height reservation, websocket ordering, room cleanup, local hydration, and render error isolation need first-class architecture.

This roadmap defines a staged in-place migration. It explicitly avoids a big-bang rewrite. The existing Messenger must remain functional at every stage.

## Non-Negotiable Product Rules

1. No existing Messenger feature may regress.
2. Direct chats, groups, channels, mandatory channels, customer/accountant rules, media, albums, voice, documents, location, reactions, mentions, forwarding, pinned messages, search, notifications, background upload, cache, and public-profile routing must continue to work.
3. Each stage must be reversible.
4. Each stage must keep the production route usable.
5. Broad benchmark runs happen at checkpoint stages, not after every small rename.
6. Focused unit/component/e2e tests are required before moving forward.
7. UI polish must not hide behavioral regressions.
8. Feature parity is more important than deleting code quickly.

## Target Architecture

The target shape is domain-driven and store-backed:

| Layer | Responsibility |
| --- | --- |
| `MessengerView` | Auth bootstrap, route shell, account/session guard only |
| `ChatShell` | Messenger layout, mobile/desktop shell, top-level back handling |
| `ConversationListContainer` | List store binding, list UI, list actions |
| `ChatRoomContainer` | Active room binding, header/timeline/composer assembly |
| `ChatTimeline` | Virtualized message timeline, scroll anchors, read markers |
| `ChatComposer` | Text, reply/edit, attachments, voice, read-only state |
| `RoomManagers` | Group/channel/new-conversation/public-profile flows |
| `stores/chat/*` | Domain state and mutations |
| `services/chat/*` | API, IndexedDB/local cache, websocket gateway, upload/download runtime |
| `components/chat/messages/*` | Specialized message renderers |

Stores must be small and domain-specific. A single huge `useChatStore` would recreate the current monolith in another place.

## Store And Service Boundaries

### Stores

| Store | Owns | Must not own |
| --- | --- | --- |
| `useChatSessionStore` | active room key, room kind, selected title/avatar/status, typing/activity state, route restore state | raw API calls |
| `useConversationsStore` | conversation list, ordering, unread/mute/pin state, list cache hydration status | message timeline DOM state |
| `useMessagesStore` | messages by room key, pagination cursors, read state, optimistic text state, message snapshots | upload binary work |
| `useChatTransferStore` | upload/download jobs, progress, cancellation, resumable ownership | message rendering |
| `useChatUiStore` | overlays, search mode, selection mode, context menu state, lightbox state | server persistence |

### Services

| Service | Responsibility |
| --- | --- |
| `chatApi.ts` | Typed API calls and endpoint selection for direct/group/channel |
| `chatCacheRepository.ts` | IndexedDB/local cache read/write, cache versioning, stale handling |
| `chatEventGateway.ts` | Normalize websocket events, enforce ordering, dispatch store actions |
| `chatRoomLifecycle.ts` | Room enter/leave teardown routines |
| `chatMediaMetadata.ts` | Media width/height/aspect-ratio extraction and fallback |
| `chatUploadRuntime.ts` | Upload queue ownership, resumable upload, commit, cancellation |
| `chatDownloadRuntime.ts` | Document/media download queue and cache handoff |

## Stage Status Tracker

| Stage | Name | Status | Primary Goal |
| --- | --- | --- | --- |
| A | Domain Naming Cleanup | Completed | Remove historical stage naming without behavior change |
| B | Store Foundation, Diagnostics Gate, Local Hydration | Completed | Establish store/cache/gateway foundations early |
| C | Container Extraction And Room Tear-down | Completed | Shrink `ChatView.vue` safely and prevent leaks |
| D | Message Renderer Split And Error Boundaries | Completed | Make message rendering modular and fault-tolerant |
| E | Media Dimension Contract And Virtualized Timeline | In Progress | Introduce safe real virtualization without scroll jumps |
| F | Realtime Gateway And Request Churn Reduction | Planned | Convert realtime/network updates into precise store mutations |
| G | UI System Final Pass | Planned | Polish the user-facing experience after architecture is stable |
| H | Full Release Gate And Legacy Retirement Decision | Planned | Prove feature parity, speed, stability, and UX acceptance |

## Stage A - Domain Naming Cleanup

### Goal

Remove stage-based code smell and make helper ownership understandable by domain.

### Changes

- Rename historical helper files to domain names:
  - controller helpers -> `chatTimelineController.ts`
  - conversation/timeline performance helpers -> `conversationListModel.ts`
  - composer/overlay helpers -> `composerOverlayState.ts`
  - media/realtime helpers -> `chatRealtimeMediaPolicy.ts`
  - context-menu helpers -> `messageContextMenuModel.ts`
  - rollout helpers -> `messengerRolloutPolicy.ts`
- Rename matching test files.
- Update imports only.
- Keep all public function behavior unchanged.
- Add a short compatibility note in the old roadmap explaining the rename.
- Run a repo-wide reference audit before closing the stage:
  - search the whole repository for historical stage-helper names.
  - update tracked benchmark scripts, e2e specs, tests, docs, and config references.
  - do not treat generated `tmp` artifacts as authoritative unless they are the active benchmark runner or are tracked.

### Files Expected

- `frontend/src/utils/*`
- `frontend/src/components/ChatView.vue`
- relevant tests under `frontend/src/utils/*.test.ts`
- tracked benchmark/e2e/scripts/docs references that mention historical stage-helper names
- docs only for roadmap sync

### Tests

- Focused utility tests for renamed files.
- `npm run test:unit:run -- src/components/ChatView.test.ts`
- No benchmark required unless behavior changes unexpectedly.

### Exit Criteria

- Completed on 2026-06-04.
- No active `frontend/src` import references a historical stage-named Messenger helper.
- Focused Stage A tests passed.
- `git diff` shows rename/import/documentation changes only.

### Rollback

Revert the rename commit.

## Stage B - Store Foundation, Diagnostics Gate, Local Hydration

### Goal

Create the store/service foundation before further extraction so later changes have stable ownership and measurable regression visibility.

### Changes

- Add domain stores:
  - `frontend/src/stores/chat/session.ts`
  - `frontend/src/stores/chat/conversations.ts`
  - `frontend/src/stores/chat/messages.ts`
  - `frontend/src/stores/chat/transfers.ts`
  - `frontend/src/stores/chat/ui.ts`
- Add service skeletons:
  - `frontend/src/services/chat/chatApi.ts`
  - `frontend/src/services/chat/chatCacheRepository.ts`
  - `frontend/src/services/chat/chatEventGateway.ts`
  - `frontend/src/services/chat/chatRoomLifecycle.ts`
- Move only low-risk state first:
  - active room key
  - selected room identity metadata
  - conversation list array
  - loading/error flags for conversation list
  - overlay/search/selection booleans as mirrored state, not yet the sole source of truth
- Add diagnostics gating:
  - normal production keeps only cheap marks.
  - expensive DOM snapshots/frame probes run only when `VITE_MESSENGER_DIAGNOSTICS=true` or benchmark mode enables them.
- Add local hydration contract:
  - `conversationsStore` reads last known conversations from IndexedDB/local cache first.
  - UI paints cached list immediately with stale marker internal state.
  - API sync runs in background and reconciles ordering/unread/mute/pin state.
  - Cache writes are versioned and bounded.
  - Cache keys include the current user and cache schema version so stale data cannot leak across users or incompatible app versions.
  - Reconciliation must merge local pending state instead of blindly overwriting it with the server response.
  - Pending local mutations are tracked in an outbox/mutation log until acknowledged or rejected:
    - optimistic text/media messages.
    - in-flight uploads and retries.
    - local mute/pin/archive/read changes.
    - room list ordering changes caused by optimistic sends.
  - Server state is authoritative only for acknowledged entities; pending local entities remain visible with explicit pending/failed status.
- Add `ChatEventGateway` skeleton:
  - raw websocket event enters one service.
  - service validates room key, event type, timestamp/id ordering.
  - service dispatches store actions.
  - no component receives raw websocket payloads directly in new paths.

### Files Expected

- new `frontend/src/stores/chat/*`
- new `frontend/src/services/chat/*`
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/composables/chat/useChatMessages.ts`
- `frontend/src/composables/chat/useChatWebSocket.ts`
- `frontend/src/utils/messengerDiagnosticsMetrics.ts`

### Tests

- Store unit tests:
  - cache hydration order
  - stale cache fallback
  - API reconciliation
  - pending local mutation merge during reconciliation
  - cache invalidation by user/schema version
  - event gateway normalization
- `npm run test:unit:run -- src/stores/chat`
- `npm run test:unit:run -- src/components/ChatView.test.ts src/views/MessengerView.test.ts`
- Focused e2e: open Messenger list and direct room on Chromium.

### Exit Criteria

- Completed on 2026-06-04.
- Conversation list can hydrate from local cache before the API result is reconciled.
- Cache keys are scoped by current user and schema version.
- Server reconciliation preserves pending local mute/pin mutations instead of blindly overwriting them.
- Diagnostics-heavy work remains scheduled/gated; Stage B did not add synchronous DOM probes.
- Initial store adoption mirrors legacy state and does not change visible UX ownership.
- `ChatEventGateway` normalizes websocket events into store actions while the legacy realtime convergence path remains active.
- Room lifecycle cleanup foundation exists for Stage C.
- Focused Store/Service/ChatView/WebSocket/Message tests and production build passed.

### Rollback

Disable store usage behind an adapter flag and return to local refs.

## Stage C - Container Extraction And Room Tear-down

### Goal

Shrink `ChatView.vue` by extracting containers and add a formal room cleanup routine to prevent long-session leaks.

### Changes

- Add containers:
  - `ChatShell.vue`
  - `ConversationListContainer.vue`
  - `ChatRoomContainer.vue`
  - `ChatHeaderContainer.vue`
  - `ChatComposerContainer.vue`
- Move list binding from `ChatView.vue` into `ConversationListContainer`.
- Move active room/header/composer binding into `ChatRoomContainer`.
- Add `chatRoomLifecycle.leaveRoom(previousRoomKey)` and call it when active room changes.
- Tear-down routine must:
  - revoke obsolete object URLs for the previous room when not needed by global cache.
  - cancel previous direct-user status polling.
  - cancel pending room-specific hydration jobs.
  - clear temporary lightbox buffers for the previous room.
  - clear pending reaction mutation versions for messages no longer in active room.
  - close room-scoped overlays and context menus.
  - keep background uploads alive because upload runtime owns them.
- Enforce one visible source of truth per extracted container:
  - during transition, a container may be legacy-prop-backed or Pinia-backed, but not both for the same visible state.
  - compatibility props/events stay only at the adapter boundary.
  - once a container reads a state slice from Pinia, parent visual props for that slice must be removed or ignored.
  - avoid parent-prop plus child-store synchronization because it can create split-brain reactivity and visible desync.

### Files Expected

- new container components under `frontend/src/components/chat/containers/`
- `frontend/src/components/ChatView.vue`
- `frontend/src/services/chat/chatRoomLifecycle.ts`
- `frontend/src/composables/chat/useChatMedia.ts`
- `frontend/src/composables/chat/useChatMessages.ts`

### Tests

- Unit tests for `chatRoomLifecycle`.
- Component tests for room switching:
  - direct -> direct
  - direct -> group
  - group -> channel
  - route restore
- Long-session e2e subset with repeated room switches.

### Exit Criteria

- Completed on 2026-06-04.
- `ChatView.vue` now delegates shell/list/room rendering through extracted containers.
- `ChatRoomContainer` owns room timeline/composer/room overlay rendering while legacy logic remains in `ChatView.vue`.
- `ConversationListContainer` owns global search/list rendering.
- Room switching now enters/leaves `chatRoomLifecycle`, closes room-scoped overlays, clears room UI/session runtime, and preserves background upload ownership.
- Existing browser-back and legacy source-of-truth behavior remain active during the transition.
- Focused ChatView/lifecycle/Messenger runtime tests and production build passed.

### Rollback

Containers can be inlined by reverting this stage; stores remain additive.

## Stage D - Message Renderer Split And Error Boundaries

### Goal

Replace the oversized `ChatMessageItem.vue` runtime with specialized message renderers and isolate bad payload crashes.

### Changes

- Add message renderer directory:
  - `MessageRenderBoundary.vue`
  - `BaseMessageFrame.vue`
  - `TextMessageBubble.vue`
  - `MediaMessageBubble.vue`
  - `AlbumMessageBubble.vue`
  - `VoiceMessageBubble.vue`
  - `DocumentMessageBubble.vue`
  - `LocationMessageBubble.vue`
  - `StickerMessageBubble.vue`
  - `ForwardedHeader.vue`
  - `ReplyPreview.vue`
  - `MessageStatusMeta.vue`
- Keep the existing visual output initially.
- Move one message family at a time.
- Wrap dynamic renderer resolution in `MessageRenderBoundary` using Vue `onErrorCaptured`.
- `MessageRenderBoundary` implementation contract:
  - keep local `hasError` state.
  - set `hasError=true` inside `onErrorCaptured`.
  - return `false` from `onErrorCaptured` so a bad message does not bubble into the full timeline.
  - reset local error state when the message id/type/render key changes.
  - emit diagnostics once per bad message payload.
- If one message payload is invalid, render a small fallback bubble:
  - Persian copy: `این پیام قابل نمایش نیست`
  - include message id in diagnostics only, not user-facing copy.
- Preserve:
  - selection state
  - swipe reply
  - context menu
  - reactions
  - forwarded metadata
  - mention clicks
  - reply scroll
  - album item actions

### Files Expected

- new `frontend/src/components/chat/messages/*`
- `frontend/src/components/chat/ChatMessageItem.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/ChatAlbumLayout.vue`

### Tests

- Renderer tests per message type.
- Error boundary tests with malformed JSON for media/location/document.
- Existing `ChatMessageItem.test.ts` retained during transition.
- Focused e2e for text/media/album/voice/document/location rendering.

### Exit Criteria

- Completed on 2026-06-04 as a safe first renderer split.
- `MessageRenderBoundary.vue` now wraps every rendered timeline item from `ChatRoomContainer.vue`; a malformed message renders `این پیام قابل نمایش نیست` and logs diagnostics once per message/render key instead of blanking the timeline.
- `ForwardedHeader.vue`, `ReplyPreview.vue`, and `TextMessageBubble.vue` are extracted with focused tests while preserving forwarded profile clicks, reply scroll, and text/mention click handling.
- `ChatMessageItem.vue` dropped from 2546 to 2488 lines. It is not yet adapter-only; media, album, voice, document, location, reactions, status meta, and swipe/context mechanics intentionally remain in the legacy adapter until Stage E/F can split them behind stronger virtualization/network tests.
- Visual parity risk was kept low by preserving the existing DOM class hooks used by the current tests.
- Validation passed:
  - `npm run test:unit:run -- src/components/chat/messages/MessageRenderBoundary.test.ts src/components/chat/messages/ForwardedHeader.test.ts src/components/chat/messages/ReplyPreview.test.ts src/components/chat/messages/TextMessageBubble.test.ts src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts`
  - `npm run test:unit:run -- src/components/chat/messages/MessageRenderBoundary.test.ts src/components/chat/messages/ForwardedHeader.test.ts src/components/chat/messages/ReplyPreview.test.ts src/components/chat/messages/TextMessageBubble.test.ts src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/components/chat/ChatAlbumLayout.test.ts src/components/chat/ChatInputBar.test.ts src/components/chat/ChatContextMenu.test.ts src/composables/chat/useChatMessages.test.ts src/composables/chat/useChatMedia.test.ts src/composables/chat/useChatWebSocket.test.ts`
  - `npm run build`

### Rollback

Remove the `MessageRenderBoundary` wrapper from `ChatRoomContainer.vue` and render `ChatMessageItem` directly; the extracted header/reply/text components can also be inlined back into `ChatMessageItem.vue` without data-contract changes.

## Stage E - Media Dimension Contract And Virtualized Timeline

### Goal

Introduce true virtualization safely. Media dimensions must be reserved before virtualization is enabled.

### Changes

- Add additive media metadata fields to the frontend message model:
  - `media_width`
  - `media_height`
  - `media_aspect_ratio`
- Add backend additive response fields if current persisted content does not already expose reliable dimensions.
- Backfill strategy:
  - new uploads must store dimensions at send/upload time.
  - old messages read dimensions from existing JSON content when available.
  - fallback to safe aspect ratios when unknown:
    - image/video: `4 / 3`
    - voice/document/location: fixed renderer height
    - album: derive from item count and known/fallback child ratios.
- `MediaMessageBubble` and `AlbumMessageBubble` must reserve layout space with CSS `aspect-ratio` before actual file hydration.
- Add `ChatVirtualTimeline.vue` using `@tanstack/vue-virtual`.
- Start with direct heavy rooms behind feature flag:
  - `VITE_MESSENGER_VIRTUAL_TIMELINE=true`
- Preserve:
  - sticky date separators or equivalent date affordance
  - scroll to reply
  - scroll to search result
  - pinned message jump
  - older-message prepend anchor
  - unread/mention jump
  - album highlighting
  - media lazy hydration
- Add measured fallback:
  - if virtual timeline fails a critical invariant in tests, default remains non-virtual.
- Add scroll adjustment for variable-height rows:
  - keep a measured row-height cache keyed by stable message id/render key.
  - use conservative estimated sizes for unmeasured text, reply, caption, album, and media rows.
  - implement two-phase jump behavior for search/reply/pinned/unread jumps: first scroll to the estimate, then correct after rows are rendered and measured.
  - cap correction attempts and timeouts so a bad estimate cannot create an infinite adjustment loop.
  - preserve the user's anchor when older messages are prepended or media dimensions resolve.

### Files Expected

- frontend message types
- backend serializers only if needed for additive dimensions
- upload/media metadata extraction path
- `ChatTimeline.vue`
- `ChatVirtualTimeline.vue`
- message renderer components

### Tests

- Unit tests for media dimension extraction and fallback.
- Virtual timeline tests for:
  - variable-height text
  - variable-height reply/caption rows
  - image/video with known ratio
  - image/video with fallback ratio
  - album ratio reservation
  - prepend older messages without jump
  - scroll-to-message
  - search result jump
  - jump to a far historical mixed-content message without layout drift
- Benchmark subset:
  - S02 heavy direct
  - S03 media-heavy
  - S04 search/viewer
  - S10 weak-device

### Exit Criteria

- Started on 2026-06-04 with the first safe Stage E slice:
  - added additive frontend message fields: `media_width`, `media_height`, `media_aspect_ratio`.
  - added `chatMediaDimensions.ts` to normalize dimensions from additive fields first, then legacy JSON `width`/`height`, then bounded fallback ratio `4 / 3`.
  - wired single image/video bubbles to reserve `aspect-ratio` even when old messages lack dimensions.
  - wired album items to use normalized dimensions/aspect-ratio so album layout has stable fallback sizing before media hydration.
  - added async `ChatVirtualTimeline.vue` behind `VITE_MESSENGER_VIRTUAL_TIMELINE=true`, limited to direct rooms already marked as `timelineRenderBudget.virtualizationCandidate`.
  - kept the default production path on the existing non-virtual timeline.
  - code-split `ChatVirtualTimeline` so the disabled feature flag does not force the virtualizer into the main messenger path.
- Validation passed for this slice:
  - `npm run test:unit:run -- src/utils/chatMediaDimensions.test.ts src/components/chat/ChatMessageItem.test.ts src/components/chat/ChatAlbumLayout.test.ts src/components/ChatView.test.ts`
  - `npm run build`
- Continued on 2026-06-04 with virtual timeline hardening:
  - extracted `chatVirtualTimeline.ts` for stable row flattening, row-size estimation, measured-height fallback, and direct/album child message row lookup.
  - added focused tests for date/message row keys, known-media estimates, fallback-media estimates, measured row-height cache, and album-child jump lookup.
  - exposed `ChatVirtualTimeline.scrollToMessage(messageId)` and connected it to `ChatView` through `ChatRoomContainer`, so search/reply/pinned jumps can ask the virtual timeline to render offscreen rows before falling back to legacy DOM scrolling.
  - kept the virtual path behind `VITE_MESSENGER_VIRTUAL_TIMELINE=true`; default production scrolling still uses the existing DOM path.
- Validation passed for the hardening slice:
  - `npm run test:unit:run -- src/utils/chatMediaDimensions.test.ts src/utils/chatVirtualTimeline.test.ts src/components/chat/ChatMessageItem.test.ts src/components/chat/ChatAlbumLayout.test.ts src/components/ChatView.test.ts`
  - `npm run build`
- Continued on 2026-06-04 with the browser flag gate scaffold:
  - added `frontend/e2e/messenger-virtual-timeline.spec.ts`.
  - the spec seeds a heavy direct room, requires `VITE_MESSENGER_VIRTUAL_TIMELINE=true`, asserts `.virtual-timeline` is active, checks rendered bubble count remains below the full room size, and verifies in-chat search can highlight an offscreen virtual row.
  - `npm run test:unit:run -- src/utils/chatMediaDimensions.test.ts src/utils/chatVirtualTimeline.test.ts src/components/ChatView.test.ts` passed.
  - `npm run build` passed.
- Continued on 2026-06-04 with the browser flag gate closure:
  - installed/provided the local Playwright Chromium executable and reran the flagged browser gate.
  - fixed the flagged open path so `VITE_MESSENGER_VIRTUAL_TIMELINE=true` requests enough direct-room tail messages (`180`) for the virtualization candidate threshold instead of staying capped at the normal `16/48` open limits.
  - passed `timelineRenderBudget` into `ChatRoomContainer` state so the virtual timeline gate can observe the real grouped-message budget.
  - corrected the TanStack virtualizer options shape so `count` is passed as a concrete number from a computed options object instead of a nested computed ref.
  - hardened virtual `scrollToMessage(messageId)` with a short measure/adjust loop so search jumps can mount and highlight offscreen rows after variable-height estimates settle.
- Validation passed for the browser gate closure:
  - `npm run test:unit:run -- src/composables/chat/useChatMessages.test.ts src/utils/chatVirtualTimeline.test.ts src/utils/chatMediaDimensions.test.ts`
  - `VITE_MESSENGER_VIRTUAL_TIMELINE=true npm run test:e2e -- e2e/messenger-virtual-timeline.spec.ts --project=chromium --reporter=line`
- Continued on 2026-06-04 with virtual jump/prepend coverage:
  - added virtual timeline bridge methods for `scrollToBottom`, `scrollToUnreadOrBottom(currentUserId)`, and `preservePrependAnchor(messageId)`.
  - routed initial unread scrolling and scroll-button bottom behavior through the virtual bridge when the feature flag is active.
  - changed older-message prepend anchoring to capture the current viewport message instead of the first message in the loaded array, then preserve it through the virtualizer after older rows are inserted.
  - kept the virtual unread jump resilient to lifecycle timing by retrying briefly while `currentUserId` and the async virtual timeline ref become available.
  - extended the browser gate to cover reply-preview jumps, pinned-banner jumps, initial unread jumps, and older-message prepend anchoring.
- Validation passed for the virtual jump/prepend slice:
  - `npm run test:unit:run -- src/composables/chat/useChatMessages.test.ts src/utils/chatVirtualTimeline.test.ts src/utils/chatMediaDimensions.test.ts src/components/ChatView.test.ts`
  - `VITE_MESSENGER_VIRTUAL_TIMELINE=true npm run test:e2e -- e2e/messenger-virtual-timeline.spec.ts --project=chromium --reporter=line`
  - `npm run build`
- Stage E benchmark subset completed on 2026-06-04:
  - config: `tmp/messenger-benchmark/stage-e-virtual-s02-s03-s04-s10-config-20260604T234214Z.json`
  - results: `tmp/messenger-benchmark/stage-e-virtual-s02-s03-s04-s10-results-20260604T234214Z.json`
  - benchmark tool fix: `runContextMenuProbe` now prefers the real `.message-wrapper` boundary, uses real Playwright right-click first, and falls back to synthetic `contextmenu` dispatch when legacy rows do not open a menu within the short compatibility window.
  - weak-device tuning: virtual initial open limit was reduced from `180` to `128`, and virtualization candidacy now starts at `96` timeline items or `48` media items.
  - `current-virtual` completed S02/S03/S04/S10 without benchmark errors.
  - `current-virtual` kept heavy/search scroll stable in S02 and S04 (`~60 FPS`, `0` jank) and reduced rendered heavy-room DOM bubbles to `16`.
  - S10 improved versus the failed/oversized virtual run but still trails the non-virtual path (`36.2 FPS`, `3` jank, `3264.9 ms` chat first paint), so the production default remains non-virtual and `VITE_MESSENGER_VIRTUAL_TIMELINE=true` is not approved for broad rollout yet.
- No scroll jump when images/videos/albums hydrate.
- DOM node count drops significantly in heavy rooms.
- S10 weak-device list/chat responsiveness is measured and gated for Stage F/request-churn follow-up before rollout.
- Heavy-room scroll remains stable.

### Rollback

Disable virtual timeline flag and keep media dimension fields as harmless additive metadata.

## Stage F - Realtime Gateway And Request Churn Reduction

### Goal

Reduce reload-driven behavior and route all realtime updates through precise store mutations.

### Changes

- Complete `ChatEventGateway` adoption:
  - `chat:message` -> `messagesStore.appendOrReplaceMessage()`
  - `chat:read` -> `messagesStore.patchReadState()`
  - `chat:reaction` -> `messagesStore.patchReaction()`
  - `chat:typing` -> `sessionStore.setTyping()`
  - `chat:upload_activity` -> `sessionStore.setActivity()`
  - `conversation:update` -> `conversationsStore.patchConversation()`
  - notification events -> notification store + conversation store, not list reload by default.
- Add event ordering:
  - ignore stale updates by message id/timestamp/version.
  - keep optimistic temp messages until matching persisted message arrives.
- Replace repeated `loadConversations()` calls with targeted store patches where the event has enough data.
- Add short-lived profile/member capability cache for manager flows.
- Batch manager refreshes after group/channel membership changes.

### Files Expected

- `frontend/src/services/chat/chatEventGateway.ts`
- `frontend/src/stores/chat/*`
- `frontend/src/composables/chat/useChatWebSocket.ts`
- `frontend/src/composables/chat/useChatMessages.ts`
- manager components as needed

### Tests

- Gateway ordering tests.
- Realtime burst tests.
- Notification/muted room tests.
- Group/channel manager mutation tests.
- Benchmark subset:
  - S05
  - S06
  - S07
  - S08
  - S09
  - S11

### Exit Criteria

- Started on 2026-06-05 with the first safe Stage F slice:
  - added event-clock ordering to `ChatEventGateway` for `chat:message` so stale same-message payloads are rejected before they can overwrite newer store state.
  - added per-room conversation-preview ordering in `ChatEventGateway` so a delayed older realtime message cannot move the conversation preview backwards.
  - added reaction ordering in `ChatEventGateway` so stale reaction payloads do not overwrite newer reaction state.
  - mirrored the conversation-preview ordering guard into the current `useChatWebSocket` batched legacy ref path, preserving unread accumulation while keeping preview fields pinned to the newest realtime message.
- Validation passed for this slice:
  - `npm run test:unit:run -- src/services/chat/chatEventGateway.test.ts src/composables/chat/useChatWebSocket.test.ts`
- Remaining before Stage F can close:
  - replace more `loadConversations()` calls with targeted patches where event payloads are complete.
  - route notification-driven conversation changes through store patches instead of default list reloads.
  - add manager capability/member cache and batch group/channel manager refreshes.
  - run Stage F benchmark subset: S05, S06, S07, S08, S09, S11.
- Request count in S08/S09/S11 drops by at least 30% from the current benchmark.
- No stale realtime update overwrites newer store state.
- Realtime delivery remains correct across direct/group/channel.

### Rollback

Route websocket handlers back to existing reload-based convergence path.

## Stage G - UI System Final Pass

### Goal

Make Messenger feel like one polished product, not a mix of legacy surfaces.

### Changes

- Apply final UI system after state/render architecture is stable:
  - unified bubble sizing and density
  - unified media frame and album spacing
  - unified voice/document/location bubble layout
  - unified direct/group/channel header behavior
  - unified context menu and conversation action menu
  - unified manager modals for group/channel
  - unified attachment/camera/gallery feedback states
  - unified read-only composer state for channels/management rooms
- Remove inline styles from hot message renderers where practical.
- Remove redundant decorative effects that cost layout/paint without improving usability.
- Audit RTL, Persian copy, touch targets, reduced motion, and keyboard transitions.

### Files Expected

- message renderers
- `ChatInputBar.vue`
- `ChatHeader.vue`
- `ChatConversationList.vue`
- manager modals
- `messenger-design-tokens.css`

### Tests

- Focused Vitest for UI states.
- Screenshot or visual e2e checks for:
  - text
  - image
  - album
  - video
  - voice
  - document
  - location
  - direct/group/channel headers
  - composer disabled/read-only states
- Browser matrix on touched user flows.

### Exit Criteria

- Manual UX review confirms Messenger feels cohesive.
- No known bubble/media/voice/avatar/camera UI drift remains.
- Reduced-motion mode remains usable and faster.

### Rollback

Revert UI-only stage while keeping architecture stages.

## Stage H - Full Release Gate And Legacy Retirement Decision

### Goal

Prove that the new architecture is faster, safer, and more pleasant before removing legacy fallback.

### Required Commands

- Focused unit suites for stores/services/renderers.
- Messenger Playwright subsets on Chromium first.
- Full browser matrix after Chromium is green.
- Full Messenger benchmark.
- Production build.
- `make foreign`.

### Performance Targets

| Target | Required Result |
| --- | --- |
| `ChatView.vue` | below 600 lines or only a lightweight compatibility wrapper |
| `useChatMessages` | removed or no longer takes large reactive dependency lists |
| `ChatMessageItem.vue` | below 400 lines or adapter-only |
| Timeline | real virtualization enabled for heavy rooms |
| S10 weak-device list-ready | below 5000 ms |
| Context menu | below 140 ms in primary scenarios |
| S08/S09/S11 request count | at least 30% lower than current benchmark |
| DOM nodes in heavy room | materially lower than Stage 12 |
| Heap | no scenario worse than Stage 12 by more than 2 MB |
| Browser matrix | 0 failed, known skips only |

### Feature Parity Gate

The following must be explicitly verified before any legacy retirement decision:

- direct room open/send/edit/delete/read/reply/forward/reaction
- group send/member/admin/mention/forward/location flows
- channel admin/member/read-only/mandatory/optional flows
- new conversation target rules
- avatar upload/remove for profile/group/channel
- media send from gallery and camera
- album send/retry/forward/download
- voice/document/location send and display
- background upload survives route change/reload
- download cache and cancellation
- search, scroll-to-result, scroll-to-reply
- pinned messages
- muted room notifications
- public profile routing and accountant/customer identity labels
- block/customer access restrictions
- browser back behavior for overlays and selection mode

### Rollback

Keep legacy fallback until this stage passes and the user explicitly approves retirement.

## Execution Discipline

Every implementation prompt should follow this sequence:

1. Check clean/dirty worktree.
2. Read the files directly involved in the stage.
3. Apply the smallest useful slice.
4. Run focused tests.
5. Update this roadmap and `.github/copilot-instructions.md`.
6. Run build/deploy when the project rule requires it.
7. Commit and push.
8. Report exactly what changed, what passed, and what remains.

## Immediate Next Step

Continue Stage E. The media dimension contract, flagged virtual timeline prototype, browser jump gates, and older-message prepend anchoring are in place and passing. The next slice is the Stage E benchmark subset before deciding whether the virtual timeline flag is safe for broader rollout.
