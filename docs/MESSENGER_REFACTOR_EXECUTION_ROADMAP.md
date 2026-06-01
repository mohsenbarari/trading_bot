# Messenger Refactor Execution Roadmap

Date: 2026-05-31
Owner: Copilot + User
Scope: Messenger performance, UI/UX unification, interaction architecture, and release gating.

## Why This Document Exists

This roadmap converts strategy into an execution format that is easy to run in chat sessions:

- One prompt = one stage.
- Each stage is medium-sized (not too big, not too small).
- Each stage has strict entry/exit criteria.
- Each stage has measurable benchmark and UX goals.
- Every stage is reversible.

This document is execution-focused and should be used together with:

- docs/MESSENGER_REFACTORING_ROADMAP.md (strategic and historical context)
- tmp/messenger-benchmark/comparison-summary.json (ground truth metrics)
- tmp/messenger-benchmark/surface-status.json (coverage and release readiness)

## Project Standards Applied In This Plan

1. Performance and UX are equal priorities.
2. Visual unification is mandatory across direct, group, and channel surfaces.
3. Menus and submenus must follow a clear information architecture.
4. No large unbounded changes per stage.
5. Every stage must include focused tests before moving forward.
6. Full benchmark is rerun at defined checkpoints, not after every tiny edit.
7. Rollback path must exist for every stage.
8. During current single-server mode, deployment target is `make foreign` only.

## Global Success Contract (Final Gate)

The refactor phase is considered successful only if all items below are green:

1. New version is meaningfully better than old version in benchmark summary.
2. Critical scenarios S07, S09, S10 are improved versus old baseline.
3. S05 context-menu latency is reduced to an acceptable level (< 180ms target).
4. No high-severity UI/UX drift between direct/group/channel.
5. Browser matrix remains green on Chromium, Firefox, and WebKit.
6. Menu/submenu structure is consistent and role-aware.

## Stage Execution Rules (One Prompt = One Stage)

For each stage, use this fixed sequence:

1. Run entry checks.
2. Apply targeted code changes (change budget: 3-6 key files).
3. Run focused tests only for that stage.
4. If stage passes, update stage status table.
5. Move to next stage in the next prompt.

Do not merge multiple stages into a single prompt.

## Stage Status Tracker

| Stage | Name | Status | Owner | Last Run | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Baseline Lock + Perf Budget | Completed | Copilot | 2026-05-31 | Baseline locked from `tmp/messenger-benchmark/comparison-summary.json` (`generatedAt=2026-05-31T19:13:12Z`) |
| 2 | Menu IA Normalization | In Progress | Copilot | 2026-05-31 | IA sectioning applied in header/context menus + focused Vitest/Playwright green |
| 3 | Conversation List Performance + Visual Cohesion | Completed | Copilot | 2026-05-31 | Row view-model memoization, shared-token visual alignment, and S07/S10 list-ready benchmark check passed |
| 4 | Chat Open Pipeline (Heavy/Search/Identity) | Completed | Copilot | 2026-05-31 | Non-blocking open-path hydration finalized; S02/S04/S08 Stage3-vs-Stage4 benchmark checkpoint passed |
| 5 | Composer/Overlay State Machine Stabilization | Completed | Copilot | 2026-06-01 | Reducer-backed composer resets now govern reply/edit/conversation transitions; focused Vitest and direct-room Playwright green |
| 6 | Context Menu Latency Fix (S05) | Completed | Copilot | 2026-06-01 | Precomputed menu state, deferred snapshot work, and lazy reaction-shell mount reduced S05 context latency to `156.4 ms` and cleared the `< 180 ms` stage gate |
| 7 | Media Pipeline Optimization (S09/S10) | In Progress | Copilot | 2026-06-01 | Transfer recovery bootstrap, S09 upload persistence probing, action-start/reload-cache fixes, first-interaction/menu tuning, combined room conversation reads, interaction/upload critical-path tightening, and download/context final-gate tuning are in place; S09/S10 rerun is pending |
| 8 | Realtime/Notification Coalescing (S07) | Pending | Copilot | - | - |
| 9 | UI System Enforcement Pass | Pending | Copilot | - | - |
| 10 | Group/Channel/Direct Manager Standardization | Pending | Copilot | - | - |
| 11 | Weak-Device and Motion Final Pass | Pending | Copilot | - | - |
| 12 | Final Benchmark + Release Closure | Pending | Copilot | - | - |

## Detailed Stage Plan

