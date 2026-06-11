# Messenger Resilience Report

- Generated at: 2026-06-06T06:36:02.057787+00:00
- Manifest version: 2026-05-31.v2
- Scope: L5 resilience, recovery, and failure-artifact coverage for M01-M14.

## Failure Artifacts

- `tmp/messenger-benchmark/comparison-summary.md`
- `tmp/messenger-benchmark/comparison-summary.json`
- `tmp/messenger-benchmark/surface-status.json`
- `tmp/messenger-benchmark/performance-results.json`

## Per-Surface Resilience Coverage

### M01 - Shell, bootstrap, and navigation

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S10, S11
- Mapped suites: messenger-view-vitest, auth-shell-playwright, account-status-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/views/MessengerView.test.ts`
  - `frontend/e2e/auth.spec.ts`
  - `frontend/e2e/account-status.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M02 - Conversation list

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S10, S11
- Mapped suites: chat-conversation-list-vitest, conversation-actions-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/messenger-conversation-actions.spec.ts`
  - `frontend/src/components/chat/ChatConversationList.test.ts`
  - `docs/AUTOMATED_TEST_CHECKLIST.md`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M03 - Direct room core

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S10, S11
- Mapped suites: chat-view-vitest, direct-chat-playwright, direct-room-ux-playwright, pinned-message-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/direct-chat.spec.ts`
  - `frontend/e2e/messenger-pinned-message.spec.ts`
  - `frontend/e2e/messenger-direct-room-ux.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M04 - Group room

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S11
- Mapped suites: pinned-message-playwright, group-channel-media-playwright, group-manager-vitest, room-manager-profile-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/channel-media.spec.ts`
  - `frontend/src/components/chat/ChatGroupManagerModal.test.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M05 - Channel room

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S11
- Mapped suites: pinned-message-playwright, group-channel-media-playwright, mandatory-channel-playwright, group-manager-vitest, room-manager-profile-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/channel-media.spec.ts`
  - `frontend/e2e/mandatory-channel.spec.ts`
  - `frontend/src/views/CreateChannelView.vue`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M06 - Composer and overlays

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S10
- Mapped suites: conversation-actions-playwright, chat-input-bar-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/components/chat/ChatInputBar.test.ts`
  - `frontend/src/components/chat/AttachmentMenu.test.ts`
  - `frontend/e2e/messenger-direct-room-ux.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M07 - Message rendering

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S10
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, chat-message-item-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/components/chat/ChatMessageItem.test.ts`
  - `frontend/e2e/channel-media.spec.ts`
  - `frontend/src/components/chat/ChatAlbumLayout.vue`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M08 - Actions and batch operations

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S09
- Mapped suites: direct-chat-playwright, group-channel-media-playwright, chat-input-bar-vitest, use-chat-media-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/components/chat/ChatContextMenu.test.ts`
  - `frontend/e2e/messenger-direct-room-ux.spec.ts`
  - `frontend/e2e/messenger-pinned-message.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M09 - Search, history navigation, and viewer flows

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S03, S04
- Mapped suites: direct-room-ux-playwright, chat-message-item-vitest, chat-lightbox-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/messenger-direct-room-ux.spec.ts`
  - `frontend/src/components/chat/ChatLightbox.test.ts`
  - `frontend/src/components/chat/LocationViewerModal.test.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M10 - Media, upload, download, and cache pipeline

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S09, S10, S11
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, use-chat-media-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/composables/chat/useChatMedia.test.ts`
  - `frontend/src/composables/chat/useChatFileHandler.test.ts`
  - `frontend/e2e/channel-media.spec.ts`
  - `frontend/e2e/messenger-direct-room-ux.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M11 - Room and profile management

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S11
- Mapped suites: account-status-playwright, conversation-actions-playwright, group-manager-vitest, room-manager-profile-playwright, accountant-owner-flow-playwright, customer-owner-flow-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/src/components/chat/ChatNewConversationModal.test.ts`
  - `frontend/e2e/messenger-room-manager-profile.spec.ts`
  - `frontend/src/components/PublicProfile.test.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M12 - Identity, permission, and business rules

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S11
- Mapped suites: room-manager-profile-playwright, public-profile-vitest, customer-chat-privacy-playwright, accountant-owner-flow-playwright, customer-owner-flow-playwright, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/customer-chat-privacy.spec.ts`
  - `frontend/e2e/accountant-owner-flow.spec.ts`
  - `frontend/e2e/customer-owner-flow.spec.ts`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M13 - Realtime and notification runtime

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S11
- Mapped suites: account-status-playwright, direct-chat-playwright, group-channel-media-playwright, mandatory-channel-playwright, chat-message-item-vitest, customer-chat-privacy-playwright, notifications-playwright, notification-runtime-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `frontend/e2e/notifications.spec.ts`
  - `frontend/src/composables/useNotificationRuntime.test.ts`
  - `backend websocket and notification coverage`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.

### M14 - Reliability, accessibility, localization, and performance

- Gate: `ready`
- Benchmark readiness: `measured`
- Resilience scenarios: S07, S09, S10, S11
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, use-chat-media-vitest, notifications-playwright, notification-runtime-vitest, messenger-resilience-report, messenger-manual-acceptance
- Existing evidence:
  - `tmp/messenger_benchmark_results.json`
  - `frontend/e2e/account-status.spec.ts`
  - `reconnect/runtime test slices`
- Failure-artifact links:
  - `tmp/messenger-benchmark/comparison-summary.md`
  - `tmp/messenger-benchmark/comparison-summary.json`
  - `tmp/messenger-benchmark/surface-status.json`
  - `tmp/messenger-benchmark/performance-results.json`
- Notes:
  - Resilience evidence is anchored to the official comparison artifacts plus the mapped automation for this surface.
  - Use the failure artifacts listed above first when a regression reproduces in S07/S09/S10/S11 or the corresponding parity suites.
