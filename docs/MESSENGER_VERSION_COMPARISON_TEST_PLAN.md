# Messenger Version Comparison Test Plan

> Date: 2026-05-30  
> Status: Drafted as the authoritative comparison contract for legacy Messenger, refactor preview, and future rollout candidates  
> Scope: Full-surface functional parity, UX consistency, resilience, and performance comparison across the Messenger product.

## Goal

Build one comprehensive comparison program that can answer three different questions without changing methodology every time:

1. Did the current legacy path regress compared with the historical pre-refactor baseline?
2. Is the refactor path functionally and behaviorally equivalent to the current legacy path on completed surfaces?
3. Does the rollout candidate refactor actually improve the Messenger experience enough to justify becoming the default?

This plan uses the canonical surface ids `M01`-`M14` from `docs/MESSENGER_REFACTORING_ROADMAP.md`.

## Ground Rules

- Compare versions under the same backend, same seeded data, same auth/session contract, and same browser profile.
- Use warmed runs for performance numbers; cold-start traces may be captured separately but must not be the release gate.
- Separate historical comparison from live parity comparison.
- Treat functional parity, UX parity, resilience, and performance as different layers; passing one layer does not imply the others.
- Keep all comparison outputs machine-readable and human-readable.
- Do not mark a surface green merely because it exists in both versions; the interaction model must also match the intended product contract.

## Comparison Modes

### Mode A - Historical Regression Baseline

- Compare: `pre-refactor legacy` vs `current legacy`
- Purpose: prove whether the current production path is already faster/slower or safer/less safe than the historical baseline.
- Current known source: warmed Chromium benchmark using `85402f8` vs `31a7a24`.

### Mode B - Live Parity Comparison

- Compare: `current legacy` vs `current refactor`
- Purpose: detect functional and UX drift while both versions point to the same current backend/runtime.
- Applies only to surfaces whose refactor implementation is actually available.

### Mode C - Release Gate Comparison

- Compare: `rollout candidate refactor` vs `current production legacy`
- Purpose: decide whether the default can switch safely and whether legacy can begin retirement.

## Surface Coverage Matrix

