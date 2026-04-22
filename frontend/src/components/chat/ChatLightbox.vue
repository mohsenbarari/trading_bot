<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CSSProperties } from 'vue'

type LightboxItem = {
  msgId: number
  fileId: string
  type: 'image' | 'video'
  url: string
  thumbnail: string
  senderId: number | null
  createdAt: string
}

const props = defineProps<{
  lightboxMedia: {
    items: LightboxItem[]
    currentIndex: number
    albumId: string | null
  } | null
  currentUserId: number | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'navigate', index: number): void
  (e: 'reply', msgId: number): void
  (e: 'forward', msgId: number): void
  (e: 'delete', msgId: number): void
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

const canDeleteCurrentItem = computed(() => {
  const item = currentItem.value
  if (!item || item.senderId !== props.currentUserId) return false
  const createdAt = new Date(item.createdAt).getTime()
  return Number.isFinite(createdAt) && (Date.now() - createdAt) <= 48 * 60 * 60 * 1000
})

const sceneTransform = computed(() => {
  const verticalProgress = Math.min(Math.abs(dragOffsetY.value) / 240, 1)
  const scale = gestureAxis.value === 'vertical'
    ? Math.max(0.88, 1 - verticalProgress * 0.12)
    : 1

  return {
    transform: `translate3d(0, ${dragOffsetY.value}px, 0) scale(${scale})`,
    transition: gestureStart.value && gestureAxis.value === 'vertical' ? 'none' : 'transform 0.3s cubic-bezier(0.22, 1, 0.36, 1)'
  }
})

watch(() => props.lightboxMedia?.currentIndex, () => {
  resetGesture()
})

