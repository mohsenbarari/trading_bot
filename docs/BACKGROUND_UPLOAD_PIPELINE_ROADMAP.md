# Background Upload Pipeline Roadmap

## Verified Baseline

- Current browser-matrix proof is green for the real Messenger route-change scenario on `Chromium`, `Firefox`, and `WebKit`.
- Verified scenarios today:
  - direct chat: upload activity stays visible and a document send completes after the sender leaves Messenger for Market.
  - group chat: sender-named upload activity stays visible and an image+video album completes after the sender leaves Messenger for Market.
- These green checks prove the current module-level upload service survives `ChatView` unmount and normal in-app navigation.

## Current Hard Limit

- The current pipeline is still page-owned JavaScript plus monolithic `XMLHttpRequest` / `fetch` calls.
- That means true OS-level background continuation is not guaranteed across browsers when the app is suspended, the tab is frozen, or the PWA is backgrounded for long enough that JS execution stops.
- The current reliability contract is:
  - route changes inside the web app are supported.
  - foreground wake recovery is supported.
  - full real-background continuation is browser-dependent.
  - full real-background durability after suspension needs resumable uploads, not just retries.

## Target Product Contract

The new pipeline should guarantee the following for `image`, `video`, `voice`, `document`, and `album` in both `direct` and `group` rooms:

1. An upload never has to restart from byte zero after app suspension, reload, network loss, or process discard.
2. If the platform allows background execution, uploading continues while the app is not foregrounded.
3. If the platform suspends the app, the upload resumes automatically from the last confirmed chunk on next wake.
4. Albums commit atomically at the chat layer: either the intended batch is assembled and sent in order, or the batch remains resumable without partial chat corruption.
5. The sender and receiver see correct activity state and final message ordering in direct and group chats.
6. No duplicate chat messages are created by retries, reloads, or foreground wake recovery.

## Recommended Architecture

Use a hybrid design:

1. `Chunked resumable upload sessions` as the mandatory foundation.
2. `Service-worker-assisted background execution` as a best-effort accelerator on browsers that support it.
3. `Automatic foreground resume` as the universal fallback for browsers that suspend the page and do not continue background work.

This hybrid is required because service workers alone do not provide a uniform cross-browser guarantee for long background uploads, while chunked resumability does.

## Scope

### In scope

- Direct rooms
- Group rooms
- Image
- Video
- Voice
- Document
- Album batches containing any supported media mix except mixed document+media albums unless product explicitly wants that UX
- In-app route changes
- Browser tab backgrounding
- Installed PWA backgrounding on Android-class platforms
- Foreground resume after suspension
- Network loss and reconnect
- Page reload and tab restore

### Explicitly out of scope for the first milestone

- Channel posting flow
- Cross-server upload session handoff
- Full offline send while the network is absent for the entire session
- Browser-native Background Fetch dependence as the only mechanism

## Phase 1: Backend Resumable Upload Foundation

### New data model

Add additive models and migrations:

#### `upload_batches`

- `id` UUID
- `owner_user_id`
- `room_kind` enum: `direct`, `group`
- `target_id` integer
- `message_kind` enum: `single`, `album`
- `expected_items` integer
- `committed_items` integer
- `status` enum: `collecting`, `uploading`, `uploaded`, `committing`, `committed`, `failed`, `cancelled`, `expired`
- `caption_policy` enum: `none`, `first_item_only`
- `idempotency_key`
- `created_at`, `updated_at`, `expires_at`, `last_activity_at`
- `actor_user_id` for delegated/session audit compatibility

#### `upload_sessions`

- `id` UUID
- `batch_id` nullable FK to `upload_batches`
- `owner_user_id`
- `actor_user_id`
- `room_kind` enum: `direct`, `group`
- `target_id` integer
- `media_type` enum: `image`, `video`, `voice`, `document`
- `original_file_name`
- `mime_type`
- `total_bytes`
- `chunk_size`
- `received_bytes`
- `next_offset`
- `chunk_count`
- `sha256_full` nullable
- `sha256_chunks` nullable JSONB or omitted if not needed initially
- `status` enum: `created`, `uploading`, `uploaded`, `finalizing`, `ready`, `committed`, `failed`, `cancelled`, `expired`
- `temp_storage_path`
- `final_chat_file_id` nullable FK to `chat_files`
- `preview_metadata` JSONB:
  - width
  - height
  - duration_ms
  - thumbnail
  - caption
  - album_index
  - waveform optional for voice
