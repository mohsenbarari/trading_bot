<script setup lang="ts">
const emit = defineEmits<{
  (e: 'close'): void
}>()

const props = defineProps<{
  lightboxMedia: { url: string; type: 'image' | 'video' } | null
}>()

function handleSaveMedia() {
  if (!props.lightboxMedia) return
  const a = document.createElement('a')
  a.href = props.lightboxMedia.url
  const ext = props.lightboxMedia.type === 'video' ? 'mp4' : 'jpg'
  a.download = `media_${Date.now()}.${ext}`
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="lightbox">
      <div v-if="lightboxMedia" class="lightbox-overlay" @click="emit('close')">
        <div class="lightbox-content" @click.stop>
          <div class="lightbox-toolbar">
            <button class="lightbox-btn" @click="handleSaveMedia" title="ذخیره">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </button>
            <button class="lightbox-btn close" @click="emit('close')" title="بستن">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
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
  background: rgba(0, 0, 0, 0.92);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(8px);
}

.lightbox-content {
  position: relative;
  max-width: 92vw;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.lightbox-content img, .lightbox-content video {
  max-width: 100%;
  max-height: 85vh;
  object-fit: contain;
  border-radius: 8px;
}

.lightbox-toolbar {
  position: absolute;
  top: -48px;
  right: 0;
  display: flex;
  gap: 8px;
  z-index: 10;
}

.lightbox-btn {
  background: rgba(255, 255, 255, 0.12);
  border: none;
  color: white;
  cursor: pointer;
  padding: 8px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.lightbox-btn:hover {
  background: rgba(255, 255, 255, 0.25);
}

.lightbox-btn.close {
  background: rgba(239, 68, 68, 0.2);
}

.lightbox-btn.close:hover {
  background: rgba(239, 68, 68, 0.4);
}

/* Lightbox transition */
.lightbox-enter-active,
.lightbox-leave-active {
  transition: opacity 0.25s ease, backdrop-filter 0.25s ease;
}

.lightbox-enter-from,
.lightbox-leave-to {
  opacity: 0;
}

.lightbox-enter-active .lightbox-content,
.lightbox-leave-active .lightbox-content {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.25s ease;
}

.lightbox-enter-from .lightbox-content {
  transform: scale(0.85);
  opacity: 0;
}

.lightbox-leave-to .lightbox-content {
  transform: scale(0.9);
  opacity: 0;
}
</style>
