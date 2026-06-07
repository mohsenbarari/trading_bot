<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'

import ChatMessageItem from './ChatMessageItem.vue'
import MessageRenderBoundary from './messages/MessageRenderBoundary.vue'
import type { ChatTimelineGroup } from '../../types/chat'
import { normalizeTimelineMediaDimensions } from '../../utils/chatMediaDimensions'
import {
  buildVirtualTimelineRows,
  estimateVirtualTimelineRowSize,
  findVirtualTimelineMessageRowIndex,
  type VirtualDateRow,
  type VirtualMessageRow,
  type VirtualTimelineRow,
} from '../../utils/chatVirtualTimeline'
import { isUnreadMessageForViewer } from '../../utils/chatUnread'

const props = defineProps<{
  groups: ChatTimelineGroup[]
  state: any
  handlers: Record<string, any>
}>()

const rootRef = ref<HTMLElement | null>(null)
const measuredRowHeights = new Map<string, number>()
const MAX_SCROLL_ADJUSTMENT_ATTEMPTS = 10
let scrollIntentVersion = 0

const normalizedGroups = computed(() => normalizeTimelineMediaDimensions(props.groups || []))

const rows = computed<VirtualTimelineRow[]>(() => buildVirtualTimelineRows(normalizedGroups.value))

function estimateRowSize(index: number) {
  return estimateVirtualTimelineRowSize(rows.value, index, measuredRowHeights)
}

const virtualizer = useVirtualizer(computed(() => ({
  count: rows.value.length,
  getScrollElement: () => rootRef.value?.parentElement ?? null,
  estimateSize: estimateRowSize,
  overscan: 8,
  getItemKey: index => rows.value[index]?.key ?? index,
})))

const virtualItems = computed(() => virtualizer.value.getVirtualItems())
const totalSize = computed(() => virtualizer.value.getTotalSize())

type VirtualScrollToMessageOptions = {
  align?: 'start' | 'center' | 'end' | 'auto'
  highlight?: boolean
}

function setMeasuredRowRef(key: string, element: Element | null) {
  if (!(element instanceof HTMLElement)) return
  virtualizer.value.measureElement(element)
  const height = Math.round(element.getBoundingClientRect().height)
  if (height > 0) {
    measuredRowHeights.set(key, height)
  }
}

function highlightRenderedMessage(messageId: number) {
  const element = getRenderedMessageElement(messageId)
  if (!element) return false

  element.classList.remove('highlight-message')
  void (element as HTMLElement).offsetWidth
  element.classList.add('highlight-message')
  window.setTimeout(() => {
    element.classList.remove('highlight-message')
  }, 3000)
  return true
}

function getRenderedMessageElement(messageId: number) {
  return document.getElementById(`msg-${messageId}`) || document.getElementById(`album-item-${messageId}`)
}

function isRenderedMessageAligned(messageId: number, align: VirtualScrollToMessageOptions['align']) {
  const element = getRenderedMessageElement(messageId)
  const scrollElement = rootRef.value?.parentElement
  if (!(element instanceof HTMLElement) || !(scrollElement instanceof HTMLElement)) {
    return false
  }

  if (align !== 'start') {
    return true
  }

  const elementRect = element.getBoundingClientRect()
  const scrollRect = scrollElement.getBoundingClientRect()
  return Math.abs(elementRect.top - scrollRect.top) <= 24
}

function findFirstUnreadMessageId(currentUserId: number) {
  for (const row of rows.value) {
    if (row.type !== 'message') continue
    if ('messages' in row.item) {
      const unreadAlbumItem = row.item.messages.find(message => isUnreadMessageForViewer(message, currentUserId))
      if (unreadAlbumItem) return unreadAlbumItem.id
      continue
    }

    if (isUnreadMessageForViewer(row.item, currentUserId)) {
      return row.item.id
    }
  }

  return null
}

function scrollToMessage(messageId: number, options: VirtualScrollToMessageOptions = {}) {
  const intentVersion = ++scrollIntentVersion
  const rowIndex = findVirtualTimelineMessageRowIndex(rows.value, messageId)
  if (rowIndex < 0) return false

  let attempt = 0
  const align = options.align ?? 'center'
  const shouldHighlight = options.highlight ?? true
  const adjustAndHighlight = () => {
    if (intentVersion !== scrollIntentVersion) return
    virtualizer.value.scrollToIndex(rowIndex, { align })
    requestAnimationFrame(() => {
      void nextTick(() => {
        if (intentVersion !== scrollIntentVersion) return
        virtualizer.value.measure()
        if (shouldHighlight && highlightRenderedMessage(messageId)) return
        if (!shouldHighlight && isRenderedMessageAligned(messageId, align)) return

        attempt += 1
        if (attempt < MAX_SCROLL_ADJUSTMENT_ATTEMPTS) {
          window.setTimeout(adjustAndHighlight, 40)
        }
      })
    })
  }

  adjustAndHighlight()
  return true
}

