# Messenger Refactor Handoff

> Created: 2026-06-07  
> Purpose: historical handoff for the next heavy Messenger refactor after project release.  
> Status: current Messenger is release-ready; broad refactor is intentionally paused. Continue only with bug fixes and narrow, measured optimizations until the next planned architecture cycle.

## Executive Summary

The Messenger went through two major refactor tracks:

1. **Stage 12 / release-closure refactor** made the existing production Messenger safer, benchmarked, rollback-aware, and measurably better than the historical pre-refactor baseline.
2. **Architecture refactor Stages A-H** introduced domain naming, Pinia/service foundations, container extraction, renderer boundaries, media dimension contracts, a gated virtual timeline, request-churn reduction, UI polish, and a full release gate.

The result is stable enough for release, but it is not a fully clean architecture yet. `ChatView.vue` and `ChatMessageItem.vue` remain large compatibility/runtime hubs. The correct next step after release is not deletion or a big-bang rewrite; it is a planned second architecture pass with the context in this document.

## Version Definitions

| Name | Meaning | Current Status | Do Not Confuse With |
| --- | --- | --- | --- |
| Historical pre-refactor | Old benchmark baseline, usually commit `85402f8` and temporary worktree paths such as `../messenger-bench-pre` | Benchmark/reference only | Not part of current production runtime |
| Current legacy / production | The active Messenger runtime in this repo, centered on `MessengerView.vue` -> `ChatView.vue` | Production path; keep | The word `legacy` here does not mean safe-to-delete old code |
| Refactor preview shell | `frontend/src/components/messenger-v2/MessengerRefactorShell.vue` behind `messenger_ui_version=refactor` | Preview/rollout marker, not a complete replacement | Not the production Messenger |
| Virtual timeline path | `ChatVirtualTimeline.vue` behind `VITE_MESSENGER_VIRTUAL_TIMELINE=true` | Experimental/flagged for heavy direct rooms | Not approved as default, especially on weak-device S10 |

## Primary Runtime Files

| Area | Current Files |
| --- | --- |
| Route shell and auth/bootstrap | `frontend/src/views/MessengerView.vue` |
| Main production Messenger runtime | `frontend/src/components/ChatView.vue` |
| Room assembly container | `frontend/src/components/chat/containers/ChatRoomContainer.vue` |
| Conversation list container | `frontend/src/components/chat/containers/ConversationListContainer.vue` |
| Layout shell | `frontend/src/components/chat/containers/ChatShell.vue` |
| Message item compatibility renderer | `frontend/src/components/chat/ChatMessageItem.vue` |
| Virtual timeline, feature-flagged | `frontend/src/components/chat/ChatVirtualTimeline.vue` |
| Context menu | `frontend/src/components/chat/ChatContextMenu.vue` |
| Header/composer/list/media UI | `frontend/src/components/chat/ChatHeader.vue`, `ChatInputBar.vue`, `ChatConversationList.vue`, `AttachmentMenu.vue`, `ChatAlbumLayout.vue`, `ChatLightbox.vue` |
| Preview shell | `frontend/src/components/messenger-v2/MessengerRefactorShell.vue` |

## Extracted Renderer Files

These are real improvements but only the first slice of message-renderer decomposition:

- `frontend/src/components/chat/messages/MessageRenderBoundary.vue`
- `frontend/src/components/chat/messages/ForwardedHeader.vue`
- `frontend/src/components/chat/messages/ReplyPreview.vue`
- `frontend/src/components/chat/messages/TextMessageBubble.vue`

Remaining renderer debt: media, album, voice, document, location, reactions, status/meta, and swipe/context mechanics still live mostly in `ChatMessageItem.vue`.

## Store And Service Foundations

Pinia/service foundations exist, but not every visible state is fully store-owned yet.

Stores:

- `frontend/src/stores/chat/session.ts`
- `frontend/src/stores/chat/conversations.ts`
- `frontend/src/stores/chat/messages.ts`
- `frontend/src/stores/chat/transfers.ts`
- `frontend/src/stores/chat/ui.ts`

Services:

- `frontend/src/services/chat/chatApi.ts`
- `frontend/src/services/chat/chatCacheRepository.ts`
- `frontend/src/services/chat/chatEventGateway.ts`
- `frontend/src/services/chat/chatRoomLifecycle.ts`
- `frontend/src/services/chat/chatManagerCache.ts`

