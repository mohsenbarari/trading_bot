<script setup lang="ts">
import { computed } from 'vue'

/**
 * ChatAlbumLayout.vue
 * Telegram-like dynamic album layout driven by media aspect ratios.
 */
type AlbumItem = {
  msg: any
  url: string
  previewUrl?: string
  hasResolvedMedia?: boolean
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
  isDownloadSelectionMode?: boolean
  selectedDownloadMessageIds?: number[]
}>()

const emit = defineEmits<{
  (e: 'media-click', msg: any): void
  (e: 'download', msg: any): void
  (e: 'cancel-send', msg: any): void
  (e: 'cancel-download', msg: any): void
  (e: 'reply-item', msg: any): void
  (e: 'forward-item', msg: any): void
  (e: 'delete-item', msg: any): void
  (e: 'toggle-download-item', msg: any): void
}>()

const hasActiveUpload = computed(() => props.items.some(item => Boolean(item.msg?.is_sending)))
const selectedDownloadMessageIdsSet = computed(() => new Set(props.selectedDownloadMessageIds || []))

function formatBytes(bytes: number, decimals = 1) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const factor = 1024
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(factor)), units.length - 1)
  return `${(bytes / Math.pow(factor, index)).toFixed(decimals)} ${units[index]}`
}

function handleCellClick(msg: any) {
  if (msg?.is_sending) return

  if (props.isDownloadSelectionMode) {
    emit('toggle-download-item', msg)
    return
  }

  emit('media-click', msg)
}

function shouldShowInlineDownload(item: AlbumItem) {
  if (props.isDownloadSelectionMode) return false
  if (item.msg?.is_sending) return false
  return item.type === 'video' && !item.hasResolvedMedia && !item.msg?.is_downloading
}

function shouldShowDownloadProgress(item: AlbumItem) {
  if (props.isDownloadSelectionMode) return false
  if (item.msg?.is_sending) return false
  return item.type === 'video' && Boolean(item.msg?.is_downloading)
}

function shouldShowCenteredPlay(item: AlbumItem) {
  if (props.isDownloadSelectionMode) return false
  if (item.msg?.is_sending) return false
  return item.type === 'video' && Boolean(item.hasResolvedMedia)
}

function isItemSelected(msgId: number) {
  return selectedDownloadMessageIdsSet.value.has(msgId)
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
        :class="{
          'download-selection-mode': props.isDownloadSelectionMode,
          'download-selected': props.isDownloadSelectionMode && isItemSelected(cell.item.msg.id),
          'download-unselected': props.isDownloadSelectionMode && !isItemSelected(cell.item.msg.id)
        }"
        :style="{ width: `${cell.width}px`, height: `${cell.height}px` }"
        @click.stop="handleCellClick(cell.item.msg)"
      >
        <img
          v-if="cell.item.type === 'image'"
          :src="cell.item.hasResolvedMedia ? cell.item.url : (cell.item.previewUrl || cell.item.url)"
          :data-media-msg-id="cell.item.msg.id"
          loading="lazy"
          decoding="async"
          class="album-media msg-media-content"
          :class="{ 'album-media-preview': !cell.item.hasResolvedMedia }"
        />
        <video
          v-else-if="cell.item.hasResolvedMedia"
          :src="cell.item.url"
          class="album-media"
          muted
          loop
          playsinline
        ></video>
        <img
          v-else-if="cell.item.previewUrl || cell.item.url"
          :src="cell.item.previewUrl || cell.item.url"
          :data-media-msg-id="cell.item.msg.id"
          loading="lazy"
          decoding="async"
          class="album-media album-media-preview"
          alt="video preview"
        />
        <div v-else class="album-media album-media-fallback"></div>
        <div v-if="shouldShowCenteredPlay(cell.item)" class="album-video-center-indicator" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="white"><path d="M8 5v14l11-7z"/></svg>
        </div>
        <button
          v-if="shouldShowInlineDownload(cell.item)"
          class="album-download-btn"
          type="button"
          title="دانلود ویدئو"
          data-context-ignore
          data-swipe-ignore
          @click.stop="emit('download', cell.item.msg)"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
        </button>
        <div
          v-if="shouldShowDownloadProgress(cell.item)"
          class="album-download-progress-overlay"
          data-context-ignore
          data-swipe-ignore
          @click.stop="emit('cancel-download', cell.item.msg)"
        >
          <div class="album-download-progress-shell">
            <svg class="album-download-progress-ring" viewBox="0 0 36 36">
              <circle class="ring-bg" cx="18" cy="18" r="16"></circle>
              <circle
                class="ring-fg"
                cx="18"
                cy="18"
                r="16"
                :stroke-dasharray="`${cell.item.msg.download_progress || 0}, 100`"
              ></circle>
            </svg>
            <span class="album-progress-cancel">✕</span>
          </div>
        </div>
        <div v-if="props.isDownloadSelectionMode" class="album-selection-indicator">
          <span class="album-selection-circle" :class="{ selected: isItemSelected(cell.item.msg.id) }">
            <svg v-if="isItemSelected(cell.item.msg.id)" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </span>
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
  direction: ltr;
  gap: 2px;
}

.album-item {
  position: relative;
  overflow: hidden;
  flex: none;
  background: rgba(0, 0, 0, 0.06);
}

.album-item.download-selection-mode {
  cursor: pointer;
}

.album-item.download-selection-mode::before {
  content: '';
  position: absolute;
  inset: 0;
  background: rgba(5, 10, 18, 0.14);
  z-index: 1;
  pointer-events: none;
}

.album-item.download-selected::before {
  background: rgba(77, 163, 255, 0.18);
}

.album-item.download-unselected::before {
  background: rgba(5, 10, 18, 0.34);
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

.album-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.album-media-preview {
  filter: saturate(1.02) contrast(1.03) brightness(0.99);
  transform: scale(1.01);
}

.album-media-fallback {
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.12), rgba(0, 0, 0, 0.08)),
    rgba(0, 0, 0, 0.08);
}

.album-download-btn {
  position: absolute;
  inset: 0;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.28);
  color: #fff;
  cursor: pointer;
  z-index: 3;
  backdrop-filter: blur(4px);
}

.album-download-btn:hover {
  background: rgba(0, 0, 0, 0.36);
}

.album-download-btn svg,
.album-video-center-indicator svg {
  filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.32));
}

.album-video-center-indicator,
.album-download-progress-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 3;
  pointer-events: none;
}

.album-video-center-indicator::before {
  content: '';
  position: absolute;
  width: 54px;
  height: 54px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.34);
  backdrop-filter: blur(6px);
}

.album-video-center-indicator svg {
  position: relative;
  z-index: 1;
  transform: translateX(1px);
}

.album-download-progress-overlay {
  background: rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(4px);
}

.album-download-progress-shell {
  position: relative;
  width: 58px;
  height: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.album-download-progress-ring {
  position: absolute;
  inset: 0;
  width: 58px;
  height: 58px;
}

.album-download-progress-text {
  position: relative;
  z-index: 1;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
}

.album-selection-indicator {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  pointer-events: none;
}

.album-selection-circle {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 2px solid rgba(255, 255, 255, 0.92);
  background: rgba(0, 0, 0, 0.24);
  color: white;
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
}

.album-selection-circle.selected {
  background: #3390ec;
  border-color: #ffffff;
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