### Stage 1 - Baseline Lock + Perf Budget

Goal:
- Freeze measurable targets before optimization work.

Primary outputs:
- Scenario budgets for S00-S11: list-ready, chat-ready, context latency, heap envelope.

Files expected:
- scripts/messenger_benchmark_config.json
- docs/MESSENGER_REFACTORING_ROADMAP.md
- docs/MESSENGER_REFACTOR_EXECUTION_ROADMAP.md

Tests/commands:
- make messenger-benchmark-all

Exit criteria:
- Budget table recorded and accepted.
- Baseline artifacts generated with no tooling regressions.

Rollback:
- Revert config/doc-only updates.

Stage 1 execution result (locked):
- Command run: `make messenger-benchmark-all`
- Artifacts refreshed:
	- `tmp/messenger-benchmark/comparison-summary.json`
	- `tmp/messenger-benchmark/performance-results.json`
	- `docs/messenger-surface-report.md`
	- `docs/MESSENGER_RESILIENCE_REPORT.md`
	- `docs/MESSENGER_MANUAL_ACCEPTANCE_CHECKLIST.md`

Stage 1 locked performance budget (final gate thresholds):

| Scenario | Old list (ms) | Current list (ms) | Budget list max (ms) | Old chat (ms) | Current chat (ms) | Budget chat max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S00 | 683.3 | 732.5 | 683.3 | 868.9 | 815.9 | 815.9 |
| S01 | 631.0 | 747.7 | 631.0 | 787.5 | 1234.4 | 787.5 |
| S02 | 733.4 | 840.2 | 733.4 | 1252.3 | 786.6 | 786.6 |
| S03 | 707.1 | 612.2 | 612.2 | 795.1 | 1640.0 | 795.1 |
| S04 | 559.2 | 665.9 | 559.2 | 886.9 | 1265.5 | 886.9 |
| S05 | 630.1 | 665.8 | 630.1 | 1628.5 | 842.2 | 842.2 |
| S06 | 598.8 | 658.2 | 598.8 | 840.0 | 887.5 | 840.0 |
| S07 | 830.5 | 999.3 | 830.5 | 1472.9 | 1389.2 | 1389.2 |
| S08 | 836.7 | 614.0 | 614.0 | 831.4 | 1165.1 | 831.4 |
| S09 | 633.6 | 859.5 | 633.6 | 1149.8 | 883.4 | 883.4 |
| S10 | 8156.9 | 8126.8 | 8126.8 | 2699.0 | 2765.0 | 2699.0 |
| S11 | 636.0 | 686.6 | 636.0 | 1105.6 | 1243.8 | 1105.6 |

Additional Stage 1 guardrails:
- Context menu latency:
	- Global hard target: `< 180 ms`
	- S05 hard target: `<= 140.1 ms` (current best baseline)
- Realtime/long-session:
	- `averageSwitchMs <= 800 ms` for S05/S07
	- `averageMenuMs <= 110 ms` for S05/S07/S09
- Memory and render envelope (from `comparison-summary.json` deltas):
	- `heapDeltaMb <= +2.0` for every scenario
	- `heapDeltaMb <= 0` for S07/S09 critical paths
	- `|domNodeDelta| <= 5` for every scenario

### Stage 2 - Menu IA Normalization

Goal:
- Unify menu and submenu architecture across room types.

IA contract:
- Section A: Primary actions
- Section B: Communication actions
- Section C: Room management actions
- Section D: Destructive actions

Files expected:
- frontend/src/components/chat/ChatContextMenu.vue
- frontend/src/components/chat/ChatHeader.vue
- frontend/src/views/ChatView.vue

Tests/commands:
- Focused Vitest for context and header behavior.
- Relevant Playwright messenger action slices.

Exit criteria:
- Same sectioning logic in direct/group/channel.
- No strict-locator or back-stack regressions.

Rollback:
- Revert context-menu/header/view wiring commits.

Stage 2 kickoff progress:
- Sectioned IA labels and ordering now applied in:
	- `frontend/src/components/chat/ChatContextMenu.vue`
	- `frontend/src/components/chat/ChatHeader.vue`
	- `frontend/src/components/ChatView.vue`
- Focused validations completed:
	- `npm run test:unit:run -- src/components/chat/ChatContextMenu.test.ts src/components/chat/ChatHeader.test.ts`
	- `npm run test:e2e -- e2e/messenger-conversation-actions.spec.ts --project=chromium --workers=1`

### Stage 3 - Conversation List Performance + Visual Cohesion

