<script setup lang="ts">
/**
 * ChatAlbumLayout.vue
 * Telegram-style mosaic layout for image/video albums.
 * Renders every item in the album without collapsing extra media into a +N overlay.
 */
const props = defineProps<{
  items: Array<{
    msg: any
    url: string
    type: 'image' | 'video'
  }>
}>()

const emit = defineEmits<{
  (e: 'media-click', msg: any): void
  (e: 'download', msg: any): void
}>()

function getLayoutClass(): string {
  const count = props.items.length
  if (count >= 5) return 'album-grid-many'
  if (count === 2) return 'album-grid-2'
  if (count === 3) return 'album-grid-3'
  if (count >= 4) return 'album-grid-4'
  return 'album-grid-1'
}

function getItemStyle(index: number): Record<string, string> {
  const count = props.items.length

  if (count === 5 || count === 8) {
    return index < 2 ? { gridColumn: 'span 3' } : { gridColumn: 'span 2' }
  }

  if (count === 7) {
    return index === 0 ? { gridColumn: 'span 6' } : { gridColumn: 'span 2' }
  }

  if (count >= 9) {
    const remainder = count % 3
    if (remainder === 1 && index === count - 1) {
      return { gridColumn: 'span 6' }
    }
    if (remainder === 2 && index >= count - 2) {
      return { gridColumn: 'span 3' }
    }
  }

  if (count >= 5) {
    return { gridColumn: 'span 2' }
  }

  return {}
}
</script>

<template>
  <div class="album-layout" :class="getLayoutClass()">
    <div
      v-for="(item, index) in items"
      :key="index"
      class="album-item"
      :class="`album-pos-${index}`"
      :style="getItemStyle(index)"
      @click="emit('media-click', item.msg)"
    >
      <img
        v-if="item.type === 'image'"
        :src="item.url"
        :data-media-msg-id="item.msg.id"
        loading="lazy"
        class="album-media msg-media-content"
      />
      <video
        v-else
        :src="item.url"
        class="album-media"
        muted
        loop
        playsinline
      ></video>
      <div v-if="item.type === 'video'" class="album-video-badge">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="white"><path d="M8 5v14l11-7z"/></svg>
      </div>
    </div>
  </div>
</template>

<style scoped>
.album-layout {
  display: grid;
  gap: 2px;
  border-radius: 12px;
  overflow: hidden;
  max-width: 320px;
  cursor: pointer;
}

.album-grid-1 {
  grid-template-columns: 1fr;
}

.album-grid-2 {
  grid-template-columns: 1fr 1fr;
  aspect-ratio: 2/1;
}

.album-grid-3 {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.album-grid-3 .album-pos-0 {
  grid-row: 1 / 3;
}

.album-grid-4 {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.album-grid-many {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  grid-auto-rows: 96px;
  grid-auto-flow: dense;
}

.album-item {
  position: relative;
  overflow: hidden;
  min-height: 80px;
}

.album-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.album-video-badge {
  position: absolute;
  bottom: 4px;
  left: 4px;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 8px;
  padding: 2px 6px;
  display: flex;
  align-items: center;
  gap: 2px;
}
</style>