function scrollToBottom() {
  scrollIntentVersion += 1
  if (rows.value.length === 0) return false
  virtualizer.value.scrollToIndex(rows.value.length - 1, { align: 'end' })
  return true
}

function cancelScrollIntent() {
  scrollIntentVersion += 1
}

function scrollToUnreadOrBottom(currentUserId: number) {
  scrollIntentVersion += 1
  const unreadMessageId = findFirstUnreadMessageId(currentUserId)
  if (unreadMessageId !== null) {
    return scrollToMessage(unreadMessageId, { align: 'start', highlight: false })
  }

  return scrollToBottom()
}

function preservePrependAnchor(messageId: number) {
  return scrollToMessage(messageId, { align: 'start', highlight: false })
}

defineExpose({
  scrollToMessage,
  scrollToBottom,
  scrollToUnreadOrBottom,
  preservePrependAnchor,
  cancelScrollIntent,
})
</script>

<template>
  <div ref="rootRef" class="virtual-timeline" :style="{ height: `${totalSize}px` }">
    <div
      v-for="virtualRow in virtualItems"
      :key="virtualRow.key"
      :ref="(element) => setMeasuredRowRef(String(virtualRow.key), element as Element | null)"
      class="virtual-timeline-row"
      :data-index="virtualRow.index"
      :style="{ transform: `translateY(${virtualRow.start}px)` }"
    >
      <template v-if="rows[virtualRow.index]?.type === 'date'">
        <div class="date-separator sticky-date virtual-date-row">
          <span @click="handlers.scrollToTimelineGroup((rows[virtualRow.index] as VirtualDateRow).group)">
            {{ (rows[virtualRow.index] as VirtualDateRow).group.label }}
          </span>
        </div>
      </template>

      <template v-else-if="rows[virtualRow.index]?.type === 'message'">
        <MessageRenderBoundary
          :messageId="(rows[virtualRow.index] as VirtualMessageRow).item.id"
          :renderKey="`${state.searchQuery}:${state.isSelectionMode}:${state.selectionMemoKey}`"
        >
          <ChatMessageItem
            v-memo="[(rows[virtualRow.index] as VirtualMessageRow).item, state.searchQuery, state.isSelectionMode, handlers.isAlbumInDownloadSelection((rows[virtualRow.index] as VirtualMessageRow).item), state.selectionMemoKey]"
            :msg="handlers.getTimelineItemMessage((rows[virtualRow.index] as VirtualMessageRow).item)"
            :isAlbum="handlers.isAlbumTimelineItem((rows[virtualRow.index] as VirtualMessageRow).item)"
            :albumItems="handlers.getTimelineItemAlbumItems((rows[virtualRow.index] as VirtualMessageRow).item)"
            :isAlbumDownloadMode="handlers.isAlbumInDownloadSelection((rows[virtualRow.index] as VirtualMessageRow).item)"
            :selectedAlbumDownloadMessageIds="state.selectedMessages"
            :currentUserId="state.currentUserId"
            :selectedUserName="state.selectedUserName"
            :selectedMessages="state.selectedMessages"
            :imageCache="state.imageCache"
            :isSelectionMode="state.isSelectionMode"
            :searchQuery="state.searchQuery"
            :isManagementMessage="state.isSelectedManagementRoom"
            :room-kind="state.selectedRoomKind"
            @swipe-reply="handlers.handleReply"
            @select="handlers.handleGroupedItemSelection((rows[virtualRow.index] as VirtualMessageRow).item)"
            @click-message="handlers.handleMessageClick"
            @context-menu="handlers.showContextMenu"
            @scroll-to="handlers.scrollToMessage"
            @media-click="handlers.handleMediaInteraction"
            @location-click="handlers.handleLocationClick"
            @download="handlers.downloadMedia"
            @cancel-send="handlers.handleCancelSend"
            @cancel-download="handlers.handleCancelDownload"
            @reply-album-item="handlers.handleAlbumReplyItem"
            @forward-album-item="handlers.handleAlbumForwardItem"
            @delete-album-item="handlers.handleAlbumDeleteItem"
            @toggle-album-download-item="handlers.handleAlbumDownloadItemToggle"
            @toggle-reaction="handlers.handleMessageReactionToggle"
            @recovery-action="handlers.handleRecoveryAction"
            @open-public-profile="handlers.openPublicProfile"
            :on-load="() => handlers.hydrateRenderedMedia((rows[virtualRow.index] as VirtualMessageRow).item)"
          />
        </MessageRenderBoundary>
      </template>
    </div>
  </div>
</template>

<style scoped>
.virtual-timeline {
  position: relative;
  width: 100%;
  min-height: 100%;
}

.virtual-timeline-row {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  contain: layout paint style;
  will-change: transform;
}

.virtual-date-row {
  position: relative;
  top: auto;
}
</style>