Important follow-up: do not create one huge `useChatStore`. Continue with small domain stores or the project will recreate the same monolith in Pinia.

## Renamed Historical Stage Helpers

The architecture refactor removed stage-numbered helper names and moved to domain names. Future agents should use the current names:

- `frontend/src/utils/chatTimelineController.ts`
- `frontend/src/utils/conversationListModel.ts`
- `frontend/src/utils/composerOverlayState.ts`
- `frontend/src/utils/chatRealtimeMediaPolicy.ts`
- `frontend/src/utils/messageContextMenuModel.ts`
- `frontend/src/utils/messengerRolloutPolicy.ts`
- `frontend/src/utils/messengerRefactor.ts`
- `frontend/src/utils/messengerDiagnosticsMetrics.ts`
- `frontend/src/utils/chatMediaDimensions.ts`
- `frontend/src/utils/chatVirtualTimeline.ts`
- `frontend/src/utils/chatUnread.ts`

If old `messengerStage*` names appear in generated logs or old docs, treat them as historical references unless they are in tracked active code.

## Roadmaps And Acceptance Docs

| File | Role |
| --- | --- |
| `docs/MESSENGER_REFACTORING_ROADMAP.md` | Original broad refactor/release closure history |
| `docs/MESSENGER_ARCHITECTURE_REFACTOR_ROADMAP.md` | Architecture Stages A-H execution contract and final state |
| `docs/MESSENGER_VERSION_COMPARISON_TEST_PLAN.md` | Old/current/refactor comparison strategy |
| `docs/messenger-surface-manifest.json` | Surface catalog M01-M14 |
| `docs/messenger-surface-report.md` | Generated surface readiness report |
| `docs/MESSENGER_MANUAL_ACCEPTANCE_CHECKLIST.md` | Manual acceptance evidence |
| `docs/MESSENGER_RESILIENCE_REPORT.md` | Resilience/recovery evidence |
| `docs/GROUP_CHANNEL_MESSENGER_SPEC.md` | Group/channel behavior contract |

## Benchmark Artifacts

Current important benchmark/config paths:

- Official full benchmark config: `scripts/messenger_benchmark_config.json`
- Benchmark runner: `scripts/run_messenger_benchmark.mjs`
- Benchmark report builder: `scripts/build_messenger_benchmark_report.py`
- Surface report builder: `scripts/build_messenger_surface_report.py`
- Latest official comparison summary: `tmp/messenger-benchmark/comparison-summary.md`
- Latest official comparison JSON: `tmp/messenger-benchmark/comparison-summary.json`
- Latest official surface status: `tmp/messenger-benchmark/surface-status.json`
- Latest official performance JSON: `tmp/messenger-benchmark/performance-results.json`
- S10 context-menu micro-benchmark config: `tmp/messenger-benchmark/stage11-s10-config.json`
- S10 context-menu micro-benchmark output: `tmp/messenger-benchmark/stage11-s10/performance-results.json`

The benchmark runner expects a config via:

```bash
node scripts/run_messenger_benchmark.mjs --config tmp/messenger-benchmark/stage11-s10-config.json
```

Do not pass the config path as a positional argument; that starts the default/full benchmark config.

## Final Measured State

Full Stage H release gate:

- All `14` Messenger surfaces were marked ready.
- Missing items: `0`.
- Browser matrix evidence: last full matrix had `343 passed`, `12 skipped`, and `2` harness-classified failures; focused follow-up mini-batch had `150 passed`.
- Full Messenger benchmark completed with no logged benchmark blockers.

High-level benchmark result from Stage H:

- Chat first paint improved across all measured scenarios.
- Bundle JS gzip improved versus the historical pre-refactor baseline.
- DOM/heap generally stayed within safety budgets.
- Upload completion improved in S09.
- Context-menu latency remained the main measured debt.

After the later context-menu and diagnostics micro-tasks, S10 weak-device micro-benchmark showed:

