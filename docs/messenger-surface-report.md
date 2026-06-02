# Messenger Surface Report

- Generated at: 2026-06-02T11:37:02.898145+00:00
- Manifest: docs/messenger-surface-manifest.json
- Surface count: 14
- Manifest status: benchmark-complete

## Comparison Modes

| ID | Label | Compare | Purpose |
| --- | --- | --- | --- |
| historical-regression | Mode A - Historical Regression Baseline | pre-refactor-legacy vs current-legacy | Detect whether the current production Messenger regressed against the historical pre-refactor baseline. |
| live-parity | Mode B - Live Parity Comparison | current-legacy vs current-refactor | Detect functional and UX drift while both versions run on the same current backend/runtime. |
| release-gate | Mode C - Release Gate Comparison | rollout-candidate-refactor vs current-production-legacy | Decide whether the refactor can become the default and whether legacy retirement can begin. |

## Test Layers

| Layer | Label | Outputs |
| --- | --- | --- |
| L0 | Surface Manifest Audit | docs/messenger-surface-manifest.json, docs/messenger-surface-report.md |
| L1 | Contract And State Diff | comparison-summary.json, surface-status.json |
| L2 | Deterministic Component And Composable Tests | vitest parity results, component diff artifacts |
| L3 | Full Functional Parity E2E | playwright traces, per-surface pass/fail report |
| L4 | Performance Benchmark | performance-results.json, comparison-summary.md |
| L5 | Resilience And Recovery | docs/MESSENGER_RESILIENCE_REPORT.md, tmp/messenger-benchmark/resilience-report.json |
| L6 | Manual UX Review | docs/MESSENGER_MANUAL_ACCEPTANCE_CHECKLIST.md, tmp/messenger-benchmark/manual-acceptance.json |

## Scenario Packs

| Scenario | Label | Primary Surfaces | Seed Profile |
| --- | --- | --- | --- |
| S00 | Fresh boot and empty-state sanity | M01, M02, M05, M14 | new user, no direct rooms, mandatory room only |
| S01 | Light direct chat | M02, M03, M06, M07, M08 | 8 conversations, 40 active-room messages |
| S02 | Heavy direct chat | M02, M03, M07, M14 | 120 conversations, 700 active-room messages |
| S03 | Media-heavy direct room | M07, M09, M10, M14 | mixed image/video/voice/document/location album history |
| S04 | Search/viewer stress | M03, M09, M14 | long searchable history with replies and older matches |
| S05 | Group admin/member matrix | M04, M06, M11, M13 | one writable group, one read-only member view |
| S06 | Channel admin/member matrix | M05, M06, M11, M13 | optional writable channel, mandatory read-only room |
| S07 | Notification/realtime stress | M02, M03, M04, M05, M13, M14 | multi-room message bursts, reactions, mentions, typing/upload activity |
| S08 | Identity and permission matrix | M11, M12, M13 | owner/accountant/customer/blocked viewers and room/profile access |
| S09 | Upload/download persistence | M08, M10, M14 | route leave, reload, resume, cancel, open/share/download from cache |
| S10 | Weak-device performance | M01, M02, M03, M06, M07, M14 | CPU/network throttled Chromium plus reduced-motion preference |
| S11 | Long-session soak | M02, M03, M10, M11, M13, M14 | 20-30 minute event mix with room switching and managers |

## Surface Index

| ID | Area | Severity | Primary Scenarios | Layers | Evidence | New Work |
| --- | --- | --- | --- | --- | --- | --- |
| M01 | Shell, bootstrap, and navigation | S2 | S00, S10, S11 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M02 | Conversation list | S2 | S00, S01, S02, S07, S10, S11 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M03 | Direct room core | S1 | S01, S02, S04, S07, S10, S11 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M04 | Group room | S2 | S05, S07, S08, S11 | L0, L1, L2, L3, L5, L6 | 2 | 0 |
| M05 | Channel room | S2 | S00, S06, S07, S11 | L0, L1, L2, L3, L5, L6 | 3 | 0 |
| M06 | Composer and overlays | S2 | S01, S03, S04, S05, S06, S10 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M07 | Message rendering | S2 | S01, S02, S03, S04, S10 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M08 | Actions and batch operations | S2 | S01, S03, S04, S09 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M09 | Search, history navigation, and viewer flows | S2 | S03, S04 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M10 | Media, upload, download, and cache pipeline | S1 | S03, S09, S10, S11 | L0, L1, L2, L3, L4, L5, L6 | 4 | 0 |
| M11 | Room and profile management | S2 | S05, S06, S08, S11 | L0, L1, L2, L3, L5, L6 | 3 | 0 |
| M12 | Identity, permission, and business rules | S1 | S07, S08, S11 | L0, L1, L2, L3, L5, L6 | 3 | 0 |
| M13 | Realtime and notification runtime | S2 | S05, S06, S07, S08, S11 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |
| M14 | Reliability, accessibility, localization, and performance | S2 | S00, S02, S03, S04, S07, S09, S10, S11 | L0, L1, L2, L3, L4, L5, L6 | 3 | 0 |

## M01 — Shell, bootstrap, and navigation

