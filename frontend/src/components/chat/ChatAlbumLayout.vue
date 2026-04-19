<script setup lang="ts">
/**
 * ChatAlbumLayout.vue
 * Telegram-style mosaic layout for grouped consecutive image/video messages.
 * Renders 2-4 images in a dynamic grid layout.
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
  if (count === 2) return 'album-grid-2'
  if (count === 3) return 'album-grid-3'
  if (count >= 4) return 'album-grid-4'
  return 'album-grid-1'
}
</script>

<template>
  <div class="album-layout" :class="getLayoutClass()">
    <div
      v-for="(item, index) in items.slice(0, 4)"
      :key="index"
      class="album-item"
      :class="`album-pos-${index}`"
      @click="emit('media-click', item.msg)"
    >
      <img
        v-if="item.type === 'image'"
        :src="item.url"
        loading="lazy"
        class="album-media"
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
      <div v-if="items.length > 4 && index === 3" class="album-more-overlay">
        +{{ items.length - 4 }}
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

.album-more-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 24px;
  font-weight: 600;
}
</style>