watch(() => props.lightboxMedia?.albumId, () => {
  resetGesture()
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

function emitForCurrent(action: 'reply' | 'forward' | 'delete') {
  const item = currentItem.value
  if (!item) return

  if (action === 'reply') {
    emit('reply', item.msgId)
    return
  }

  if (action === 'forward') {
    emit('forward', item.msgId)
    return
  }

  emit('delete', item.msgId)
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

function getHorizontalDragRatio() {
  if (gestureAxis.value !== 'horizontal') return 0
  return Math.max(-1, Math.min(1, dragOffsetX.value / 220))
}

function shouldRenderStageItem(index: number) {
  const activeIndex = props.lightboxMedia?.currentIndex ?? 0
  return Math.abs(index - activeIndex) <= 1
}

function getStageItemStyle(index: number): CSSProperties {
  const activeIndex = props.lightboxMedia?.currentIndex ?? 0
  const dragRatio = getHorizontalDragRatio()
  const compositeOffset = index - activeIndex + dragRatio
  const distance = Math.abs(compositeOffset)

  const opacity = Math.max(0, 1 - distance * 0.42)
  const scale = Math.max(0.78, 1 - distance * 0.12)
  const rotateY = compositeOffset * -26
  const translateX = compositeOffset * 74
  const translateZ = Math.round(Math.max(-220, 90 - distance * 160))
  const blur = Math.min(distance * 1.4, 4)

  return {
    opacity: String(opacity),
    transform: `translate3d(${translateX}%, 0, ${translateZ}px) rotateY(${rotateY}deg) scale(${scale})`,
    filter: `blur(${blur}px) saturate(${Math.max(0.82, 1.06 - distance * 0.16)})`,
    zIndex: String(200 - Math.round(distance * 100)),
    pointerEvents: distance < 0.35 ? 'auto' : 'none',
  }
}

function getThumbStyle(index: number): CSSProperties {
  const activeIndex = props.lightboxMedia?.currentIndex ?? 0
  const dragRatio = getHorizontalDragRatio()
  const compositeOffset = index - activeIndex + dragRatio * 0.9
  const distance = Math.abs(compositeOffset)

  if (distance > 4.25) {
    return {
      opacity: '0',
      transform: `translate3d(${compositeOffset * 88}px, 18px, 0) scale(0.72) rotateY(${compositeOffset * -20}deg)`,
      filter: 'blur(6px) saturate(0.7)',
      zIndex: '0',
      pointerEvents: 'none',
    }
  }

  const scale = Math.max(0.72, 1 - distance * 0.14)
  const translateY = Math.min(distance * 8, 16)
  const rotateY = compositeOffset * -24
  const opacity = Math.max(0.22, 1 - distance * 0.22)
  const brightness = Math.max(0.72, 1.08 - distance * 0.12)
  const saturate = Math.max(0.78, 1.12 - distance * 0.12)
  const shadowLift = Math.max(0, 14 - distance * 4)

  return {
    opacity: String(opacity),
    transform: `translate3d(${compositeOffset * 88}px, ${translateY}px, 0) scale(${scale}) rotateY(${rotateY}deg)`,
    filter: `saturate(${saturate}) brightness(${brightness})`,
    zIndex: String(100 - Math.round(distance * 10)),
    boxShadow: distance < 0.45
      ? '0 0 0 2px rgba(255, 255, 255, 0.92), 0 20px 34px rgba(0, 0, 0, 0.34)'
      : `0 ${shadowLift}px ${Math.max(16, shadowLift * 2)}px rgba(0, 0, 0, 0.18)`,
  }
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
      navigateRelative(-1)
    } else if (dragOffsetX.value < -60) {
      navigateRelative(1)
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
          <div class="lightbox-toolbar" @click.stop>
            <div class="lightbox-counter" v-if="lightboxMedia.items.length > 1">
              {{ lightboxMedia.currentIndex + 1 }} / {{ lightboxMedia.items.length }}
            </div>
            <div v-else class="lightbox-counter placeholder"></div>

            <div class="lightbox-actions">
              <button class="lightbox-btn" @click.stop="emitForCurrent('reply')" title="پاسخ">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
              </button>
              <button class="lightbox-btn" @click.stop="emitForCurrent('forward')" title="هدایت">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
              </button>
              <button v-if="canDeleteCurrentItem" class="lightbox-btn danger" @click.stop="emitForCurrent('delete')" title="حذف">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
              </button>
              <button class="lightbox-btn" @click.stop="handleSaveMedia" title="ذخیره">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
              </button>
              <button class="lightbox-btn close" @click.stop="emit('close')" title="بستن">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
              </button>
            </div>
          </div>

          <div class="lightbox-stage-wrap">
            <div
              class="lightbox-stage"
              @touchstart="handleTouchStart"
              @touchmove="handleTouchMove"
              @touchend="handleTouchEnd"
              @touchcancel="handleTouchEnd"
            >
              <div class="lightbox-stage-scene" :style="sceneTransform">
                <div class="lightbox-stage-track">
                  <div
                    v-for="(item, index) in lightboxMedia.items"
                    v-show="shouldRenderStageItem(index)"
                    :key="item.msgId"
                    class="lightbox-stage-card"
                    :class="{ active: index === lightboxMedia.currentIndex }"
                    :style="getStageItemStyle(index)"
                  >
                    <img
                      v-if="item.type === 'image'"
                      :src="item.url"
                      class="lightbox-media"
                      alt="مدیا"
                      draggable="false"
                      @click.stop
                    />
                    <video
                      v-else
                      :src="item.url"
                      class="lightbox-media"
                      :controls="index === lightboxMedia.currentIndex"
                      :autoplay="index === lightboxMedia.currentIndex"
                      :muted="index !== lightboxMedia.currentIndex"
                      playsinline
                      @click.stop
                    ></video>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="lightbox-strip-slot" @click.stop>
            <div v-if="hasAlbumStrip" class="lightbox-strip">
              <button
                v-for="(item, index) in lightboxMedia.items"
                :key="item.msgId"
                class="lightbox-thumb"
                :class="{ active: index === lightboxMedia.currentIndex }"
                :style="getThumbStyle(index)"
                :aria-current="index === lightboxMedia.currentIndex ? 'true' : 'false'"
                @click.stop="navigateTo(index)"
              >
                <img :src="item.thumbnail || item.url" alt="thumbnail" class="lightbox-thumb-image" />
                <span v-if="item.type === 'video'" class="thumb-video-badge">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="white"><path d="M8 5v14l11-7z" /></svg>
                </span>
              </button>
            </div>
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
  padding: max(14px, env(safe-area-inset-top)) max(14px, env(safe-area-inset-right)) max(18px, env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left));
  background: rgba(7, 10, 16, 0.76);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(18px);
}

.lightbox-shell {
  width: min(92vw, 920px);
  max-width: 100%;
  min-width: 0;
  height: min(100%, 920px);
  max-height: 100%;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 12px;
  color: white;
}

.lightbox-toolbar {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.lightbox-counter {
  min-width: 68px;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  font-size: 13px;
  line-height: 1;
  text-align: center;
  backdrop-filter: blur(12px);
}

.lightbox-counter.placeholder {
  visibility: hidden;
}

.lightbox-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
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
  transition: background 0.18s ease, transform 0.18s ease;
  backdrop-filter: blur(12px);
}

.lightbox-btn:hover {
  background: rgba(255, 255, 255, 0.22);
}

.lightbox-btn:active {
  transform: scale(0.95);
}

.lightbox-btn.close {
  background: rgba(255, 255, 255, 0.18);
}

.lightbox-btn.danger {
  background: rgba(185, 28, 28, 0.32);
}

.lightbox-stage-wrap {
  min-height: 0;
  min-width: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  display: grid;
  place-items: center;
}

.lightbox-stage {
  width: min(100%, 820px);
  height: 100%;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 100%;
  padding: 10px;
  box-sizing: border-box;
  border-radius: 24px;
  overflow: hidden;
  display: grid;
  place-items: center;
  background: rgba(10, 14, 20, 0.72);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.28);
}

.lightbox-stage-scene {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  position: relative;
  overflow: hidden;
  will-change: transform;
  perspective: 1800px;
}

.lightbox-stage-track {
  position: relative;
  width: 100%;
  height: 100%;
  transform-style: preserve-3d;
}

.lightbox-stage-card {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 10px;
  box-sizing: border-box;
  transform-origin: center center;
  transition: transform 0.42s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.28s ease, filter 0.28s ease;
}

.lightbox-stage-card.active {
  transition-duration: 0.34s;
}

.lightbox-media {
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  object-position: center center;
  background: transparent;
  margin: auto;
  border-radius: 18px;
  box-shadow: 0 22px 40px rgba(0, 0, 0, 0.24);
}

.lightbox-strip-slot {
  position: relative;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 92px;
  display: grid;
  place-items: center;
  padding: 10px 18px 8px;
  box-sizing: border-box;
  overflow: hidden;
}

.lightbox-strip-slot::before,
.lightbox-strip-slot::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 56px;
  pointer-events: none;
  z-index: 3;
}