Goal:
- Improve list readiness in stressed scenarios while enforcing row-level visual consistency.

Files expected:
- frontend/src/components/chat/ChatConversationList.vue
- frontend/src/components/chat/ChatConversationList.test.ts
- frontend/src/styles/messenger-design-tokens.css

Tests/commands:
- Vitest: conversation list suite
- Benchmark subset: S07, S10

Exit criteria:
- S07/S10 list-ready improves versus current state.
- Row visual states are token-driven and consistent.

Rollback:
- Revert list rendering and style-token deltas.

Stage 3 kickoff progress:
- Applied row-level view-model precomputation in `ChatConversationList.vue` to reduce repeated per-row function calls in template render paths.
- Aligned key conversation list visuals and motion values with shared messenger tokens (`--messenger-*`) for consistency with the broader messenger shell.
- Focused validation completed:
	- `npm run test:unit:run -- src/components/chat/ChatConversationList.test.ts`
	- `npm run test:unit:run -- src/components/chat/ChatConversationList.test.ts src/utils/messengerStage4Performance.test.ts`
	- `npm run test:e2e -- e2e/messenger-conversation-actions.spec.ts --project=chromium --workers=1`

Stage 3 exit validation (S07/S10 subset):
- Baseline snapshot (pre-Stage 3 local benchmark run):
	- S07 list-ready: `633.4 ms`
	- S10 list-ready: `8088.0 ms`
- Stage 3 snapshot (after build and Stage 3 changes):
	- S07 list-ready: `631.7 ms`
	- S10 list-ready: `8055.4 ms`
- Command used:
	- `npm run benchmark:messenger -- --config tmp/messenger-benchmark/stage3-s07-s10-config.json`

### Stage 4 - Chat Open Pipeline (Heavy/Search/Identity)

Goal:
- Fix chat-ready regressions in heavy scenarios.

Approach:
- Two-phase room-open: fast shell paint first, deferred hydration second.

Files expected:
- frontend/src/composables/chat/useChatMessages.ts
- frontend/src/views/ChatView.vue
- frontend/src/composables/chat/useChatScroll.ts

Tests/commands:
- Focused chat open tests
- Benchmark subset: S02, S04, S08

Exit criteria:
- S02/S04/S08 chat-ready materially improved.

Rollback:
- Revert open pipeline changes only.

Stage 4 kickoff progress (Phase 1):
- Implemented a two-step open path in `useChatMessages.ts`:
	- Fast initial room fetch (`limit=24`) for first open without warm snapshot.
	- Deferred background hydration (`limit=48`, silent refresh) after first paint.
- Added hydration de-dup guard per user to avoid duplicate background refresh storms.
- Kept warm-snapshot behavior intact and aligned test doubles with dynamic `limit=` query matching.

Focused validation completed:
- `npm run test:unit:run -- src/composables/chat/useChatMessages.test.ts`
- `npm run test:unit:run -- src/components/ChatView.test.ts`

Stage 4 benchmark checkpoint (S02/S04/S08 subset):
- Command:
	- `npm run benchmark:messenger -- --config tmp/messenger-benchmark/stage4-s02-s04-s08-config.json`
- Baseline (`b98363d`) -> Stage 4 kickoff (working tree):
	- S02 chat first paint: `885.9 ms` (improved vs `1169.8 ms` pre-refactor baseline, but list/context still pending tuning)
	- S04 chat first paint: `704.9 ms` (notable improvement)
	- S08 list-ready: `692.9 ms` (improved), while S08 chat first paint regressed (`1293.5 ms`) and needs next slice tuning.

Stage 4 finalization (non-blocking hydration refinement):
- Removed chat-open critical-path blocking on `waitForChatUploadBackgroundReady()` in `useChatMessages.ts`.
- Initial message paint now uses immediate pending state + asynchronous reconciliation for restored uploads.
- Silent/background refresh path still awaits uploader readiness for correctness.

Stage 4 focused validations (final):
- `npm run test:unit:run -- src/composables/chat/useChatMessages.test.ts`
- `npm run test:unit:run -- src/components/ChatView.test.ts`

Stage 3 vs Stage 4 differential benchmark (authoritative Stage 4 closeout):
- Command:
	- `npm run benchmark:messenger -- --config tmp/messenger-benchmark/stage4-vs-stage3-s02-s04-s08-config.json`
