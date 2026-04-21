<script setup lang="ts">
import { computed, ref } from 'vue'

type LightboxItem = {
  msgId: number
  fileId: string
  type: 'image' | 'video'
  url: string
  thumbnail: string
}

const props = defineProps<{
  lightboxMedia: {
    items: LightboxItem[]
    currentIndex: number
    albumId: string | null
  } | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'navigate', index: number): void
}>()

const gestureStart = ref<{ x: number; y: number } | null>(null)
const gestureAxis = ref<'horizontal' | 'vertical' | null>(null)
const dragOffsetX = ref(0)
const dragOffsetY = ref(0)

const currentItem = computed(() => {
  if (!props.lightboxMedia) return null
  return props.lightboxMedia.items[props.lightboxMedia.currentIndex] || null
})

const hasAlbumStrip = computed(() => {
  return Boolean(props.lightboxMedia?.albumId) && (props.lightboxMedia?.items.length || 0) > 1
})

const stageTransform = computed(() => {
  const verticalProgress = Math.min(Math.abs(dragOffsetY.value) / 240, 1)
  const scale = gestureAxis.value === 'vertical'
    ? Math.max(0.88, 1 - verticalProgress * 0.12)
    : 1

  return {
    transform: `translate3d(${dragOffsetX.value}px, ${dragOffsetY.value}px, 0) scale(${scale})`,
    transition: gestureStart.value ? 'none' : 'transform 0.22s ease'
  }
})

function resetGesture() {
  gestureStart.value = null
  gestureAxis.value = null
  dragOffsetX.value = 0
  dragOffsetY.value = 0
}

