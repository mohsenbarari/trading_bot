<script setup lang="ts">
import { computed, ref } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'

import ChatMessageItem from './ChatMessageItem.vue'
import MessageRenderBoundary from './messages/MessageRenderBoundary.vue'
import type { ChatTimelineGroup, ChatTimelineItem, Message } from '../../types/chat'
import { normalizeTimelineMediaDimensions, resolveMessageMediaDimensions } from '../../utils/chatMediaDimensions'

type VirtualDateRow = {
  type: 'date'
  key: string
  group: ChatTimelineGroup
}

type VirtualMessageRow = {
  type: 'message'
  key: string
  item: ChatTimelineItem
}

type VirtualTimelineRow = VirtualDateRow | VirtualMessageRow

const props = defineProps<{
  groups: ChatTimelineGroup[]
  state: any
  handlers: Record<string, any>
}>()

const rootRef = ref<HTMLElement | null>(null)
const measuredRowHeights = new Map<string, number>()

const normalizedGroups = computed(() => normalizeTimelineMediaDimensions(props.groups || []))

const rows = computed<VirtualTimelineRow[]>(() => {
  const flattened: VirtualTimelineRow[] = []
  for (const group of normalizedGroups.value) {
    flattened.push({
      type: 'date',
      key: `date:${group.label}`,
      group,
    })
    for (const item of group.items) {
      flattened.push({
        type: 'message',
        key: `message:${item.id}`,
        item,
      })
    }
  }
  return flattened
})

function estimateMessageSize(item: ChatTimelineItem) {
  if ('messages' in item) {
    const count = item.messages.length
    if (count <= 1) return 270
    if (count <= 3) return 250
    return 330
  }

  const message = item as Message
  if (message.message_type === 'image' || message.message_type === 'video') {
    const dimensions = resolveMessageMediaDimensions(message)
    return Math.min(452, Math.max(210, Math.round(320 / dimensions.aspectRatio) + 64))
  }
  if (message.message_type === 'voice') return 88
  if (message.message_type === 'document') return 112
  if (message.message_type === 'location') return 230

  const textLength = typeof message.content === 'string' ? message.content.length : 0
  const lines = Math.ceil(textLength / 42)
  return Math.min(220, Math.max(58, 44 + lines * 24))
}

function estimateRowSize(index: number) {
  const row = rows.value[index]
  if (!row) return 72
  const measured = measuredRowHeights.get(row.key)
  if (measured) return measured
  return row.type === 'date' ? 38 : estimateMessageSize(row.item)
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
