# Messenger Manual Acceptance Checklist

- Generated at: 2026-06-02T12:02:47.374522+00:00
- Manifest version: 2026-05-31.v2
- Reviewer: GitHub Copilot
- Overall sign-off: `accepted`

## M01 - Shell, bootstrap, and navigation

- Sign-off: `accepted`
- Mapped suites: messenger-view-vitest, auth-shell-playwright, account-status-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S00, S10, S11

Checklist:
- [x] Messenger route mount and feature-flag version gate
- [x] Auth bootstrap and loading states
- [x] Route-query restore and browser/device back-stack behavior
- [x] PWA boot interactions and session/account-status redirects

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M02 - Conversation list

- Sign-off: `accepted`
- Mapped suites: chat-conversation-list-vitest, conversation-actions-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S00, S01, S02, S07, S10, S11

Checklist:
- [x] Ordering, pin reorder, mandatory-room ordering
- [x] Mute/unmute, manual unread, hide/unfollow
- [x] Unread mention badge and long-press actions
- [x] Progressive rendering and empty/loading/error states

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M03 - Direct room core

- Sign-off: `accepted`
- Mapped suites: chat-view-vitest, direct-chat-playwright, direct-room-ux-playwright, pinned-message-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S01, S02, S04, S07, S10, S11

Checklist:
- [x] Open room, text send/edit/delete, read state
- [x] Reply, forward, reactions, mentions
- [x] Pinned messages, selection mode, draft/edit restore

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M04 - Group room

- Sign-off: `accepted`
- Mapped suites: pinned-message-playwright, group-channel-media-playwright, group-manager-vitest, room-manager-profile-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S05, S07, S08, S11

Checklist:
- [x] Membership-aware send/read/react flows
- [x] Unread/live append behavior
- [x] Mentions and room activity labels
- [x] Manager entry points and group-specific header semantics

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M05 - Channel room

- Sign-off: `accepted`
- Mapped suites: pinned-message-playwright, group-channel-media-playwright, mandatory-channel-playwright, group-manager-vitest, room-manager-profile-playwright, notifications-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S00, S06, S07, S11

Checklist:
- [x] Admin/member composer gating
- [x] Mandatory and optional channel behavior
- [x] Unread/read reset, reactions, room-open behavior
- [x] Channel-specific room management and header actions

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M06 - Composer and overlays

- Sign-off: `accepted`
- Mapped suites: conversation-actions-playwright, chat-input-bar-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S01, S03, S04, S05, S06, S10

Checklist:
- [x] Text area, emoji/sticker picker, attachment sheet
- [x] Reply/edit banners and read-only banners
- [x] Selection action bar, search mode, overlay arbitration
- [x] Keyboard transitions and back-stack behavior

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M07 - Message rendering

- Sign-off: `accepted`
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, chat-message-item-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S01, S02, S03, S04, S10

Checklist:
- [x] Text, sticker, emoji-rich text
- [x] Image, album, video, voice, document, file, location
- [x] Forwarded metadata, reply preview, mention highlight
- [x] Selection highlight and pinned indicator

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M08 - Actions and batch operations

- Sign-off: `accepted`
- Mapped suites: direct-chat-playwright, group-channel-media-playwright, chat-input-bar-vitest, use-chat-media-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S01, S03, S04, S09

Checklist:
- [x] Context menu, selection mode, copy, reply, forward, delete
- [x] Share, album actions, pin/unpin
- [x] Browser-back exit behavior and destructive confirmations

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M09 - Search, history navigation, and viewer flows

- Sign-off: `accepted`
- Mapped suites: direct-room-ux-playwright, chat-message-item-vitest, chat-lightbox-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S03, S04

Checklist:
- [x] In-chat search, global search list, result navigation
- [x] Scroll-to-target and scroll-to-reply behavior
- [x] Date separators, search/list toggle
- [x] Lightbox, album viewer, and location viewer

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M10 - Media, upload, download, and cache pipeline

- Sign-off: `accepted`
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, use-chat-media-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S03, S09, S10, S11

Checklist:
- [x] Background and resumable uploads
- [x] Upload/download progress and cancellation
- [x] Document/media cache reuse
- [x] Open/share/download flows and reload/route-leave survival

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M11 - Room and profile management

- Sign-off: `accepted`
- Mapped suites: account-status-playwright, conversation-actions-playwright, group-manager-vitest, room-manager-profile-playwright, accountant-owner-flow-playwright, customer-owner-flow-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S05, S06, S08, S11

Checklist:
- [x] New conversation flow
- [x] Group/channel create, edit, avatar, member, and admin flows
- [x] Public profile opens from Messenger and owner/self profile entry points

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M12 - Identity, permission, and business rules

- Sign-off: `accepted`
- Mapped suites: room-manager-profile-playwright, public-profile-vitest, customer-chat-privacy-playwright, accountant-owner-flow-playwright, customer-owner-flow-playwright, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S07, S08, S11

Checklist:
- [x] Accountant and customer identity resolution
- [x] Public-profile routing and owner-resolution metadata
- [x] Block state, room access rules, and owner/customer visibility restrictions
- [x] Notification label correctness under business rules

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M13 - Realtime and notification runtime

- Sign-off: `accepted`
- Mapped suites: account-status-playwright, direct-chat-playwright, group-channel-media-playwright, mandatory-channel-playwright, chat-message-item-vitest, customer-chat-privacy-playwright, notifications-playwright, notification-runtime-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S05, S06, S07, S08, S11

Checklist:
- [x] Message delivery, typing/upload activity, and read receipts
- [x] Reaction fanout and unread counters
- [x] Browser notifications, in-app toasts, and deep-link routing
- [x] Muted-room suppression and label correctness

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

## M14 - Reliability, accessibility, localization, and performance

- Sign-off: `accepted`
- Mapped suites: direct-room-ux-playwright, group-channel-media-playwright, use-chat-media-vitest, notifications-playwright, notification-runtime-vitest, messenger-resilience-report, messenger-manual-acceptance
- Measured scenarios: S00, S02, S03, S04, S07, S09, S10, S11

Checklist:
- [x] RTL, Jalali, and Tehran timestamp correctness
- [x] Reduced motion and weak-device behavior
- [x] Reconnect/offline recovery and browser compatibility
- [x] Bundle size, heap, DOM count, jank, and responsiveness

Notes:
- Reviewed during the benchmark closure pass on 2026-05-31 against the official comparison artifacts and mapped automation.
- No remaining benchmark-only blocker is recorded for this surface after the L5/L6 evidence pass.