- `resume_token`
- `retry_count`
- `last_error`
- `created_at`, `updated_at`, `expires_at`, `last_activity_at`

### Temp storage design

- Store partial bytes under `uploads/chat_sessions/{user_id}/{session_id}.part`.
- Keep final `chat_files` storage unchanged for the first release.
- On finalize, atomically move the merged temp file into the existing chat-file storage path and create the `ChatFile` row.
- Add cleanup worker for expired temp sessions and orphaned part files.

### New service seam

Create `core/services/chat_upload_session_service.py` responsible for:

- create batch/session
- validate room membership and permissions for direct/group targets
- append chunk with offset validation
- query resume state
- finalize file
- finalize batch commit
- publish activity start/stop
- cancel session/batch
- expire abandoned sessions

### Proposed API contract

#### `POST /api/chat/upload-batches`

Create a batch for a direct or group target.

Request body:

- `room_kind`
- `target_id`
- `message_kind`
- `expected_items`
- `caption_policy`
- `idempotency_key`

Response:

- `batch_id`
- `status`
- `expires_at`

#### `POST /api/chat/upload-sessions`

Create one upload session.

Request body:

- `batch_id` nullable
- `room_kind`
- `target_id`
- `media_type`
- `file_name`
- `mime_type`
- `total_bytes`
- `chunk_size`
- `preview_metadata`

Response:

- `session_id`
- `resume_token`
- `next_offset=0`
- `chunk_size`
- `expires_at`

#### `PATCH /api/chat/upload-sessions/{session_id}/chunk`

Append one chunk.

Headers or body fields:

- `resume_token`
- `offset`
- `is_last_chunk`

Body:

- raw chunk bytes or multipart chunk field

Response:

- `received_bytes`
- `next_offset`
- `status`

#### `GET /api/chat/upload-sessions/{session_id}`

Query resume state.

Response:

- `status`
- `next_offset`
- `received_bytes`
- `total_bytes`
- `preview_metadata`
- `final_chat_file_id` if finalized

#### `POST /api/chat/upload-sessions/{session_id}/finalize`

Validate the last byte, build `ChatFile`, and mark the session `ready`.

#### `POST /api/chat/upload-batches/{batch_id}/commit`

Commit one single-item send or one album send into chat.

Server behavior:

- validate all required sessions are `ready`
- build final message payloads
- write messages in stable order
- preserve album ordering
- attach caption only on the first album item when configured
- publish realtime events once messages are durable
- mark sessions `committed`
- mark batch `committed`

#### `POST /api/chat/upload-sessions/{session_id}/cancel`

Cancel one upload session.

#### `POST /api/chat/upload-batches/{batch_id}/cancel`

Cancel the full album or single-item batch.

## Phase 2: Frontend Queue Refactor

### Replace the current page-owned monolithic model

Refactor `frontend/src/services/chatUploadBackground.ts` into a session-backed queue manager:

- keep the module-level singleton ownership pattern
- replace one `XHR -> /upload-media -> /send` flow with:
  - create batch
  - create session(s)
  - append chunk(s)
  - finalize session(s)
  - commit batch

### New client-side state model

Persist the following in IndexedDB:

- batch manifest
- upload session ids
- room target
- direct/group kind
- local optimistic ids
- local file/blob handles
- chunk progress per item
- current offset
- preview metadata
- batch commit status
- retry schedule
- last known foreground owner: `page` or `service_worker`

### Message-type handling

#### Image

- Keep current preprocess pipeline for EXIF-safe preview generation.
- Upload the final edited/original blob in chunks.
- Preserve current thumbnail and dimension metadata.

#### Video

- Keep current preview/thumbnail extraction.
- Chunk upload the final video blob.
- Preserve duration and dimensions.

#### Voice

- Treat voice as a first-class resumable media type, not a special audio exception.
- Preserve duration and waveform metadata.
- Reuse the same session model as document/image/video.

#### Document

- Preserve original filename and MIME.
- No preview thumbnail requirement, but allow optional document thumbnail later.

#### Album