| Surface ID | Comparison target | Existing evidence to reuse | New comparison work required |
|---|---|---|---|
| `M01` Shell/bootstrap/navigation | Route mount, feature-flag switch, loading/error/boot states, route restore, browser back, auth/session redirects | `MessengerView.test.ts`, `auth.spec.ts`, `account-status.spec.ts` | Add explicit old/new route-shell comparison and boot-state UX assertions |
| `M02` Conversation list | Ordering, pins, mute/unread/hide, mandatory room ordering, long-press menu, progressive rendering | `messenger-conversation-actions.spec.ts`, `ChatConversationList.test.ts` | Add version-diff snapshot of list density, action availability, and list performance on large sets |
| `M03` Direct room core | Text send/edit/delete, reply, reactions, mentions, selection, pinned messages | `direct-chat.spec.ts`, `messenger-pinned-message.spec.ts`, `messenger-direct-room-ux.spec.ts` | Add explicit old/new behavioral parity harness for direct-room core flows |
| `M04` Group room | Membership-aware send/read/react, unread/live append, mentions, manager entry points | `channel-media.spec.ts`, `ChatGroupManagerModal.test.ts` | Add version-diff coverage for group header, message density, and manager UX consistency |
| `M05` Channel room | Admin/member gating, mandatory/optional behavior, reactions, unread/reset, header-open management | `channel-media.spec.ts`, `mandatory-channel.spec.ts`, `CreateChannelView` tests | Add explicit old/new parity for admin/member composer and room-management semantics |
| `M06` Composer and overlays | Emoji/sticker, attachment, reply/edit, read-only, search, selection bar, keyboard transitions | `ChatInputBar.test.ts`, `AttachmentMenu.test.ts`, `messenger-direct-room-ux.spec.ts` | Add overlay-arbitration and keyboard-motion comparison across versions |
| `M07` Message rendering | Text, album, video, voice, document, location, forwarded metadata, mention highlight, selection highlight | `ChatMessageItem.test.ts`, `ChatAlbumLayout.vue` consumers, `channel-media.spec.ts` | Add screenshot/parity snapshots for each message family in both versions |
| `M08` Actions and batch ops | Context menu, share, forward, delete, copy, pin, album actions, browser-back exit | `ChatContextMenu.test.ts`, `messenger-direct-room-ux.spec.ts`, `messenger-pinned-message.spec.ts` | Add one cross-version batch-actions suite with surface-level result summaries |
| `M09` Search/history/viewers | Global search, in-chat search, search result nav, lightbox, location viewer, scroll-to-target | `messenger-direct-room-ux.spec.ts`, `ChatLightbox.test.ts`, `LocationViewerModal.test.ts` | Add old/new parity for search result UX and viewer toolbar behavior |
| `M10` Media/upload/download/cache | Background upload, resumable upload, cancellation, progress, cache reuse, file open/share/download | `useChatMedia.test.ts`, `useChatFileHandler.test.ts`, `channel-media.spec.ts`, `messenger-direct-room-ux.spec.ts` | Add version-diff performance + resilience scenarios for media-heavy rooms |
| `M11` Room/profile management | New conversation, group/channel create/edit/avatar/member/admin flows, profile opens | `ChatNewConversationModal.test.ts`, `messenger-room-manager-profile.spec.ts`, `PublicProfile` tests | Add full old/new manager-flow comparison including back-stack consistency |
| `M12` Identity/permission/business rules | Accountant/customer labels, owner-resolution, blocked access, room visibility, profile routing | `customer-chat-privacy.spec.ts`, `accountant-owner-flow.spec.ts`, `customer-owner-flow.spec.ts` | Add parity checks that UI affordances and labels stay business-correct in both versions |
| `M13` Realtime/notifications | Message delivery, typing/upload activity, toasts, browser notifications, deep links, muted-room suppression | `notifications.spec.ts`, `useNotificationRuntime.test.ts`, websocket/unit coverage | Add version-diff latency and label correctness report for live events |
| `M14` Reliability/accessibility/performance | RTL, Jalali, reduced motion, reconnect, offline recovery, weak-device behavior, bundle/heap/DOM/jank | existing benchmark JSON, `account-status.spec.ts`, reconnect/runtime tests | Build the full benchmark + resilience suite and make it release-gating |

## Test Layers

### Layer 0 - Surface Manifest Audit

- Keep one machine-readable manifest for `M01`-`M14`.
- Every surface gets: owner, comparison mode applicability, existing coverage, required new coverage, and release severity.
- Output: `messenger-surface-manifest.json` and `messenger-surface-report.md`.

### Layer 1 - Contract And State Diff

- Compare API payload shape and critical state transitions between versions.
- Cover: route state, conversation projections, message payloads, realtime payloads, upload/download state transitions.
- Output: deterministic JSON diffs without UI noise.

### Layer 2 - Deterministic Component/Composable Tests

- Reuse Vitest for message renderer variants, composer surfaces, overlay arbitration, managers, and file/media helpers.
- Add explicit old/new adapter tests where the same scenario is rendered through both versioned surfaces.
- Output: fast parity feedback on isolated surfaces.

### Layer 3 - Full Functional Parity E2E

- Use Playwright Chromium first as the main comparison engine.
- Re-run green comparison slices on Firefox/WebKit once Chromium parity is stable.
- Cover the full surface matrix through stable seeded scenarios rather than ad hoc user paths.
- Output: per-surface pass/fail plus traces/screenshots for mismatches.

### Layer 4 - Performance Benchmark

- Promote the existing `tmp/messenger_benchmark.mjs` ideas into committed comparison tooling.
- Capture per-surface performance, not only route-open numbers.
- Use median of at least 3 warmed runs for release decisions.
- Output: JSON metrics, Markdown table, and delta classification (`better`, `same`, `worse`).

### Layer 5 - Resilience And Recovery

- Stress background uploads/downloads, route leave/reload survival, reconnect after sleep, offline recovery, and notification deep-link correctness.
- Include manager modal reopen, back-stack recovery, and long-session drift.
- Output: scenario status plus failure artifact links.

### Layer 6 - Manual UX Review

