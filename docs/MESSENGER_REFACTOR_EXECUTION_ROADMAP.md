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
| 1 | Baseline Lock + Perf Budget | Pending | Copilot | - | - |
| 2 | Menu IA Normalization | Pending | Copilot | - | - |
| 3 | Conversation List Performance + Visual Cohesion | Pending | Copilot | - | - |
| 4 | Chat Open Pipeline (Heavy/Search/Identity) | Pending | Copilot | - | - |
| 5 | Composer/Overlay State Machine Stabilization | Pending | Copilot | - | - |
| 6 | Context Menu Latency Fix (S05) | Pending | Copilot | - | - |
| 7 | Media Pipeline Optimization (S09/S10) | Pending | Copilot | - | - |
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

### Stage 5 - Composer/Overlay State Machine Stabilization

Goal:
- Eliminate jitter and state conflicts in input/overlay transitions.

Files expected:
- frontend/src/components/chat/ChatInputBar.vue
- frontend/src/utils/messengerStage5ComposerOverlay.ts
- frontend/src/views/ChatView.vue

Tests/commands:
- Composer-focused Vitest
- Direct-room UX Playwright subset

Exit criteria:
- Stable transitions for reply/edit/selection/recording/search/picker.

Rollback:
- Revert state-machine adapter and input wiring.

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