function handleSaveMedia() {
  const item = currentItem.value
  if (!item) return

  const a = document.createElement('a')
  a.href = item.url
  const fallbackExt = item.type === 'video' ? 'mp4' : 'jpg'
  a.download = `${item.fileId || `media_${Date.now()}`}.${fallbackExt}`
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function navigateTo(index: number) {
  if (!props.lightboxMedia) return
  if (index < 0 || index >= props.lightboxMedia.items.length) return
  emit('navigate', index)
}

function navigateRelative(offset: number) {
  if (!props.lightboxMedia) return
  navigateTo(props.lightboxMedia.currentIndex + offset)
}

function handleTouchStart(event: TouchEvent) {
  if (event.touches.length !== 1) return
  const touch = event.touches[0]
  if (!touch) return

  gestureStart.value = { x: touch.clientX, y: touch.clientY }
  gestureAxis.value = null
  dragOffsetX.value = 0
  dragOffsetY.value = 0
}

function handleTouchMove(event: TouchEvent) {
  const touch = event.touches[0]
  const start = gestureStart.value
  if (!touch || !start) return

  const dx = touch.clientX - start.x
  const dy = touch.clientY - start.y

  if (!gestureAxis.value) {
    if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return
    gestureAxis.value = Math.abs(dx) >= Math.abs(dy) ? 'horizontal' : 'vertical'
  }

  if (gestureAxis.value === 'horizontal') {
    if ((props.lightboxMedia?.items.length || 0) <= 1) return
    dragOffsetX.value = dx
    dragOffsetY.value = 0
  } else {
    dragOffsetY.value = dy
    dragOffsetX.value = 0
  }
}

function handleTouchEnd() {
  if (!props.lightboxMedia || !gestureStart.value) {
    resetGesture()
    return
  }

  if (gestureAxis.value === 'horizontal') {
    if (dragOffsetX.value > 60) {
      navigateRelative(1)
    } else if (dragOffsetX.value < -60) {
      navigateRelative(-1)
    }
  } else if (gestureAxis.value === 'vertical' && Math.abs(dragOffsetY.value) > 90) {
    emit('close')
  }

  resetGesture()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="lightbox">
      <div v-if="lightboxMedia && currentItem" class="lightbox-overlay" @click="emit('close')">
        <div class="lightbox-shell">
          <div class="lightbox-toolbar">
            <div class="lightbox-counter" v-if="lightboxMedia.items.length > 1">
              {{ lightboxMedia.currentIndex + 1 }} / {{ lightboxMedia.items.length }}
            </div>
            <div class="lightbox-actions">
              <button class="lightbox-btn" @click.stop="handleSaveMedia" title="ذخیره">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                  <polyline points="7 10 12 15 17 10"></polyline>
                  <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
              </button>
              <button class="lightbox-btn close" @click.stop="emit('close')" title="بستن">
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>
          </div>

          <div
            class="lightbox-stage"
            :style="stageTransform"
            @click.stop
            @touchstart="handleTouchStart"
            @touchmove="handleTouchMove"
            @touchend="handleTouchEnd"
            @touchcancel="handleTouchEnd"
          >
            <img
              v-if="currentItem.type === 'image'"
              :key="currentItem.msgId"
              :src="currentItem.url"
              class="lightbox-media"
              alt="مدیا"
              draggable="false"
            />
            <video
              v-else
              :key="currentItem.msgId"
              :src="currentItem.url"
              class="lightbox-media"
              controls
              autoplay
              playsinline
              @click.stop
            ></video>
          </div>

          <div v-if="hasAlbumStrip" class="lightbox-strip" @click.stop>
            <button
              v-for="(item, index) in lightboxMedia.items"
              :key="item.msgId"
              class="lightbox-thumb"
              :class="{ active: index === lightboxMedia.currentIndex }"
              @click.stop="navigateTo(index)"
            >
              <img :src="item.thumbnail || item.url" alt="thumbnail" />
              <span v-if="item.type === 'video'" class="thumb-video-badge">
                <svg viewBox="0 0 24 24" width="10" height="10" fill="white"><path d="M8 5v14l11-7z" /></svg>
              </span>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.lightbox-overlay {
  position: fixed;
  inset: 0;
  padding: 18px 14px 24px;
  background: rgba(7, 10, 16, 0.74);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(18px);
}

.lightbox-shell {
  width: min(88vw, 860px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.lightbox-toolbar {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: white;
}

.lightbox-counter {
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  font-size: 13px;
  backdrop-filter: blur(12px);
}

.lightbox-actions {
  display: flex;
  gap: 8px;
}

.lightbox-btn {
  background: rgba(255, 255, 255, 0.12);
  border: none;
  color: white;
  cursor: pointer;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
  backdrop-filter: blur(12px);
}

.lightbox-btn:hover {
  background: rgba(255, 255, 255, 0.22);
}

.lightbox-btn.close {
  background: rgba(239, 68, 68, 0.18);
}

.lightbox-btn.close:hover {
  background: rgba(239, 68, 68, 0.3);
}

.lightbox-stage {
  width: min(82vw, 820px);
  max-width: 100%;
  max-height: min(74vh, 760px);
  border-radius: 22px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(10, 14, 20, 0.72);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.28);
}

.lightbox-media {
  display: block;
  width: 100%;
  max-width: 100%;
  max-height: min(74vh, 760px);
  object-fit: contain;
  background: transparent;
}

.lightbox-strip {
  width: min(88vw, 860px);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  overflow-x: auto;
  padding: 6px 4px 0;
}

.lightbox-thumb {
  position: relative;
  width: 54px;
  height: 54px;
  flex: none;
  border: none;
  border-radius: 14px;
  overflow: hidden;
  padding: 0;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.08);
  opacity: 0.55;
  transform: scale(0.95);
  transition: opacity 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
}

.lightbox-thumb.active {
  opacity: 1;
  transform: scale(1);
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.9);
}

.lightbox-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.thumb-video-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.56);
  backdrop-filter: blur(8px);
}

.lightbox-enter-active,
.lightbox-leave-active {
  transition: opacity 0.22s ease;
}

.lightbox-enter-from,
.lightbox-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .lightbox-overlay {
    padding: 12px 10px 18px;
  }

  .lightbox-shell {
    width: 100%;
    gap: 10px;
  }

  .lightbox-stage {
    width: 100%;
    max-height: 68vh;
    border-radius: 18px;
  }

  .lightbox-media {
    max-height: 68vh;
  }

  .lightbox-thumb {
    width: 48px;
    height: 48px;
    border-radius: 12px;
  }
}
</style>
