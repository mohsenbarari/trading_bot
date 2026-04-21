<script setup lang="ts">
import { computed } from 'vue'

/**
 * ChatAlbumLayout.vue
 * Telegram-like dynamic album layout driven by media aspect ratios.
 */
type AlbumItem = {
  msg: any
  url: string
  type: 'image' | 'video'
  width?: number
  height?: number
}

type AlbumCell = {
  item: AlbumItem
  width: number
  height: number
}

type AlbumRow = {
  key: string
  height: number
  cells: AlbumCell[]
}

type AlbumLayout = {
  width: number
  height: number
  rows: AlbumRow[]
}

const GAP = 2
const MAX_ALBUM_WIDTH = 320
const MIN_ALBUM_WIDTH = 232
const MIN_ROW_HEIGHT = 84
const MAX_ROW_HEIGHT = 196

const props = defineProps<{
  items: AlbumItem[]
  currentUserId: number | null
}>()

const emit = defineEmits<{
  (e: 'media-click', msg: any): void
  (e: 'download', msg: any): void
  (e: 'cancel-send', msg: any): void
  (e: 'reply-item', msg: any): void
  (e: 'forward-item', msg: any): void
  (e: 'delete-item', msg: any): void
}>()

const hasActiveUpload = computed(() => props.items.some(item => Boolean(item.msg?.is_sending)))

function formatBytes(bytes: number, decimals = 1) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const factor = 1024
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(factor)), units.length - 1)
  return `${(bytes / Math.pow(factor, index)).toFixed(decimals)} ${units[index]}`
}

function handleCellClick(msg: any) {
  if (msg?.is_sending) return
  emit('media-click', msg)
}

function canDeleteItem(msg: any) {
  if (!msg || msg.sender_id !== props.currentUserId) return false
  const msgTime = new Date(msg.created_at).getTime()
  return (Date.now() - msgTime) <= 48 * 60 * 60 * 1000
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function extractAspectRatio(item: AlbumItem) {
  if (hasActiveUpload.value) {
    return 1
  }

  const width = Number(item.width)
  const height = Number(item.height)

  if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
    return clamp(width / height, 0.66, 1.85)
  }

  return 1
}

function getPreferredAlbumWidth(count: number, averageRatio: number) {
  if (count === 1) {
    if (averageRatio < 0.8) return 236
    if (averageRatio > 1.45) return 320
    return 288
  }

  if (averageRatio < 0.82) return 252
  if (averageRatio < 0.96) return 286
  if (count >= 5 || averageRatio > 1.18) return 320
  return 304
}

function buildPartitions(total: number, maxRowSize = 3, prefix: number[] = [], results: number[][] = []) {
  if (total === 0) {
    results.push(prefix)
    return results
  }

  for (let size = 1; size <= Math.min(maxRowSize, total); size += 1) {
    buildPartitions(total - size, maxRowSize, [...prefix, size], results)
  }

  return results
}

function createSingleLayout(items: AlbumItem[]): AlbumLayout {
  const [item] = items
  if (!item) {
    return { width: MIN_ALBUM_WIDTH, height: MIN_ROW_HEIGHT, rows: [] }
  }

  const ratio = extractAspectRatio(item)
  const width = getPreferredAlbumWidth(1, ratio)
  const height = clamp(Math.round(width / ratio), 148, 420)

  return {
    width,
    height,
    rows: [
      {
        key: `row-${item.msg.id}`,
        height,
        cells: [{ item, width, height }]
      }
    ]
  }
}

function buildLayout(items: AlbumItem[]): AlbumLayout {
  if (items.length <= 1) {
    return createSingleLayout(items)
  }

  const ratios = items.map(extractAspectRatio)
  const averageRatio = ratios.reduce((sum, ratio) => sum + ratio, 0) / ratios.length
  const albumWidth = clamp(getPreferredAlbumWidth(items.length, averageRatio), MIN_ALBUM_WIDTH, MAX_ALBUM_WIDTH)
  const targetHeight = clamp(albumWidth / clamp(averageRatio, 0.78, 1.28), 190, 440)
  const partitions = buildPartitions(items.length)

  let bestLayout: AlbumLayout | null = null
  let bestScore = Number.POSITIVE_INFINITY

  for (const partition of partitions) {
    let cursor = 0
    let totalHeight = 0
    let penalty = 0
    const rowHeights: number[] = []
    const rows: AlbumRow[] = []

    for (const rowSize of partition) {
      const rowItems = items.slice(cursor, cursor + rowSize)
      const rowRatios = ratios.slice(cursor, cursor + rowSize)
      cursor += rowSize

      const ratioSum = rowRatios.reduce((sum, ratio) => sum + ratio, 0)
      const rawRowHeight = (albumWidth - GAP * (rowItems.length - 1)) / ratioSum
      const rowHeight = Math.round(rawRowHeight)

      if (rowHeight < MIN_ROW_HEIGHT) {
        penalty += (MIN_ROW_HEIGHT - rowHeight) * 7
      }
      if (rowHeight > MAX_ROW_HEIGHT) {
        penalty += (rowHeight - MAX_ROW_HEIGHT) * 5
      }
      if (rowSize === 1 && items.length > 2) {
        penalty += 18
      }
      if (rowSize === 1 && partition.length > 2) {
        penalty += 14
      }

      rowHeights.push(rowHeight)
      totalHeight += rowHeight

      const availableWidth = albumWidth - GAP * (rowItems.length - 1)
      let consumedWidth = 0
      const cells: AlbumCell[] = rowItems.map((item, index) => {
        const isLast = index === rowItems.length - 1
        const ratio = rowRatios[index] ?? 1
        const width = isLast
          ? availableWidth - consumedWidth
          : Math.round(rowHeight * ratio)

        consumedWidth += width
        return {
          item,
          width,
          height: rowHeight
        }
      })

      rows.push({
        key: `row-${cursor}-${rowSize}`,
        height: rowHeight,
        cells
      })
    }

    totalHeight += GAP * (partition.length - 1)
    const meanHeight = rowHeights.reduce((sum, height) => sum + height, 0) / rowHeights.length
    const variancePenalty = rowHeights.reduce((sum, height) => sum + Math.abs(height - meanHeight), 0)
    const score = Math.abs(totalHeight - targetHeight) + penalty + variancePenalty

    if (score < bestScore) {
      bestScore = score
      bestLayout = {
        width: albumWidth,
        height: totalHeight,
        rows
      }
    }
  }

  return bestLayout ?? createSingleLayout(items)
}