| Metric | Pre-refactor `85402f8` median | Current `a764789` median | Delta |
| --- | ---: | ---: | ---: |
| Context menu | `309.9 ms` | `350.7 ms` | `+40.8 ms` worse |
| List ready | `7867.7 ms` | `7428.4 ms` | `-439.3 ms` better |
| Chat first paint | `2190.5 ms` | `2093.5 ms` | `-97.0 ms` better |
| Scroll FPS | `61` | `59.5` | slightly worse |
| Jank | `0` | `1` | slightly worse |
| Heap | `7.86 MB` | `8.05 MB` | `+0.19 MB` |
| DOM nodes | `4418` | `4422` | effectively equal |

Interpretation: context menu is much better than the earlier artifact-heavy measurements, but still slightly slower than the old baseline on weak devices. Treat this as performance debt, not as a reason for another broad refactor loop.

## Key Fixes After Architecture Gate

Several post-gate production fixes are important context for future agents:

- Group seen-list added, then album seen-list enabled.
- Seen receipt semantics fixed so group/channel room read cursors do not falsely turn sent messages into recipient-seen state.
- Group sender labels were added, Telegram-style.
- Browser/hardware back behavior was corrected so room switches do not stack multiple exits.
- Context-menu actions now discard overlay back-state instead of popping route history.
- Initial chat-open scroll races were fixed through settle gates, unread/self-message guards, silent-hydration bottom preservation, and delayed-scroll suppression.
- DOM snapshot diagnostics are now gated behind explicit diagnostics enablement:
  - `VITE_MESSENGER_DIAGNOSTICS=true`
  - `window.__MESSENGER_DIAGNOSTICS_ENABLED = true`
  - `localStorage.messenger_diagnostics = "true"`

## Current Deletion Guidance

Do not delete runtime Messenger paths yet.

Safe to delete only if storage pressure requires it:

- Old temporary benchmark worktrees such as `../messenger-bench-pre`, if they exist.
- Old `tmp/messenger-benchmark/*` logs/results after keeping the latest reference artifacts listed above.
- Old `tmp/e2e-logs/*` after keeping final Stage H logs if needed.

Do not delete without a separate retirement plan:

- `frontend/src/components/ChatView.vue`
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/chat/*`
- `frontend/src/components/messenger-v2/MessengerRefactorShell.vue`
- rollout/version-gate utilities
- benchmark configs and report builders

Reason: in current naming, `legacy` means the active production path, not a removable obsolete implementation.

## Remaining Architecture Debt

High-priority debt for the next heavy refactor after release:

1. Shrink `ChatView.vue` from a glue-layer monolith into a compatibility wrapper or small orchestrator.
2. Split `ChatMessageItem.vue` into real domain renderers:
   - media bubble
   - album bubble
   - voice bubble
   - document bubble
   - location bubble
   - reaction/status footer
3. Move more visible state ownership into the domain stores without creating split-brain prop/store reactivity.
4. Revisit virtual timeline rollout only after weak-device S10 is green.
5. Replace timer-heavy scroll intent logic with a small scroll-intent state machine.
6. Keep diagnostics probes gated; cheap marks are fine, DOM traversal must remain opt-in.
7. Keep request-churn reductions targeted. Avoid converting known-safe reload fallbacks unless the event has enough routing data for a correct local patch.

## Recommended Future Plan

For the next heavy refactor, avoid a rewrite. Use this order:

1. Freeze current behavior with focused tests around the user-visible flows that caused recent regressions: back navigation, unread anchors, media hydration, seen-list, group sender labels, direct role tags.
2. Extract `ChatMessageItem.vue` renderers first. This lowers risk in UI/UX work and makes virtualization easier.
3. Convert `ChatRoomContainer` from a prop/handler relay into a store-backed active-room container.
4. Reduce `ChatView.vue` only after renderer/container seams are stable.
5. Re-benchmark S10 and context-menu after each small slice, not with repeated full matrices.
6. Consider legacy/runtime retirement only after the new path is default for a real release window and rollback is no longer needed.

## Operational Rules For Future Agents

- After every change, update `.github/copilot-instructions.md`.
- For code changes, run focused tests first; run full matrix only at release gates.
- If benchmark is needed, start with the smallest scenario config.
- Use `--config` with `scripts/run_messenger_benchmark.mjs`.
- Do not remove rollback/fallback code just because a stage is marked complete.
- Treat `tmp` artifacts as non-authoritative unless the roadmap explicitly names them as the latest reference.