- Release owner: frontend-messenger-shell
- Integration owners: frontend-auth-runtime, frontend-session-status-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S00, S10, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Messenger route mount and feature-flag version gate
- Auth bootstrap and loading states
- Route-query restore and browser/device back-stack behavior
- PWA boot interactions and session/account-status redirects

Existing evidence:
- frontend/src/views/MessengerView.test.ts
- frontend/e2e/auth.spec.ts
- frontend/e2e/account-status.spec.ts

Required new work:

Key metrics:
- route_ready_ms
- boot_error_rate
- route_restore_success_rate

## M02 — Conversation list

- Release owner: frontend-conversation-runtime
- Integration owners: frontend-realtime-runtime, backend-chat-read-model
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S00, S01, S02, S07, S10, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Ordering, pin reorder, mandatory-room ordering
- Mute/unmute, manual unread, hide/unfollow
- Unread mention badge and long-press actions
- Progressive rendering and empty/loading/error states

Existing evidence:
- frontend/e2e/messenger-conversation-actions.spec.ts
- frontend/src/components/chat/ChatConversationList.test.ts
- docs/AUTOMATED_TEST_CHECKLIST.md

Required new work:

Key metrics:
- conversation_list_ready_ms
- conversation_row_render_count
- conversation_menu_open_ms

## M03 — Direct room core

- Release owner: frontend-direct-room-runtime
- Integration owners: backend-chat-runtime, frontend-realtime-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S1
- Primary scenarios: S01, S02, S04, S07, S10, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Open room, text send/edit/delete, read state
- Reply, forward, reactions, mentions
- Pinned messages, selection mode, draft/edit restore

Existing evidence:
- frontend/e2e/direct-chat.spec.ts
- frontend/e2e/messenger-pinned-message.spec.ts
- frontend/e2e/messenger-direct-room-ux.spec.ts

Required new work:

Key metrics:
- chat_first_paint_ms
- message_action_latency_ms
- direct_room_error_rate

## M04 — Group room

- Release owner: frontend-group-room-runtime
- Integration owners: backend-chat-room-runtime, frontend-room-management
- Comparison modes: live-parity, release-gate
- Severity: S2
- Primary scenarios: S05, S07, S08, S11
- Test layers: L0, L1, L2, L3, L5, L6

Must include:
- Membership-aware send/read/react flows
- Unread/live append behavior
- Mentions and room activity labels
- Manager entry points and group-specific header semantics

Existing evidence:
- frontend/e2e/channel-media.spec.ts
- frontend/src/components/chat/ChatGroupManagerModal.test.ts

Required new work:

Key metrics:
- group_room_open_ms
- group_event_patch_ms
- group_manager_flow_success_rate

## M05 — Channel room

- Release owner: frontend-channel-room-runtime
- Integration owners: backend-chat-room-runtime, frontend-room-management
- Comparison modes: live-parity, release-gate
- Severity: S2
- Primary scenarios: S00, S06, S07, S11
- Test layers: L0, L1, L2, L3, L5, L6

Must include:
- Admin/member composer gating
- Mandatory and optional channel behavior
- Unread/read reset, reactions, room-open behavior
- Channel-specific room management and header actions

Existing evidence:
- frontend/e2e/channel-media.spec.ts
- frontend/e2e/mandatory-channel.spec.ts
- frontend/src/views/CreateChannelView.vue

Required new work:

Key metrics:
- channel_room_open_ms
- channel_read_reset_ms
- channel_composer_gate_accuracy

## M06 — Composer and overlays

- Release owner: frontend-composer-overlay-runtime
- Integration owners: frontend-keyboard-runtime, frontend-media-entry-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S01, S03, S04, S05, S06, S10
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Text area, emoji/sticker picker, attachment sheet
- Reply/edit banners and read-only banners
- Selection action bar, search mode, overlay arbitration
- Keyboard transitions and back-stack behavior

Existing evidence:
- frontend/src/components/chat/ChatInputBar.test.ts
- frontend/src/components/chat/AttachmentMenu.test.ts
- frontend/e2e/messenger-direct-room-ux.spec.ts

Required new work:

Key metrics:
- overlay_open_ms
- selection_mode_enter_ms
- selection_mode_exit_ms

## M07 — Message rendering

- Release owner: frontend-message-renderer
- Integration owners: frontend-media-runtime, frontend-identity-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S01, S02, S03, S04, S10
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Text, sticker, emoji-rich text
- Image, album, video, voice, document, file, location
- Forwarded metadata, reply preview, mention highlight
- Selection highlight and pinned indicator

Existing evidence:
- frontend/src/components/chat/ChatMessageItem.test.ts
- frontend/e2e/channel-media.spec.ts
- frontend/src/components/chat/ChatAlbumLayout.vue

Required new work:

Key metrics:
- message_family_render_ms
- media_bubble_layout_shift_count
- heavy_room_dom_nodes

## M08 — Actions and batch operations

- Release owner: frontend-message-actions
- Integration owners: frontend-selection-runtime, backend-chat-mutation-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S01, S03, S04, S09
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Context menu, selection mode, copy, reply, forward, delete
- Share, album actions, pin/unpin
- Browser-back exit behavior and destructive confirmations