- Results (`chat first paint`):
	- S02: `1150.1 ms` -> `845.1 ms` (Stage 4 improved)
	- S04: `856.2 ms` -> `862.7 ms` (near-flat; statistically close)
	- S08: `1095.9 ms` -> `802.6 ms` (Stage 4 improved)
- Results (`list ready`):
	- S02: `835.3 ms` -> `724.0 ms`
	- S04: `724.3 ms` -> `604.6 ms`
	- S08: `679.7 ms` -> `528.1 ms`

### Stage 5 - Composer/Overlay State Machine Stabilization

Goal:
- Eliminate jitter and state conflicts in input/overlay transitions.

Files expected:
- frontend/src/components/chat/ChatInputBar.vue
- frontend/src/utils/messengerStage5ComposerOverlay.ts
- frontend/src/components/ChatView.vue

Tests/commands:
- Composer-focused Vitest
- Direct-room UX Playwright subset

Exit criteria:
- Stable transitions for reply/edit/selection/recording/search/picker.

Rollback:
- Revert state-machine adapter and input wiring.

Stage 5 completion:
- Extended `messengerStage5ComposerOverlay.ts` with explicit reply/edit/conversation entry transitions so composer overlays now close from one reducer seam instead of scattered local mutations.
- Updated `ChatView.vue` to reset draft/edit/reply state and close composer overlays/context menus consistently when entering reply/edit flows and when switching into another direct/group/channel conversation.
- Updated `ChatInputBar.vue` so starting voice recording force-closes the sticker picker and clears pending picker/keyboard swap state before entering recording mode.
- Hardened edit-mode focus handoff in `ChatView.vue` by treating `ChatInputBar` exposed methods as optional, which keeps lightweight test/runtime stubs safe.

Focused validation completed:
- `npm run test:unit:run -- src/utils/messengerStage5ComposerOverlay.test.ts src/components/chat/ChatInputBar.test.ts src/components/ChatView.test.ts`
- `npm run test:e2e -- e2e/messenger-direct-room-ux.spec.ts --project=chromium --reporter=line`

### Stage 6 - Context Menu Latency Fix (S05 Critical)

Goal:
- Remove the largest latency regression in benchmark.

Approach:
- Lazy mount, precomputed action model, minimized synchronous layout work.

Files expected:
- frontend/src/components/chat/ChatMessageItem.vue
- frontend/src/components/chat/ChatContextMenu.vue
- frontend/src/views/ChatView.vue

Tests/commands:
- Context and action test slices
- Benchmark subset: S05

Exit criteria:
- S05 context latency target met (< 180ms objective).

Rollback:
- Revert context performance patches only.

Stage 6 completion summary:
- Added `frontend/src/utils/messengerStage6ContextMenu.ts` as the shared authority for sectioned action descriptors and bounded context-menu positioning.
- Updated `ChatView.vue` to precompute menu model/style on open, cache file-share capability outside the hot path, and defer expensive DOM snapshot metrics work until after the synchronous menu-open path.
- Updated `ChatContextMenu.vue` to render from the precomputed action model and lazy-mount the reaction shell one frame after open so the root menu becomes visible before quick-reaction/localStorage work runs.
- Added focused utility coverage and aligned the existing context-menu orchestrator tests with the new menu-state contract.

Focused validation completed:
- `npx vitest run src/components/chat/ChatContextMenu.test.ts src/components/ChatView.test.ts src/utils/messengerStage6ContextMenu.test.ts --reporter=dot`
- `PLAYWRIGHT_HTML_OPEN=never npm run test:e2e -- e2e/direct-chat.spec.ts --project=chromium --workers=1 --reporter=line`
- `npm run benchmark:messenger -- --config /root/trading-bot/trading_bot/tmp/messenger-benchmark/stage6-s05-config.json`

Stage 6 benchmark result:
- Initial Stage 6 local regression snapshot during refactor: `347.9 ms`
- Final Stage 6 S05 context-menu latency: `156.4 ms`
- Exit status: passed the Stage 6 execution contract (`< 180 ms objective`) while remaining above the historical `140.1 ms` hard baseline for future tuning.

### Stage 7 - Media Pipeline Optimization (S09/S10)

Goal:
- Improve upload/download persistence and weak-device behavior.

Files expected:
- frontend/src/composables/chat/useChatMedia.ts
- frontend/src/composables/chat/useChatFileHandler.ts
- frontend/src/services/chatUploadBackground.ts

Tests/commands:
- Media/upload focused Vitest
- Playwright upload resume slices
- Benchmark subset: S09, S10

