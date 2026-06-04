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
| B | Store Foundation, Diagnostics Gate, Local Hydration | Planned | Establish store/cache/gateway foundations early |
| C | Container Extraction And Room Tear-down | Planned | Shrink `ChatView.vue` safely and prevent leaks |
| D | Message Renderer Split And Error Boundaries | Planned | Make message rendering modular and fault-tolerant |
| E | Media Dimension Contract And Virtualized Timeline | Planned | Introduce safe real virtualization without scroll jumps |
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

- Conversation list can paint from local cache before API completes.
- Diagnostics-heavy work is disabled by default.
- Initial store adoption does not change visible UX.
- No feature behavior moves exclusively to the new stores until tests prove parity.

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

- `ChatView.vue` line count reduced materially.
- Room switching does not leak timers/object URLs/hydration jobs.
- Background upload survives room switch.
- Existing browser-back behavior still works.

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

- `ChatMessageItem.vue` becomes an adapter or drops below target size.
- One malformed message cannot blank the timeline.
- Visual parity screenshots remain acceptable.

### Rollback

Switch renderer resolver back to legacy `ChatMessageItem`.

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

- No scroll jump when images/videos/albums hydrate.
- DOM node count drops significantly in heavy rooms.
- S10 weak-device list/chat responsiveness improves.
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

Start Stage B. The naming baseline is complete, so the next slice is the store/service foundation, diagnostics gate, local hydration, cache reconciliation, and websocket gateway skeleton.