const layout = computed(() => buildLayout(props.items))
</script>

<template>
  <div class="album-layout" :style="{ width: `${layout.width}px` }">
    <div
      v-for="row in layout.rows"
      :key="row.key"
      class="album-row"
      :style="{ height: `${row.height}px` }"
    >
      <div
        v-for="cell in row.cells"
        :key="cell.item.msg.id"
        :id="`album-item-${cell.item.msg.id}`"
        class="album-item"
        :style="{ width: `${cell.width}px`, height: `${cell.height}px` }"
        @click="handleCellClick(cell.item.msg)"
      >
        <div v-if="!cell.item.msg.is_sending" class="album-item-actions" data-context-ignore>
          <button class="album-action-btn" title="پاسخ" @click.stop="emit('reply-item', cell.item.msg)">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
          </button>
          <button class="album-action-btn" title="هدایت" @click.stop="emit('forward-item', cell.item.msg)">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
          </button>
          <button
            v-if="canDeleteItem(cell.item.msg)"
            class="album-action-btn delete"
            title="حذف"
            @click.stop="emit('delete-item', cell.item.msg)"
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
          </button>
        </div>
        <img
          v-if="cell.item.type === 'image'"
          :src="cell.item.url"
          :data-media-msg-id="cell.item.msg.id"
          loading="lazy"
          class="album-media msg-media-content"
        />
        <video
          v-else
          :src="cell.item.url"
          class="album-media"
          muted
          loop
          playsinline
        ></video>
        <div v-if="cell.item.type === 'video'" class="album-video-badge">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="white"><path d="M8 5v14l11-7z"/></svg>
        </div>
        <div
          v-if="cell.item.msg.is_sending"
          class="album-upload-overlay"
          @click.stop="emit('cancel-send', cell.item.msg)"
        >
          <div v-if="(cell.item.msg.upload_progress || 0) < 100" class="album-upload-badge">
            <span>
              {{ formatBytes(cell.item.msg.upload_loaded || 0) }} / {{ formatBytes(cell.item.msg.upload_total || 0) }}
            </span>
          </div>
          <div class="album-progress-shell">
            <svg class="album-progress-ring" viewBox="0 0 36 36">
              <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
              <circle
                class="ring-fg"
                cx="18"
                cy="18"
                r="16"
                :stroke-dasharray="`${cell.item.msg.upload_progress || 0}, 100`"
              ></circle>
            </svg>
            <span class="album-progress-cancel">✕</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.album-layout {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-radius: 14px;
  overflow: hidden;
  max-width: 100%;
  cursor: pointer;
  background: rgba(0, 0, 0, 0.04);
}

.album-row {
  display: flex;
  gap: 2px;
}

.album-item {
  position: relative;
  overflow: hidden;
  flex: none;
  background: rgba(0, 0, 0, 0.06);
}

.album-item.highlight-message::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  animation: album-item-highlight 2.5s ease-in-out forwards;
  z-index: 2;
}

@keyframes album-item-highlight {
  0% {
    box-shadow: none;
    background: transparent;
  }
  15% {
    box-shadow: inset 0 0 0 3px rgba(255, 200, 0, 0.92), 0 0 0 2px rgba(255, 200, 0, 0.35);
    background: rgba(255, 200, 0, 0.12);
  }
  100% {
    box-shadow: none;
    background: transparent;
  }
}

.album-item-actions {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 3;
  display: flex;
  align-items: center;
  gap: 4px;
}

.album-action-btn {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  background: rgba(0, 0, 0, 0.48);
  backdrop-filter: blur(8px);
  cursor: pointer;
  transition: background 0.18s ease, transform 0.18s ease;
}

.album-action-btn:active {
  transform: scale(0.94);
}

.album-action-btn.delete {
  background: rgba(185, 28, 28, 0.62);
}

.album-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.album-video-badge {
  position: absolute;
  bottom: 6px;
  left: 6px;
  background: rgba(0, 0, 0, 0.56);
  border-radius: 999px;
  padding: 3px 7px;
  display: flex;
  align-items: center;
  gap: 2px;
  backdrop-filter: blur(8px);
}

.album-upload-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(0, 0, 0, 0.34);
  backdrop-filter: blur(6px);
  color: #fff;
  cursor: pointer;
}

.album-upload-badge {
  max-width: calc(100% - 18px);
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.38);
  font-size: 11px;
  line-height: 1.3;
  text-align: center;
}

.album-progress-shell {
  position: relative;
  width: 46px;
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.album-progress-ring {
  position: absolute;
  inset: 0;
  width: 46px;
  height: 46px;
}

.album-progress-cancel {
  position: relative;
  z-index: 1;
  font-size: 18px;
  font-weight: 600;
}

.ring-bg {
  fill: none;
  stroke: rgba(255, 255, 255, 0.28);
  stroke-width: 3;
}

.ring-fg {
  fill: none;
  stroke: #ffffff;
  stroke-width: 3;
  transform: rotate(-90deg);
  transform-origin: center;
}
</style>