- One batch id per album selection.
- One session per album item.
- A batch only commits after every non-cancelled item reaches `ready`.
- If one item fails or pauses, the batch remains resumable without losing already uploaded sibling items.

### Direct and group target abstraction

- Continue using one normalized room target model:
  - direct: `room_kind='direct'`, `target_id=user_id`
  - group: `room_kind='group'`, `target_id=chat_id`
- Do not fork separate queue logic per room type.
- Keep routing helpers centralized through the existing room-key abstraction style.

## Phase 3: Service Worker Background Executor

### Guiding rule

The service worker should accelerate and extend background progress where the browser permits it, but resumability must not depend on service-worker background execution.

### Required changes

- Extend the generated SW setup beyond the current share-target-only fragment.
- Prefer a dedicated upload worker module, for example `frontend/public/upload-background-sw.js`, imported by the generated Workbox service worker.
- Keep share-target logic separate from upload ownership logic.

### Worker responsibilities

- receive a `start-upload-batch` message from the app
- claim resumable tasks from IndexedDB
- append chunks while the app is hidden if the browser permits execution
- periodically persist `next_offset`
- finalize sessions
- attempt batch commit
- publish progress snapshots back to open clients via `clients.matchAll()` + `postMessage`
- relinquish ownership cleanly when the page comes back and wants to continue the batch

### Ownership arbitration

Use an explicit lock record in IndexedDB:

- `owner = page:<clientId>` or `sw`
- `lease_expires_at`
- `heartbeat_at`

Rules:

- page owns uploads while foregrounded by default
- on `visibilitychange -> hidden`, eligible uploads may be transferred to SW ownership
- on app wake, page can reclaim expired or cooperative SW leases
- only one owner can append chunks for a given session at a time

### Browser support reality

#### Chromium family

- best candidate for real background assistance
- use service worker messaging plus `sync` / wake opportunities where available
- do not assume `Background Fetch` exists or stays enabled across all Chromium targets

#### Firefox

- rely mainly on resumable session recovery
- background continuation may be limited

#### WebKit / iOS

- rely mainly on resumable session recovery
- background continuation is likely short-lived or unavailable

### Expected semantics after Phase 3

- Chromium PWA/mobile: many uploads continue in the background for longer windows.
- Firefox/WebKit: uploads may pause when suspended, but they resume from the last chunk instead of restarting from zero.

## Phase 4: Activity, UX, and Commit Semantics

### Activity source of truth

Move upload activity truth from page-local booleans toward server/session state.

Recommended rule:

- if any active `upload_session` exists for `(sender, room)`, publish `chat:activity { activity: 'uploading_file', active: true }`
- when the last active session for that `(sender, room)` becomes `ready`, `committed`, `failed`, or `cancelled`, publish `active: false`

This avoids losing activity state just because the visible page changed owners.

### UI requirements

- direct room header: `در حال ارسال فایل...`
- group room header: `<sender> در حال ارسال فایل...`
- conversation list row: same generalized activity text rules as today
- optimistic bubbles must survive route changes, reload, and wake recovery
- album bubbles must show per-item state while the batch is not committed
- failed sessions must expose `retry`, `cancel`, and `resume` semantics without duplicating sent items

### Commit safety

- Never call `/chat/send` directly per item from the page after Phase 1.
- Use batch commit only.
- For single-item messages, a single-item batch still goes through the same commit path.
- This guarantees identical semantics for media, file, voice, and album flows.

## Phase 5: Detailed Simulation and Validation Plan

## Automation layers

### Layer A: browser automation already in repo

Use Playwright for:

- Messenger -> Market
- Messenger -> Dashboard
- Messenger -> Profile
- Messenger -> Notifications
- hard reload mid-upload
- close/reopen chat view in same app session
- network drop and reconnect
- direct and group flows

### Layer B: Chromium lifecycle automation

For Chromium only, add CDP-driven lifecycle tests:

- set page hidden
- set page frozen when available
- return to active state

Goal:

- prove resumable chunks continue or recover correctly after visibility/lifecycle transitions

### Layer C: installed PWA / Android device tests

Use a real Android device or emulator plus ADB:

1. start upload in installed PWA
2. press Home
3. open another app via `adb shell am start ...`
4. wait `15s`, `60s`, `180s`
5. toggle network off/on during the wait in selected cases
6. return to the PWA
7. verify:
   - no duplicate messages
   - upload resumed from last chunk
   - album ordering stayed intact
   - receiver saw correct final messages