- Review interaction feel, animation restraint, touch targets, visual hierarchy, and UI consistency.
- This is the final layer because some regressions are perceptual, not binary.
- Output: short acceptance checklist with explicit sign-off or rejection reasons.

## Scenario Packs

| Scenario ID | Purpose | Seed profile | Primary surfaces |
|---|---|---|---|
| `S00` | Fresh boot and empty-state sanity | new user, no direct rooms, mandatory room only | `M01`, `M02`, `M05`, `M14` |
| `S01` | Light direct chat | 8 conversations, 40 active-room messages | `M02`, `M03`, `M06`, `M07`, `M08` |
| `S02` | Heavy direct chat | 120 conversations, 700 active-room messages | `M02`, `M03`, `M07`, `M14` |
| `S03` | Media-heavy direct room | mixed image/video/voice/document/location album history | `M07`, `M09`, `M10`, `M14` |
| `S04` | Search/viewer stress | long searchable history with replies and older matches | `M03`, `M09`, `M14` |
| `S05` | Group admin/member matrix | one writable group, one read-only member view | `M04`, `M06`, `M11`, `M13` |
| `S06` | Channel admin/member matrix | optional writable channel, mandatory read-only room | `M05`, `M06`, `M11`, `M13` |
| `S07` | Notification/realtime stress | multi-room message bursts, reactions, mentions, typing/upload activity | `M02`, `M03`, `M04`, `M05`, `M13`, `M14` |
| `S08` | Identity and permission matrix | owner/accountant/customer/blocked viewers and room/profile access | `M11`, `M12`, `M13` |
| `S09` | Upload/download persistence | route leave, reload, resume, cancel, open/share/download from cache | `M08`, `M10`, `M14` |
| `S10` | Weak-device performance | CPU/network throttled Chromium plus reduced-motion preference | `M01`, `M02`, `M03`, `M06`, `M07`, `M14` |
| `S11` | Long-session soak | 20-30 minute event mix with room switching and managers | `M02`, `M03`, `M10`, `M11`, `M13`, `M14` |

## Performance Capture Matrix

Each benchmark run must capture at least the following metrics per scenario when applicable:

- route ready time
- conversation-list first ready time
- chat first paint time
- search ready time
- lightbox open latency
- context-menu open latency
- selection-mode enter/exit latency
- upload handoff time and first progress time
- download first progress and completion time
- scroll FPS, worst frame, janky frame count
- DOM node count and JS heap
- Messenger JS/CSS bundle sizes
- request timings for conversations, messages, poll, room messages, upload finalize, download begin

## Required Artifacts

Every comparison run should emit:

- `comparison-summary.json`
- `comparison-summary.md`
- `surface-status.json`
- `performance-results.json`
- screenshot set for visual mismatches
- Playwright traces/videos for failed parity scenarios
- one final human-readable delta table grouped by surface id and severity

## Severity Model

- `S1`: data loss, wrong recipient/room behavior, broken access rule, broken upload/download integrity
- `S2`: core action unavailable or inconsistent across versions, wrong navigation/back-stack outcome, broken notification route/identity
- `S3`: UX inconsistency, animation/layout mismatch, non-blocking visual drift
- `S4`: cosmetic or non-user-facing instrumentation mismatch

Legacy retirement is blocked by any open `S1` or `S2` issue.

## Release Gate

The refactor can become the default only when all of the following are true:

- every `M01`-`M14` surface is covered by at least one deterministic comparison layer,
- Chromium parity and performance comparison are green on the full required scenario set,
- Firefox/WebKit matrix is green on the final parity slice,
- no open `S1` or `S2` gaps remain,
- heavy-room metrics are measurably better than the current Stage 7 baseline,
- manual UX review confirms one unified interaction model across direct rooms, groups, channels, managers, and media viewers.

## Immediate Next Deliverables

1. Convert the `M01`-`M14` surface map into a machine-readable manifest.
2. Promote the current temporary benchmark runner into committed comparison tooling.
3. Add one version-parity Playwright suite that can run the same scenario against legacy and refactor using the Messenger UI version gate.
4. Add one Markdown/JSON report builder so every run ends in the same table and severity summary.