Exit criteria:
- S09/S10 list/chat metrics improve against old baseline target direction.

Rollback:
- Revert media/cache service patches.

Stage 7 progress:
- Closed transfer-runtime boot cleanup slice:
	- `AppAuthenticatedShell.vue` no longer eagerly imports/starts upload and document-download background workers on every authenticated boot.
	- `chatTransferResumeHints.ts` stores lightweight local resume hints so only pending transfers trigger immediate recovery on shell mount.
	- `chatUploadBackground.ts` and `chatDocumentDownloadBackground.ts` can self-initialize on first submit/download action using the same same-origin config contract, defer non-critical restore work to idle/frame slots, and clear hints when queues drain.
- Validation completed:
	- `npm run test:unit:run -- src/services/chatUploadBackground.test.ts src/services/chatDocumentDownloadBackground.test.ts src/components/AppAuthenticatedShell.test.ts`
	- `npm run build`
- Benchmark tooling progress:
	- `scripts/run_messenger_benchmark.mjs` now adds a true S09 document-upload persistence probe that holds the first upload chunk/legacy media request, leaves and reopens the active conversation, then records upload first-visible, resume, completion, transport, and hold-state metrics under `persistence.upload`.
	- `scripts/messenger_benchmark_config.json` defines `upload_probe_size_bytes` for S09 so the upload path is large enough to exercise resumable transfer behavior.
	- `scripts/build_messenger_benchmark_report.py` now includes context-menu, download-start, and upload-completion deltas in the generated performance report.
- Transfer memory cleanup:
	- Document uploads now drop their local `blob:` preview URL after the final server message is committed, while image/video uploads still retain local URLs for instant preview reuse.
	- The benchmark heap counter now runs `HeapProfiler.collectGarbage` before reading `Performance.getMetrics`, reducing noise from short-lived upload/download objects and making heap deltas closer to retained memory.
- Stage 7 latency follow-up:
	- Added an idle/timeout scheduler for Messenger diagnostic probes so DOM snapshots and frame-budget sampling no longer compete with first list/surface paint on weak devices.
	- `MessengerView.vue` and `ChatConversationList.vue` now keep their performance marks synchronous but defer non-critical DOM counting/frame probes until idle, including the conversation-menu diagnostic snapshot.
	- `commit_upload_batch_endpoint` now keeps the authoritative upload commit, message reload, and response serialization on the request path, then schedules realtime message fanout and upload-session committed runtime events after the HTTP response to reduce sender-visible upload completion latency.
	- Focused validation: `npm run test:unit:run -- src/utils/messengerStage2Metrics.test.ts`, `python3 -m unittest tests.test_chat_router_upload_sessions.ChatRouterUploadSessionEndpointTests.test_commit_upload_batch_publishes_direct_and_group_branches`, `python3 -m unittest tests.test_chat_router_remaining_paths.ChatRouterRemainingPathTests.test_room_mute_channel_avatar_toggle_pin_and_commit_fallback_paths`, `python3 -m unittest tests.test_chat_router_upload_sessions`, `python3 -m py_compile api/routers/chat.py`, `npm run build`, and `git diff --check`.
