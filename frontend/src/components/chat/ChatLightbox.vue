<script setup lang="ts">
const emit = defineEmits<{
  (e: 'close'): void
}>()

const props = defineProps<{
  lightboxMedia: { url: string; type: 'image' | 'video' } | null
}>()
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="lightboxMedia" class="lightbox-overlay" @click="emit('close')">
        <div class="lightbox-content" @click.stop>
          <button class="lightbox-close" @click="emit('close')">✕</button>
          <img v-if="lightboxMedia.type === 'image'" :src="lightboxMedia.url" />
          <video v-else-if="lightboxMedia.type === 'video'" :src="lightboxMedia.url" controls autoplay></video>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.lightbox-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}
.lightbox-content {
  position: relative;
  max-width: 90vw;
  max-height: 90vh;
}
.lightbox-content img, .lightbox-content video {
  max-width: 100%;
  max-height: 90vh;
  object-fit: contain;
  border-radius: 8px;
}
.lightbox-close {
  position: absolute;
  top: -40px;
  right: 0;
  background: none;
  border: none;
  color: white;
  font-size: 24px;
  cursor: pointer;
}

/* Optional Vue Transition styles if not globals */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
