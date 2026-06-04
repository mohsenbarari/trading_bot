<script setup lang="ts">
import { computed, ref } from 'vue'
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

const props = defineProps<{
  groups: ChatTimelineGroup[]
  state: any
  handlers: Record<string, any>
}>()

const rootRef = ref<HTMLElement | null>(null)
const measuredRowHeights = new Map<string, number>()

const normalizedGroups = computed(() => normalizeTimelineMediaDimensions(props.groups || []))

const rows = computed<VirtualTimelineRow[]>(() => buildVirtualTimelineRows(normalizedGroups.value))

function estimateRowSize(index: number) {
  return estimateVirtualTimelineRowSize(rows.value, index, measuredRowHeights)
}

const virtualizer = useVirtualizer({
  count: computed(() => rows.value.length),
  getScrollElement: () => rootRef.value?.parentElement ?? null,
  estimateSize: estimateRowSize,
  overscan: 8,
  getItemKey: index => rows.value[index]?.key ?? index,
})

const virtualItems = computed(() => virtualizer.value.getVirtualItems())
const totalSize = computed(() => virtualizer.value.getTotalSize())

function setMeasuredRowRef(key: string, element: Element | null) {
  if (!(element instanceof HTMLElement)) return
  virtualizer.value.measureElement(element)
  const height = Math.round(element.getBoundingClientRect().height)
  if (height > 0) {
    measuredRowHeights.set(key, height)
  }
}

function highlightRenderedMessage(messageId: number) {
  const element = document.getElementById(`msg-${messageId}`) || document.getElementById(`album-item-${messageId}`)
  if (!element) return false

  element.classList.remove('highlight-message')
  void (element as HTMLElement).offsetWidth
  element.classList.add('highlight-message')
  window.setTimeout(() => {
    element.classList.remove('highlight-message')
  }, 3000)
  return true
}

function scrollToMessage(messageId: number) {
  const rowIndex = findVirtualTimelineMessageRowIndex(rows.value, messageId)
  if (rowIndex < 0) return false

  virtualizer.value.scrollToIndex(rowIndex, { align: 'center' })
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      highlightRenderedMessage(messageId)
      virtualizer.value.measure()
    })
  })
  return true
}

defineExpose({
  scrollToMessage,
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