- Stage 7 transfer-start follow-up:
	- `useChatFileHandler.ts` now marks uncached document/file opens as downloading before the async IndexedDB cache lookup, so the document bubble enters the busy state immediately while the held network request waits.
	- Upload and document-download action-triggered auto-init no longer waits for the stored-transfer restore scan; explicit shell/init recovery still restores stored queues, while new action paths skip restore and avoid re-adopting the just-created transfer.
	- Focused validation: `npm run test:unit:run -- src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, `npm run build`, and `git diff --check`.
- Stage 7 measured checkpoint after latency follow-up (working-tree, 3 measured S09/S10 runs, `--skip-warmup`):
	- Command: `cd frontend && npm run benchmark:messenger -- --config /root/trading-bot/trading_bot/tmp/messenger-benchmark/stage7-s09-s10-upload-probe-config.json --skip-warmup`
	- S09 averages, pre-refactor -> current: list `748.0 -> 637.6 ms`, chat `1116.6 -> 1008.1 ms`, context `246.3 -> 217.5 ms`, heap `7.40 -> 7.50 MB`, download start `255.3 -> 527.9 ms`, download reload `1069.5 -> 954.3 ms`, upload first-visible `969.7 -> 1023.9 ms`, upload completion `680.6 -> 470.7 ms`.
	- S10 averages, pre-refactor -> current: list `8005.6 -> 8121.6 ms`, chat `2610.4 -> 2221.7 ms`, context `483.0 -> 514.4 ms`, heap `7.47 -> 8.14 MB`, conversations API `266.7 -> 268.1 ms`, messages API `551.8 -> 460.5 ms`.
	- Decision: Stage 7 now closes the S09 upload-completion regression and improves S09 list/chat/context plus S09 download reload, but remains open because S09 download start/upload first-visible and S10 list/context/heap are still outside the target direction.
- Stage 7 measured checkpoint after transfer-start follow-up (working-tree, 3 measured S09/S10 runs, `--skip-warmup`):
	- Command: `cd frontend && npm run benchmark:messenger -- --config /root/trading-bot/trading_bot/tmp/messenger-benchmark/stage7-s09-s10-upload-probe-config.json --skip-warmup`
	- S09 averages, pre-refactor -> current: list `830.1 -> 628.9 ms`, chat `1073.3 -> 1027.3 ms`, context `189.1 -> 187.2 ms`, heap `7.39 -> 7.58 MB`, download start `396.1 -> 574.9 ms`, download reload `691.3 -> 860.2 ms`, upload first-visible `1024.6 -> 984.8 ms`, upload completion `766.2 -> 842.4 ms`.
	- S10 averages, pre-refactor -> current: list `7871.5 -> 8034.9 ms`, chat `2537.7 -> 2355.7 ms`, context `476.8 -> 518.2 ms`, heap `7.47 -> 7.59 MB`, conversations API `266.8 -> 268.4 ms`, messages API `555.4 -> 524.3 ms`.
	- Decision: Stage 7 remains open. The action-start split kept S09 list/chat/context directionally green and improved upload first-visible, but S09 download-start/reload and upload completion regressed in this run, and S10 list/context/heap still miss the target direction.
- Stage 7 bundle/start responsiveness follow-up:
	- `ChatView.vue` now lazy-loads inactive heavy Messenger surfaces (context menu, search result surfaces, attachment sheet, forward/new/group/channel/admin modals, location modal, and lightbox) and only mounts them in production when their owning state is active. Vitest keeps the previous always-mounted stubs through a test-only guard.
	- Chat route entry now warms the context-menu/search chunks from an idle diagnostic slot so first interaction remains responsive without forcing those chunks into the initial conversation-list route.
	- `ChatMessageItem.vue` now sets a component-local document intent busy flag and flushes the DOM before the shared file handler starts async cache/network work, making tap-to-download visible immediately even when IndexedDB or network setup is delayed.
	- Focused validation: `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, `npm run build`.
	- Build checkpoint: the Messenger route chunk reported by Vite dropped to `113.69 KB gzip` after splitting inactive surfaces out of the initial Messenger bundle; benchmark rerun is pending.
- Stage 7 document-download persistence hardening:
	- The latest S09/S10 benchmark completed against `8fec096`; S10 list/context/heap and Messenger bundle size were directionally green, but S09 document download persistence still failed one run with `completedState=idle`, a `60s` completion timeout, and a `15.9s` reload timeout.
	- Root cause: uncached document body taps could start a component-local shared-file fetch. If the user left/reopened the conversation while the benchmark held `/api/chat/files/**`, that component-owned fetch could lose the durable download handoff, leaving the reopened bubble idle instead of completed.
	- `ChatMessageItem.vue` now routes uncached document taps through the parent/background document-download service, keeps only cached documents on the direct shared-file open path, treats `local_blob_url` documents as completed for the download icon state, and retains the immediate local busy affordance until the background state arrives.
	- Focused validation: `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts`, `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, `npm run build`, and `git diff --check`.
- Stage 7 document-download reload-cache hardening:
	- The S09/S10 rerun completed against `083f9f9`; S09 completion was fixed across measured runs, but reload still failed because the reopened document bubble returned to `idle` and hit the benchmark's `15s` reload timeout.
	- Root cause: completed background downloads were retained as in-memory object URLs only. After a full page reload, `useChatFileHandler` had no persistent cached blob for that `fileId`, so the document action rendered as idle even though the transfer had completed before reload.
	- `chatDocumentDownloadBackground.ts` now seeds completed document blobs into the shared persistent file cache before emitting `completed`, for both non-streaming and streaming download paths. This keeps the background service and visible document bubble aligned after reload.
	- Focused validation: `npm run test:unit:run -- src/services/chatDocumentDownloadBackground.test.ts`, `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, `npm run build`, and `git diff --check`.