.lightbox-strip-slot::before {
  left: 0;
  background: linear-gradient(90deg, rgba(7, 10, 16, 0.82), rgba(7, 10, 16, 0));
}

.lightbox-strip-slot::after {
  right: 0;
  background: linear-gradient(270deg, rgba(7, 10, 16, 0.82), rgba(7, 10, 16, 0));
}

.lightbox-strip {
  --thumb-size: 72px;
  position: relative;
  width: min(100%, 520px);
  height: calc(var(--thumb-size) + 24px);
  box-sizing: border-box;
  border-radius: 28px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.12), rgba(255, 255, 255, 0.04));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 10px 26px rgba(0, 0, 0, 0.22);
  perspective: 1200px;
  overflow: hidden;
}

.lightbox-thumb {
  position: relative;
  width: var(--thumb-size);
  height: var(--thumb-size);
  position: absolute;
  left: calc(50% - (var(--thumb-size) / 2));
  top: calc(50% - (var(--thumb-size) / 2));
  border: none;
  border-radius: 18px;
  overflow: hidden;
  padding: 0;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.08);
  opacity: 0.68;
  transform-origin: center center;
  transform-style: preserve-3d;
  transition: opacity 0.32s ease, transform 0.42s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.32s ease, filter 0.32s ease;
}

.lightbox-thumb.active {
  opacity: 1;
}

.lightbox-thumb-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.lightbox-thumb::after {
  content: '';
  position: absolute;
  inset: auto 12px 8px;
  height: 3px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  transform: scaleX(0.2);
  transform-origin: center;
  opacity: 0;
  transition: transform 0.28s ease, opacity 0.28s ease;
}

.lightbox-thumb.active::after {
  opacity: 1;
  transform: scaleX(1);
}

.thumb-video-badge {
  position: absolute;
  left: 8px;
  bottom: 8px;
  width: 22px;
  height: 22px;
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
    padding: max(10px, env(safe-area-inset-top)) max(10px, env(safe-area-inset-right)) max(14px, env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left));
  }

  .lightbox-shell {
    width: 100%;
    height: 100%;
    gap: 10px;
  }

  .lightbox-toolbar {
    align-items: flex-start;
  }

  .lightbox-actions {
    gap: 6px;
  }

  .lightbox-btn {
    width: 40px;
    height: 40px;
  }

  .lightbox-stage {
    width: 100%;
    padding: 8px;
    border-radius: 18px;
  }

  .lightbox-strip-slot {
    width: 100%;
    min-height: 80px;
    padding-inline: 12px;
  }

  .lightbox-thumb {
    border-radius: 16px;
  }

  .lightbox-strip {
    --thumb-size: 64px;
    width: min(100%, 420px);
  }
}
</style>