### Layer D: iOS / Safari manual matrix

Because automation is weaker, keep a manual verification script for:

- Safari in browser tab
- installed iOS web app if available
- switch to another app
- lock screen short interval
- reopen app
- verify resume behavior from last chunk

## Exact scenario matrix

Each row must run for `direct` and `group`.

### Single image

- leave Messenger for Market at 5%, 50%, 95%
- background the app for 15s, 60s
- reload at 40%
- cut network at 30%, restore after 20s

### Single video

- same matrix as image
- plus one large-file scenario above 25MB

### Single voice

- start send, background immediately
- resume after 30s
- verify waveform/duration metadata preserved

### Single document

- leave Messenger immediately after send starts
- reload after first chunk
- verify filename/MIME survive resume

### Album of 2 items

- image + video direct
- image + video group
- background after item 1 chunk 3 and item 2 chunk 1
- verify commit happens only after both sessions are ready

### Album of 6 to 10 items

- mixed image/video set
- route changes between Messenger, Market, Dashboard while chunks are in progress
- background the app during the middle of the batch
- verify all items commit once and in order

### Group concurrency

- sender A uploads album while sender B sends text to the same group
- verify batch ordering remains correct and activity text names remain accurate

### Recovery after process death

- kill browser/PWA process during upload
- reopen app
- verify resume from last confirmed chunk rather than byte zero

## Expected results by platform

### Chromium desktop / Android PWA

- best-effort true background continuation may happen
- if not, resume from last chunk on wake

### Firefox desktop

- likely pause on suspension
- mandatory resume from last chunk on wake

### WebKit desktop / iOS

- likely pause on suspension
- mandatory resume from last chunk on wake

The acceptance criterion is not “every browser must actively upload while hidden”.
The acceptance criterion is:

1. no data loss
2. no duplicate messages
3. no byte-zero restart after confirmed chunk progress
4. correct commit semantics for direct/group and single/album

## Phase 6: Observability

Add explicit observability before rollout:

- `chat:upload_session` websocket event family:
  - `created`
  - `progress`
  - `paused`
  - `resumed`
  - `ready`
  - `committed`
  - `failed`
  - `cancelled`
- server counters:
  - resumed sessions
  - chunk retry count
  - foreground-resume recoveries
  - batch commit retries
  - expired abandoned sessions
- structured logs with `session_id`, `batch_id`, `room_kind`, `target_id`, `media_type`

## Phase 7: Rollout Order

Recommended rollout order:

1. backend resumable sessions behind feature flag
2. frontend single-item document path on direct chats
3. frontend image/video/voice single-item direct chats
4. group single-item flows
5. album batches direct
6. album batches group
7. service-worker ownership on Chromium-family browsers
8. browser/device soak tests
9. default-on rollout

## File-Level Starting Points In This Repo

### Backend

- `api/routers/chat.py`
- `api/routers/chat_schemas.py`
- `models/chat_file.py`
- new models for upload batch/session
- `core/services/chat_upload_session_service.py` (new)
- optional cleanup worker near current background services

### Frontend

- `frontend/src/services/chatUploadBackground.ts`
- `frontend/src/composables/chat/useChatMedia.ts`
- `frontend/src/composables/chat/useChatMessages.ts`
- `frontend/src/components/ChatView.vue`
- `frontend/src/utils/chatRoomRouting.ts`
- `frontend/public/share-target-sw.js`
- `frontend/vite.config.ts`

### Tests

- `frontend/e2e/messenger-direct-room-ux.spec.ts`
- `frontend/e2e/channel-media.spec.ts`
- new Chromium lifecycle spec for hidden/frozen recovery
- Android manual or scripted device-run checklist

## Final Recommendation

Do not choose between `chunked resumable upload` and `service-worker background upload` as if they are substitutes.

The correct architecture for this repo is:

- `chunked resumable upload` as the reliability base for every browser
- `service-worker background execution` as the enhancement layer for browsers that allow real hidden/background work

That is the only design that can cover media, file, voice, and album reliably across direct/group chats, in-app navigation, reloads, and real background app transitions without pretending the web platform gives equal background guarantees everywhere.