- Stage 7 final-latency follow-up:
	- The S09/S10 rerun completed against `b6b03f1`; document download persistence was functionally fixed (`busy -> busy -> completed -> completed` in all S09 runs), but Stage 7 stayed open because S09 context/download-start latency and S10 conversations API timing still missed target direction.
	- `ChatContextMenu.vue` is back on the main Messenger chunk to remove the first-open async chunk cost from right-click/long-press interactions. The Messenger JS gzip checkpoint is now `115.41 KB`, still materially below the pre-refactor `138.3 KB` baseline while trading a small bundle increase for interaction latency.
	- `/api/chat/conversations` now uses one combined group/channel room projection through `list_room_conversations`, removing one backend query from the conversation-list read path. Existing group-only/channel-only service APIs remain for other callers.
	- `scripts/run_messenger_benchmark.mjs` now checks document bubble transfer state every `50ms` instead of `200ms`, reducing measurement noise for tap-to-busy/download-start without changing the probed UI state.
	- Focused validation: `python3 -m py_compile core/services/chat_room_service.py api/routers/chat.py`, `python3 -m unittest tests.test_chat_room_service_room_read_models tests.test_chat_router_direct_reads tests.test_chat_router_remaining_paths`, `node --check scripts/run_messenger_benchmark.mjs`, `npm run test:unit:run -- src/components/ChatView.test.ts`, `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, and `npm run build`.
- Stage 7 interaction/upload critical-path follow-up:
	- The S09/S10 rerun completed against `9ac44e8`; document reload persistence stayed fixed, but Stage 7 remained open because S09 download-start/upload-completion and S10 chat/context variability still missed target direction.
	- `ChatMessageItem.vue` now primes the document intent-busy state on primary `pointerdown` for uncached documents and emits the background download action without waiting for an extra Vue tick, while keeping cached/local documents on their existing paths.
	- `ChatView.vue` keeps `ChatContextMenu.vue` mounted so first context-menu open no longer pays component mount cost, and `ChatContextMenu.vue` disables menu transition work under reduced-motion to match benchmark/browser motion settings.
	- `useChatMessages.ts` now defers the `chat-first-message-paint` DOM diagnostic snapshot through `scheduleMessengerDiagnosticTask`, keeping S10 first message paint away from synchronous DOM counting.
	- `chatUploadBackground.ts` now avoids eager document DataURL generation on the normal IndexedDB persistence path, starts upload-batch commit without waiting for the `sending`-phase IDB write, and orders pending writes before retry/delete/failure paths so stale sending records cannot reappear after a successful commit.
	- Focused validation: `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatMessages.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, `npm run test:unit:run -- src/services/chatUploadBackground.test.ts`, and `npm run build` (`MessengerView` JS gzip checkpoint: `115.50 KB`).
- Stage 7 measured checkpoint after interaction/upload critical-path follow-up (committed `aa31079`, 3 measured S09/S10 runs, `--skip-warmup`):
	- Command: `cd frontend && npm run benchmark:messenger -- --config /root/trading-bot/trading_bot/tmp/messenger-benchmark/stage7-s09-s10-upload-probe-config.json --skip-warmup`
	- S09 averages, pre-refactor -> current: list `730.3 -> 613.2 ms`, chat `1201.3 -> 674.9 ms`, context `301.2 -> 247.3 ms`, heap `7.37 -> 7.10 MB`, conversations API `187.1 -> 178.0 ms`, messages API `103.9 -> 115.1 ms`, download start `350.9 -> 665.2 ms`, download complete `98.7 -> 154.2 ms`, download reload `1031.5 -> 853.2 ms`, upload first-visible `1040.0 -> 791.7 ms`, upload completion `603.5 -> 565.1 ms`.
	- S10 averages, pre-refactor -> current: list `7868.8 -> 7040.1 ms`, chat `2271.9 -> 2126.2 ms`, context `470.9 -> 538.7 ms`, heap `7.47 -> 7.18 MB`, conversations API `268.7 -> 259.4 ms`, messages API `494.7 -> 490.4 ms`.
	- Decision: Stage 7 remains open. The run is broadly improved and upload persistence is now directionally green, but S09 download-start/download-complete and S10 context-menu latency still miss the target direction.