Existing evidence:
- frontend/src/components/chat/ChatContextMenu.test.ts
- frontend/e2e/messenger-direct-room-ux.spec.ts
- frontend/e2e/messenger-pinned-message.spec.ts

Required new work:

Key metrics:
- context_menu_open_ms
- batch_action_apply_ms
- browser_back_exit_success_rate

## M09 — Search, history navigation, and viewer flows

- Release owner: frontend-search-viewer-runtime
- Integration owners: frontend-timeline-runtime, frontend-media-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S03, S04
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- In-chat search, global search list, result navigation
- Scroll-to-target and scroll-to-reply behavior
- Date separators, search/list toggle
- Lightbox, album viewer, and location viewer

Existing evidence:
- frontend/e2e/messenger-direct-room-ux.spec.ts
- frontend/src/components/chat/ChatLightbox.test.ts
- frontend/src/components/chat/LocationViewerModal.test.ts

Required new work:

Key metrics:
- search_ready_ms
- scroll_to_target_ms
- lightbox_open_ms

## M10 — Media, upload, download, and cache pipeline

- Release owner: frontend-media-runtime
- Integration owners: backend-upload-runtime, frontend-file-cache-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S1
- Primary scenarios: S03, S09, S10, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Background and resumable uploads
- Upload/download progress and cancellation
- Document/media cache reuse
- Open/share/download flows and reload/route-leave survival

Existing evidence:
- frontend/src/composables/chat/useChatMedia.test.ts
- frontend/src/composables/chat/useChatFileHandler.test.ts
- frontend/e2e/channel-media.spec.ts
- frontend/e2e/messenger-direct-room-ux.spec.ts

Required new work:

Key metrics:
- upload_handoff_ms
- upload_first_progress_ms
- download_first_progress_ms
- download_completion_ms

## M11 — Room and profile management

- Release owner: frontend-room-management
- Integration owners: backend-chat-room-runtime, frontend-public-profile-runtime
- Comparison modes: live-parity, release-gate
- Severity: S2
- Primary scenarios: S05, S06, S08, S11
- Test layers: L0, L1, L2, L3, L5, L6

Must include:
- New conversation flow
- Group/channel create, edit, avatar, member, and admin flows
- Public profile opens from Messenger and owner/self profile entry points

Existing evidence:
- frontend/src/components/chat/ChatNewConversationModal.test.ts
- frontend/e2e/messenger-room-manager-profile.spec.ts
- frontend/src/components/PublicProfile.test.ts

Required new work:

Key metrics:
- manager_open_ms
- manager_save_ms
- profile_open_from_messenger_ms

## M12 — Identity, permission, and business rules

- Release owner: messenger-business-contracts
- Integration owners: backend-users-public-runtime, backend-customer-accountant-contracts
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S1
- Primary scenarios: S07, S08, S11
- Test layers: L0, L1, L2, L3, L5, L6

Must include:
- Accountant and customer identity resolution
- Public-profile routing and owner-resolution metadata
- Block state, room access rules, and owner/customer visibility restrictions
- Notification label correctness under business rules

Existing evidence:
- frontend/e2e/customer-chat-privacy.spec.ts
- frontend/e2e/accountant-owner-flow.spec.ts
- frontend/e2e/customer-owner-flow.spec.ts

Required new work:

Key metrics:
- permission_denial_accuracy
- identity_label_accuracy
- profile_route_accuracy

## M13 — Realtime and notification runtime

- Release owner: frontend-realtime-notification-runtime
- Integration owners: backend-realtime-runtime, frontend-notification-store
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S05, S06, S07, S08, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- Message delivery, typing/upload activity, and read receipts
- Reaction fanout and unread counters
- Browser notifications, in-app toasts, and deep-link routing
- Muted-room suppression and label correctness

Existing evidence:
- frontend/e2e/notifications.spec.ts
- frontend/src/composables/useNotificationRuntime.test.ts
- backend websocket and notification coverage

Required new work:

Key metrics:
- event_to_ui_latency_ms
- toast_route_accuracy
- notification_suppression_accuracy

## M14 — Reliability, accessibility, localization, and performance

- Release owner: messenger-performance-and-reliability
- Integration owners: frontend-messenger-shell, frontend-media-runtime, frontend-realtime-notification-runtime
- Comparison modes: historical-regression, live-parity, release-gate
- Severity: S2
- Primary scenarios: S00, S02, S03, S04, S07, S09, S10, S11
- Test layers: L0, L1, L2, L3, L4, L5, L6

Must include:
- RTL, Jalali, and Tehran timestamp correctness
- Reduced motion and weak-device behavior
- Reconnect/offline recovery and browser compatibility
- Bundle size, heap, DOM count, jank, and responsiveness

Existing evidence:
- tmp/messenger_benchmark_results.json
- frontend/e2e/account-status.spec.ts
- reconnect/runtime test slices

Required new work:

Key metrics:
- bundle_js_gzip_kb
- bundle_css_gzip_kb
- js_heap_used_mb
- dom_node_count
- scroll_fps
- janky_frame_count