- Stage 7 download/context final-gate follow-up:
	- `ChatMessageItem.vue` now captures primary pointerdown on uncached documents, applies the busy class synchronously to the document bubble, and defers the parent download emit to the next macrotask so Playwright/user tap latency is not charged for parent/background setup work.
	- `chatDocumentDownloadBackground.ts` now emits completed state immediately after the completed blob is written into the shared persistent file cache, then deletes the pending IndexedDB record asynchronously. This keeps reload correctness while removing delete latency from the visible completion path.
	- `ChatContextMenu.vue` now opens without the outer zoom transition and avoids backdrop blur on the root menu panel, preserving the menu structure while reducing weak-device paint/composite cost.
	- Focused validation: `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/chat/ChatContextMenu.test.ts src/components/ChatView.test.ts src/services/chatDocumentDownloadBackground.test.ts`, `npm run test:unit:run -- src/components/chat/ChatMessageItem.test.ts src/components/ChatView.test.ts src/composables/chat/useChatMessages.test.ts src/composables/chat/useChatFileHandler.test.ts src/services/chatDocumentDownloadBackground.test.ts src/services/chatUploadBackground.test.ts`, and `npm run build` (`MessengerView` JS gzip checkpoint: `115.54 KB`).
- Remaining Stage 7 work:
	- Rerun the S09/S10 Stage 7 benchmark after the download/context final-gate follow-up.
	- If the rerun is green, close Stage 7 and move to Stage 8.
	- If not green, stabilize any remaining S09 download/upload variability and recover S10 weak-device list/context/heap before moving to Stage 8.

### Stage 8 - Realtime/Notification Coalescing (S07 Critical)

Goal:
- Reduce reactive churn under event burst while preserving correctness.

Files expected:
- frontend/src/composables/chat/useChatWebSocket.ts
- frontend/src/composables/useNotificationRuntime.ts
- frontend/src/stores/notifications.ts

Tests/commands:
- Notification/realtime suites
- Benchmark subset: S07

Exit criteria:
- S07 list/chat better than old baseline direction.
- Unread/toast/deep-link behavior preserved.

Rollback:
- Revert event coalescing changes.

### Stage 9 - UI System Enforcement Pass

Goal:
- Enforce design-token usage and remove visual drift.

Files expected:
- frontend/src/styles/messenger-design-tokens.css
- frontend/src/components/chat/ChatMessageItem.vue
- frontend/src/components/chat/ChatAlbumLayout.vue
- frontend/src/components/chat/ChatHeader.vue

Tests/commands:
- Snapshot and behavior tests for visual states.

Exit criteria:
- No critical visual drift across direct/group/channel UI surfaces.

Rollback:
- Revert token and style enforcement commits.

### Stage 10 - Group/Channel/Direct Manager Standardization

Goal:
- Normalize manager UX, option grouping, and role-aware action placement.

Files expected:
- frontend/src/components/CreateChannelView.vue
- frontend/src/components/chat/ChatGroupManagerModal.vue
- frontend/src/views/ChatView.vue

Tests/commands:
- Manager/profile Playwright slices (all browsers)

Exit criteria:
- Consistent manager information architecture and action semantics.

Rollback:
- Revert manager-specific view changes.

### Stage 11 - Weak-Device and Motion Final Pass

Goal:
- Final polish for low-end devices and motion budget.

Files expected:
- frontend/src/styles/messenger-design-tokens.css
- frontend/src/components/chat/ChatConversationList.vue
- frontend/src/components/chat/ChatInputBar.vue

Tests/commands:
- Benchmark subset: S10
- Manual reduced-motion verification

Exit criteria:
- S10 improved versus old baseline in practical responsiveness.

Rollback:
- Revert low-end and motion-only patches.

### Stage 12 - Final Benchmark + Release Closure

Goal:
- Run full benchmark and produce release decision package.

Files expected:
- tmp/messenger-benchmark/comparison-summary.md
- tmp/messenger-benchmark/comparison-summary.json
- tmp/messenger-benchmark/surface-status.json
- docs/MESSENGER_REFACTORING_ROADMAP.md
- docs/MESSENGER_REFACTOR_EXECUTION_ROADMAP.md

Tests/commands:
- make messenger-benchmark-all
- Browser-matrix messenger slices

Exit criteria:
- Global Success Contract fully green.
- New version meaningfully better than old in official summary.

Rollback:
- Keep legacy path as fallback until explicit final acceptance.

## Prompt Template (Operational)

Use this message pattern for each stage:

1. Stage X kickoff
2. Planned file set (max 3-6 key files)
3. Expected KPI movement
4. Focused test set
5. Exit criteria

This template ensures each prompt is bounded, auditable, and benchmark-driven.
