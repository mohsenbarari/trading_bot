<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
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
  (e: 'share', msgId: number): void
  (e: 'delete', msgId: number): void
}>()

const gestureStart = ref<{ x: number; y: number } | null>(null)
const gestureAxis = ref<'horizontal' | 'vertical' | null>(null)
const gestureSurface = ref<'stage' | 'strip' | null>(null)
const dragOffsetX = ref(0)
const dragOffsetY = ref(0)
const stageSceneRef = ref<HTMLElement | null>(null)
const suppressThumbClick = ref(false)
let suppressThumbClickTimer: number | null = null

const mediaZoomScale = ref(1)
const mediaZoomX = ref(0)
const mediaZoomY = ref(0)
const mediaGestureMode = ref<'pan' | 'pinch' | null>(null)
const imagePanStartX = ref(0)
const imagePanStartY = ref(0)
const pinchStartDistance = ref(0)
const pinchStartScale = ref(1)
const pinchStartCenterX = ref(0)
const pinchStartCenterY = ref(0)
const pinchStartOffsetX = ref(0)
const pinchStartOffsetY = ref(0)
let lastStageTap: { time: number; x: number; y: number } | null = null

const MAX_MEDIA_ZOOM = 4
const DOUBLE_TAP_ZOOM_SCALE = 2.4
const DOUBLE_TAP_MAX_DELAY = 280
const DOUBLE_TAP_MAX_DISTANCE = 24

const isActionMenuOpen = ref(false)
const isAlbumDownloadSheetOpen = ref(false)
const albumDownloadSelection = ref<number[]>([])

const currentItem = computed(() => {
  if (!props.lightboxMedia) return null
  return props.lightboxMedia.items[props.lightboxMedia.currentIndex] || null
})

function isZoomableLightboxItem(item: LightboxItem | null | undefined) {
  return item?.type === 'image' || item?.type === 'video'
}

const isCurrentMediaZoomable = computed(() => isZoomableLightboxItem(currentItem.value))
const isCurrentImageItem = computed(() => currentItem.value?.type === 'image')
const isCurrentMediaZoomed = computed(() => isCurrentMediaZoomable.value && mediaZoomScale.value > 1.01)

const hasAlbumStrip = computed(() => {
  return Boolean(props.lightboxMedia?.albumId) && (props.lightboxMedia?.items.length || 0) > 1
})

const canDeleteCurrentItem = computed(() => {
  const item = currentItem.value
  if (!item || item.senderId !== props.currentUserId) return false
  const createdAt = new Date(item.createdAt).getTime()
  return Number.isFinite(createdAt) && (Date.now() - createdAt) <= 48 * 60 * 60 * 1000
})

const selectedAlbumDownloadCount = computed(() => albumDownloadSelection.value.length)

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

const has3DSiblings = computed(() => (props.lightboxMedia?.items.length ?? 0) > 1)
const isStage3DActive = computed(() => {
  if (!has3DSiblings.value) return false
  if (gestureSurface.value !== 'stage') return false
  if (gestureAxis.value !== 'horizontal') return false
  return Math.abs(dragOffsetX.value) > 0.5
})

const sceneTransform = computed<CSSProperties>(() => {
  const verticalProgress = Math.min(Math.abs(dragOffsetY.value) / 240, 1)
  const scale = gestureAxis.value === 'vertical'
    ? Math.max(0.88, 1 - verticalProgress * 0.12)
    : 1

  // Avoid emitting any inline transform when the scene is at rest. A no-op
  // translate3d(0,0,0) scale(1) still creates an extra GPU compositing layer
  // on top of `perspective`, which can make the active media render as if
  // zoomed into a corner on some browsers/devices.
  if (Math.abs(dragOffsetY.value) < 0.5 && scale === 1) {
    return {}
  }

  return {
    transform: `translate3d(0, ${dragOffsetY.value}px, 0) scale(${scale})`,
    transition: gestureStart.value && gestureAxis.value === 'vertical' ? 'none' : 'transform 0.3s cubic-bezier(0.22, 1, 0.36, 1)'
  }
})

watch(() => props.lightboxMedia?.currentIndex, () => {
  isActionMenuOpen.value = false
  resetMediaZoom()
  resetGesture()
})

watch(() => props.lightboxMedia?.albumId, () => {
  isActionMenuOpen.value = false
  isAlbumDownloadSheetOpen.value = false
  albumDownloadSelection.value = []
  resetMediaZoom()
  resetGesture()
})

onUnmounted(() => {
  if (suppressThumbClickTimer !== null) {
    window.clearTimeout(suppressThumbClickTimer)
    suppressThumbClickTimer = null
  }
})

function resetGesture() {
  gestureStart.value = null
  gestureAxis.value = null
  gestureSurface.value = null
  dragOffsetX.value = 0
  dragOffsetY.value = 0
  mediaGestureMode.value = null
  imagePanStartX.value = mediaZoomX.value
  imagePanStartY.value = mediaZoomY.value
  pinchStartDistance.value = 0
  pinchStartScale.value = mediaZoomScale.value
  pinchStartCenterX.value = 0
  pinchStartCenterY.value = 0
  pinchStartOffsetX.value = mediaZoomX.value
  pinchStartOffsetY.value = mediaZoomY.value
}

function resetMediaZoom() {
  mediaZoomScale.value = 1
  mediaZoomX.value = 0
  mediaZoomY.value = 0
  lastStageTap = null
}

function getTouchDistance(touches: TouchList) {
  if (touches.length < 2) return 0
  const first = touches[0]
  const second = touches[1]
  if (!first || !second) return 0
  return Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY)
}

function getTouchCenter(touches: TouchList) {
  if (touches.length < 2) return null
  const first = touches[0]
  const second = touches[1]
  if (!first || !second) return null

  return {
    x: (first.clientX + second.clientX) / 2,
    y: (first.clientY + second.clientY) / 2,
  }
}

function getActiveMediaMetrics() {
  const mediaEl = stageSceneRef.value?.querySelector('.lightbox-stage-card.active .lightbox-media') as HTMLElement | null
  const stageEl = stageSceneRef.value

  if (!mediaEl || !stageEl) return null

  const mediaRect = mediaEl.getBoundingClientRect()

  return {
    mediaWidth: mediaEl.clientWidth || mediaRect.width,
    mediaHeight: mediaEl.clientHeight || mediaRect.height,
    mediaRect,
    stageWidth: stageEl.clientWidth,
    stageHeight: stageEl.clientHeight,
  }
}

function clampMediaOffset(nextX: number, nextY: number, nextScale = mediaZoomScale.value) {
  if (!isCurrentMediaZoomable.value || nextScale <= 1) {
    return { x: 0, y: 0 }
  }

  const metrics = getActiveMediaMetrics()
  if (!metrics) {
    return { x: nextX, y: nextY }
  }

  const maxX = Math.max(0, (metrics.mediaWidth * nextScale - metrics.stageWidth) / 2)
  const maxY = Math.max(0, (metrics.mediaHeight * nextScale - metrics.stageHeight) / 2)

  return {
    x: clampNumber(nextX, -maxX, maxX),
    y: clampNumber(nextY, -maxY, maxY),
  }
}

function applyMediaZoom(nextScale: number, nextX: number, nextY: number) {
  const normalizedScale = clampNumber(nextScale, 1, MAX_MEDIA_ZOOM)
  if (normalizedScale <= 1.01) {
    resetMediaZoom()
    return
  }

  const clamped = clampMediaOffset(nextX, nextY, normalizedScale)
  mediaZoomScale.value = normalizedScale
  mediaZoomX.value = clamped.x
  mediaZoomY.value = clamped.y
}

function toggleCurrentMediaZoom(clientX: number, clientY: number) {
  if (!isCurrentMediaZoomable.value) return

  if (isCurrentMediaZoomed.value) {
    resetMediaZoom()
    return
  }

  const metrics = getActiveMediaMetrics()
  if (!metrics) {
    applyMediaZoom(DOUBLE_TAP_ZOOM_SCALE, 0, 0)
    return
  }

  const centerX = metrics.mediaRect.left + metrics.mediaRect.width / 2
  const centerY = metrics.mediaRect.top + metrics.mediaRect.height / 2
  const offsetX = -(clientX - centerX) * (DOUBLE_TAP_ZOOM_SCALE - 1)
  const offsetY = -(clientY - centerY) * (DOUBLE_TAP_ZOOM_SCALE - 1)
  applyMediaZoom(DOUBLE_TAP_ZOOM_SCALE, offsetX, offsetY)
}

function suppressNextThumbClick() {
  suppressThumbClick.value = true

  if (suppressThumbClickTimer !== null) {
    window.clearTimeout(suppressThumbClickTimer)
  }

  suppressThumbClickTimer = window.setTimeout(() => {
    suppressThumbClick.value = false
    suppressThumbClickTimer = null
  }, 240)
}

function handleSaveMedia() {
  const item = currentItem.value
  if (!item) return

  downloadLightboxItem(item, props.lightboxMedia?.currentIndex ?? 0)
}

function buildDownloadFileName(item: LightboxItem, index: number) {
  const fallbackExt = item.type === 'video' ? 'mp4' : 'jpg'
  const prefix = String(index + 1).padStart(2, '0')
  return `${prefix}_${item.fileId || `media_${Date.now()}_${index + 1}`}.${fallbackExt}`
}

function downloadLightboxItem(item: LightboxItem, index: number) {
  const a = document.createElement('a')
  a.href = item.url
  a.download = buildDownloadFileName(item, index)
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function toggleActionMenu() {
  isActionMenuOpen.value = !isActionMenuOpen.value
}

function openAlbumDownloadSheet() {
  if (!props.lightboxMedia || !hasAlbumStrip.value) return

  albumDownloadSelection.value = props.lightboxMedia.items.map((item) => item.msgId)
  isActionMenuOpen.value = false
  isAlbumDownloadSheetOpen.value = true
}

function handleMenuDownloadCurrent() {
  isActionMenuOpen.value = false
  handleSaveMedia()
}

function handleMenuDeleteCurrent() {
  isActionMenuOpen.value = false
  emitForCurrent('delete')
}

function closeAlbumDownloadSheet() {
  isAlbumDownloadSheetOpen.value = false
}

function handleOverlayClick() {
  if (isAlbumDownloadSheetOpen.value) {
    closeAlbumDownloadSheet()
    return
  }

  if (isActionMenuOpen.value) {
    isActionMenuOpen.value = false
    return
  }

  emit('close')
}

function setAlbumDownloadSelection(selectAll: boolean) {
  if (!props.lightboxMedia) return
  albumDownloadSelection.value = selectAll
    ? props.lightboxMedia.items.map((item) => item.msgId)
    : []
}

function isAlbumDownloadSelected(msgId: number) {
  return albumDownloadSelection.value.includes(msgId)
}

function toggleAlbumDownloadSelection(msgId: number) {
  if (isAlbumDownloadSelected(msgId)) {
    albumDownloadSelection.value = albumDownloadSelection.value.filter((id) => id !== msgId)
    return
  }

  albumDownloadSelection.value = [...albumDownloadSelection.value, msgId]
}

function handleAlbumDownloadConfirm() {
  if (!props.lightboxMedia) return

  const selectedItems = props.lightboxMedia.items.filter((item) => albumDownloadSelection.value.includes(item.msgId))
  if (!selectedItems.length) return

  selectedItems.forEach((item, index) => {
    window.setTimeout(() => {
      downloadLightboxItem(item, index)
    }, index * 120)
  })

  closeAlbumDownloadSheet()
}

function emitForCurrent(action: 'reply' | 'forward' | 'share' | 'delete') {
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
  if (action === 'share') {
    emit('share', item.msgId)
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

function handleThumbClick(index: number) {
  if (suppressThumbClick.value) {
    suppressThumbClick.value = false
    return
  }

  navigateTo(index)
}

function getStripThumbSrc(item: LightboxItem, index: number) {
  if (item.type === 'video') {
    return item.thumbnail || item.url
  }

  const activeIndex = props.lightboxMedia?.currentIndex ?? 0
  const distance = Math.abs(index - activeIndex)

  if (distance <= 4) {
    return item.url || item.thumbnail
  }

  return item.thumbnail || item.url
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

  // Keep the active media completely flat at rest. We intentionally do NOT
  // emit a transform here: any inline 3D transform (even an identity one)
  // pulls the active card into the parent's `perspective: 1800px /
  // preserve-3d` 3D rendering context and on some browsers ends up rendering
  // the contained <img> as a zoomed-in slice of itself. Leaving transform
  // unset lets the card render as a normal 2D layer.
  if (index === activeIndex && Math.abs(dragRatio) < 0.001) {
    return {
      opacity: '1',
      filter: 'none',
      zIndex: '200',
      pointerEvents: 'auto',
    }
  }

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

function getActiveMediaStyle(item: LightboxItem, index: number): CSSProperties {
  if (!isZoomableLightboxItem(item) || index !== (props.lightboxMedia?.currentIndex ?? -1)) {
    return {}
  }

  if (!isCurrentMediaZoomed.value && Math.abs(mediaZoomX.value) < 0.01 && Math.abs(mediaZoomY.value) < 0.01) {
    return {}
  }

  return {
    transform: `translate3d(${mediaZoomX.value}px, ${mediaZoomY.value}px, 0) scale(${mediaZoomScale.value})`,
    transition: mediaGestureMode.value ? 'none' : 'transform 0.24s cubic-bezier(0.22, 1, 0.36, 1)',
  }
}

function handleMediaDoubleClick(event: MouseEvent, item: LightboxItem, index: number) {
  if (item.type !== 'image' || index !== (props.lightboxMedia?.currentIndex ?? -1)) return
  toggleCurrentMediaZoom(event.clientX, event.clientY)
}

function handleTouchStart(event: TouchEvent, surface: 'stage' | 'strip' = 'stage') {
  if (surface === 'stage' && isCurrentMediaZoomable.value && event.touches.length === 2) {
    const distance = getTouchDistance(event.touches)
    const center = getTouchCenter(event.touches)
    if (!distance || !center) return

    gestureStart.value = null
    gestureAxis.value = null
    gestureSurface.value = surface
    dragOffsetX.value = 0
    dragOffsetY.value = 0
    mediaGestureMode.value = 'pinch'
    pinchStartDistance.value = distance
    pinchStartScale.value = mediaZoomScale.value
    pinchStartCenterX.value = center.x
    pinchStartCenterY.value = center.y
    pinchStartOffsetX.value = mediaZoomX.value
    pinchStartOffsetY.value = mediaZoomY.value
    suppressThumbClick.value = false
    return
  }

  if (event.touches.length !== 1) return
  const touch = event.touches[0]
  if (!touch) return

  if (surface === 'stage' && isCurrentMediaZoomed.value) {
    gestureStart.value = { x: touch.clientX, y: touch.clientY }
    gestureSurface.value = surface
    gestureAxis.value = null
    dragOffsetX.value = 0
    dragOffsetY.value = 0
    mediaGestureMode.value = 'pan'
    imagePanStartX.value = mediaZoomX.value
    imagePanStartY.value = mediaZoomY.value
    suppressThumbClick.value = false
    return
  }

  gestureStart.value = { x: touch.clientX, y: touch.clientY }
  gestureSurface.value = surface
  gestureAxis.value = null
  dragOffsetX.value = 0
  dragOffsetY.value = 0
  suppressThumbClick.value = false
}

function handleTouchMove(event: TouchEvent) {
  if (mediaGestureMode.value === 'pinch') {
    if (!isCurrentMediaZoomable.value || event.touches.length < 2 || !pinchStartDistance.value) return
    const distance = getTouchDistance(event.touches)
    const center = getTouchCenter(event.touches)
    if (!distance || !center) return
    if (event.cancelable) {
      event.preventDefault()
    }

    const nextScale = pinchStartScale.value * (distance / pinchStartDistance.value)
    const nextX = pinchStartOffsetX.value + (center.x - pinchStartCenterX.value)
    const nextY = pinchStartOffsetY.value + (center.y - pinchStartCenterY.value)
    applyMediaZoom(nextScale, nextX, nextY)
    return
  }

  if (mediaGestureMode.value === 'pan') {
    const touch = event.touches[0]
    const start = gestureStart.value
    if (!touch || !start || !isCurrentMediaZoomed.value) return
    if (event.cancelable) {
      event.preventDefault()
    }

    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    applyMediaZoom(mediaZoomScale.value, imagePanStartX.value + dx, imagePanStartY.value + dy)
    return
  }

  const touch = event.touches[0]
  const start = gestureStart.value
  if (!touch || !start) return

  const dx = touch.clientX - start.x
  const dy = touch.clientY - start.y
  const surface = gestureSurface.value || 'stage'

  if (!gestureAxis.value) {
    if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return

    if (surface === 'strip') {
      if (Math.abs(dx) <= Math.abs(dy)) return
      gestureAxis.value = 'horizontal'
    } else {
      gestureAxis.value = Math.abs(dx) >= Math.abs(dy) ? 'horizontal' : 'vertical'
    }
  }

  if (gestureAxis.value === 'horizontal') {
    if ((props.lightboxMedia?.items.length || 0) <= 1) return
    dragOffsetX.value = dx
    dragOffsetY.value = 0
    if (surface === 'strip' && Math.abs(dx) > 12 && !suppressThumbClick.value) {
      suppressNextThumbClick()
    }
  } else {
    if (surface === 'strip') return
    dragOffsetY.value = dy
    dragOffsetX.value = 0
  }
}

function handleTouchEnd(event: TouchEvent) {
  if (mediaGestureMode.value === 'pinch') {
    if (event.touches.length === 1 && isCurrentMediaZoomed.value) {
      const touch = event.touches[0]
      if (touch) {
        gestureStart.value = { x: touch.clientX, y: touch.clientY }
        gestureSurface.value = 'stage'
        gestureAxis.value = null
        dragOffsetX.value = 0
        dragOffsetY.value = 0
        mediaGestureMode.value = 'pan'
        imagePanStartX.value = mediaZoomX.value
        imagePanStartY.value = mediaZoomY.value
        pinchStartDistance.value = 0
        pinchStartScale.value = mediaZoomScale.value
        return
      }
    }

    if (mediaZoomScale.value <= 1.01) {
      resetMediaZoom()
    }
    resetGesture()
    return
  }

  if (mediaGestureMode.value === 'pan') {
    if (mediaZoomScale.value <= 1.01) {
      resetMediaZoom()
    }
    resetGesture()
    return
  }

  if (!props.lightboxMedia || !gestureStart.value) {
    resetGesture()
    return
  }

  const surface = gestureSurface.value

  if (surface === 'stage' && !gestureAxis.value && isCurrentImageItem.value && event.changedTouches.length === 1) {
    const touch = event.changedTouches[0]
    if (touch) {
      const now = Date.now()
      if (
        lastStageTap
        && (now - lastStageTap.time) <= DOUBLE_TAP_MAX_DELAY
        && Math.hypot(lastStageTap.x - touch.clientX, lastStageTap.y - touch.clientY) <= DOUBLE_TAP_MAX_DISTANCE
      ) {
        lastStageTap = null
        toggleCurrentMediaZoom(touch.clientX, touch.clientY)
        resetGesture()
        return
      }

      lastStageTap = {
        time: now,
        x: touch.clientX,
        y: touch.clientY,
      }
    }
  } else if (gestureAxis.value) {
    lastStageTap = null
  }

  if (gestureAxis.value === 'horizontal') {
    if (dragOffsetX.value > 60) {
      navigateRelative(-1)
    } else if (dragOffsetX.value < -60) {
      navigateRelative(1)
    }
  } else if (surface === 'stage' && gestureAxis.value === 'vertical' && Math.abs(dragOffsetY.value) > 90) {
    emit('close')
  }

  resetGesture()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="lightbox">
      <div v-if="lightboxMedia && currentItem" class="lightbox-overlay" @click="handleOverlayClick">
        <div class="lightbox-shell">
          <div class="lightbox-toolbar" @click.stop>
            <div class="lightbox-actions">
              <div class="lightbox-action-group lightbox-action-group-primary">
                <button class="lightbox-btn lightbox-btn-labeled lightbox-btn-emphasis" @click.stop="emitForCurrent('reply')" title="پاسخ">
                  <span class="lightbox-btn-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 14 4 9 9 4"></polyline><path d="M20 20v-7a4 4 0 0 0-4-4H4"></path></svg>
                  </span>
                  <span class="lightbox-btn-label">پاسخ</span>
                </button>
                <button class="lightbox-btn lightbox-btn-labeled lightbox-btn-emphasis" @click.stop="emitForCurrent('forward')" title="هدایت">
                  <span class="lightbox-btn-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 14 20 9 15 4"></polyline><path d="M4 20v-7a4 4 0 0 1 4-4h12"></path></svg>
                  </span>
                  <span class="lightbox-btn-label">هدایت</span>
                </button>
                <button class="lightbox-btn lightbox-btn-labeled lightbox-btn-emphasis" @click.stop="emitForCurrent('share')" title="اشتراک‌گذاری">
                  <span class="lightbox-btn-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>
                  </span>
                  <span class="lightbox-btn-label">اشتراک</span>
                </button>
              </div>

              <div class="lightbox-action-group lightbox-action-group-utility">
                <div class="lightbox-menu-wrap">
                  <button class="lightbox-btn" :class="{ active: isActionMenuOpen }" @click.stop="toggleActionMenu" title="گزینه‌های بیشتر">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><circle cx="12" cy="5" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="12" cy="19" r="1.8" /></svg>
                  </button>
                  <div v-if="isActionMenuOpen" class="lightbox-menu-panel" @click.stop>
                    <button class="lightbox-menu-item" @click.stop="handleMenuDownloadCurrent">
                      دانلود
                    </button>
                    <button v-if="hasAlbumStrip" class="lightbox-menu-item" @click.stop="openAlbumDownloadSheet">
                      دانلود آلبوم
                    </button>
                    <button v-if="canDeleteCurrentItem" class="lightbox-menu-item lightbox-menu-item-danger" @click.stop="handleMenuDeleteCurrent">
                      حذف
                    </button>
                  </div>
                </div>
                <button class="lightbox-btn close" @click.stop="emit('close')" title="بستن">
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
              </div>
            </div>
          </div>

          <div class="lightbox-stage-wrap">
            <div
              class="lightbox-stage"
              @touchstart="handleTouchStart($event, 'stage')"
              @touchmove="handleTouchMove"
              @touchend="handleTouchEnd($event)"
              @touchcancel="handleTouchEnd($event)"
            >
              <div
                ref="stageSceneRef"
                class="lightbox-stage-scene"
                :class="{ 'is-3d-active': isStage3DActive }"
                :style="sceneTransform"
              >
                <div class="lightbox-stage-track" :class="{ 'is-3d-active': isStage3DActive }">
                  <div
                    v-for="(item, index) in lightboxMedia.items"
                    v-show="shouldRenderStageItem(index)"
                    :key="item.msgId"
                    class="lightbox-stage-card"
                    :class="{ active: index === lightboxMedia.currentIndex }"
                    :style="getStageItemStyle(index)"
                  >
                      <div class="lightbox-media-frame">
                        <div v-if="lightboxMedia.items.length > 1 && index === lightboxMedia.currentIndex" class="lightbox-stage-counter">
                          {{ index + 1 }} / {{ lightboxMedia.items.length }}
                        </div>
                        <img
                          v-if="item.type === 'image'"
                          :src="item.url"
                          :class="['lightbox-media', { 'is-zoomable': index === lightboxMedia.currentIndex, 'is-zoomed': index === lightboxMedia.currentIndex && isCurrentMediaZoomed }]"
                          :style="getActiveMediaStyle(item, index)"
                          alt="مدیا"
                          draggable="false"
                          @click.stop
                          @dblclick.stop="handleMediaDoubleClick($event, item, index)"
                        />
                        <video
                          v-else
                          :src="item.url"
                          :class="['lightbox-media', { 'is-zoomable': index === lightboxMedia.currentIndex, 'is-zoomed': index === lightboxMedia.currentIndex && isCurrentMediaZoomed }]"
                          :style="getActiveMediaStyle(item, index)"
                          :controls="index === lightboxMedia.currentIndex && !isCurrentMediaZoomed"
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
          </div>

            <div v-if="hasAlbumStrip" class="lightbox-strip-slot" @click.stop>
            <div
              class="lightbox-strip"
              @touchstart="handleTouchStart($event, 'strip')"
              @touchmove="handleTouchMove"
              @touchend="handleTouchEnd($event)"
              @touchcancel="handleTouchEnd($event)"
            >
              <button
                v-for="(item, index) in lightboxMedia.items"
                :key="item.msgId"
                class="lightbox-thumb"
                :class="{ active: index === lightboxMedia.currentIndex }"
                :style="getThumbStyle(index)"
                :aria-current="index === lightboxMedia.currentIndex ? 'true' : 'false'"
                @click.stop="handleThumbClick(index)"
              >
                <img :src="getStripThumbSrc(item, index)" alt="thumbnail" class="lightbox-thumb-image" />
                <span v-if="item.type === 'video'" class="thumb-video-badge">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="white"><path d="M8 5v14l11-7z" /></svg>
                </span>
              </button>
            </div>
          </div>

          <div v-if="lightboxMedia && isAlbumDownloadSheetOpen" class="album-download-backdrop" @click.stop="closeAlbumDownloadSheet">
            <div class="album-download-sheet" @click.stop>
              <div class="album-download-header">
                <div>
                  <div class="album-download-title">دانلود آلبوم</div>
                  <div class="album-download-subtitle">{{ selectedAlbumDownloadCount }} از {{ lightboxMedia.items.length }} مدیا انتخاب شده</div>
                </div>
                <button class="album-download-close" @click.stop="closeAlbumDownloadSheet" title="بستن">
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
              </div>

              <div class="album-download-toolbar">
                <button class="album-download-chip" @click.stop="setAlbumDownloadSelection(true)">انتخاب همه</button>
                <button class="album-download-chip" @click.stop="setAlbumDownloadSelection(false)">برداشتن همه</button>
              </div>

              <div class="album-download-list">
                <label
                  v-for="(item, index) in lightboxMedia.items"
                  :key="`${item.msgId}-download`"
                  class="album-download-item"
                >
                  <input
                    class="album-download-checkbox"
                    type="checkbox"
                    :checked="isAlbumDownloadSelected(item.msgId)"
                    @change="toggleAlbumDownloadSelection(item.msgId)"
                  />
                  <span class="album-download-thumb-wrap">
                    <img :src="item.thumbnail || item.url" alt="thumbnail" class="album-download-thumb" />
                    <span v-if="item.type === 'video'" class="album-download-kind">ویدئو</span>
                  </span>
                  <span class="album-download-meta">
                    <span class="album-download-name">{{ item.type === 'video' ? `ویدئو ${index + 1}` : `تصویر ${index + 1}` }}</span>
                    <span class="album-download-current">{{ index === lightboxMedia.currentIndex ? 'در حال نمایش' : `آیتم ${index + 1}` }}</span>
                  </span>
                </label>
              </div>

              <div class="album-download-footer">
                <button class="album-download-secondary" @click.stop="closeAlbumDownloadSheet">انصراف</button>
                <button class="album-download-primary" :disabled="selectedAlbumDownloadCount === 0" @click.stop="handleAlbumDownloadConfirm">
                  دانلود {{ selectedAlbumDownloadCount > 0 ? `(${selectedAlbumDownloadCount})` : '' }}
                </button>
              </div>
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
  padding: max(8px, env(safe-area-inset-top)) max(8px, env(safe-area-inset-right)) max(10px, env(safe-area-inset-bottom)) max(8px, env(safe-area-inset-left));
  background: rgba(7, 10, 16, 0.76);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(18px);
}

.lightbox-shell {
  width: min(100%, 1600px);
  max-width: 100%;
  min-width: 0;
  height: 100%;
  max-height: 100%;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 8px;
  color: white;
}

.lightbox-toolbar {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: nowrap;
  gap: 10px;
  min-width: 0;
  overflow: visible;
}

.lightbox-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: nowrap;
  gap: 10px;
  flex: 1 1 auto;
  min-width: 0;
}

.lightbox-action-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
  flex: 0 0 auto;
}

.lightbox-action-group-primary {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(12px);
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

.lightbox-btn-labeled {
  width: auto;
  min-width: 94px;
  padding-inline: 14px;
  border-radius: 999px;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  line-height: 1;
}

.lightbox-btn-emphasis {
  background: rgba(255, 255, 255, 0.18);
}

.lightbox-btn-label {
  white-space: nowrap;
}

.lightbox-btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.lightbox-btn:hover {
  background: rgba(255, 255, 255, 0.22);
}

.lightbox-btn:active {
  transform: scale(0.95);
}

.lightbox-btn.active {
  background: rgba(255, 255, 255, 0.24);
}

.lightbox-btn.close {
  background: rgba(255, 255, 255, 0.18);
}

.lightbox-btn.danger {
  background: rgba(185, 28, 28, 0.32);
}

.lightbox-menu-wrap {
  position: relative;
}

.lightbox-menu-panel {
  position: absolute;
  top: calc(100% + 10px);
  inset-inline-end: 0;
  min-width: 164px;
  padding: 8px;
  border-radius: 18px;
  background: rgba(12, 17, 24, 0.94);
  box-shadow: 0 18px 38px rgba(0, 0, 0, 0.34);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  z-index: 5;
}

.lightbox-menu-item {
  width: 100%;
  border: none;
  background: transparent;
  color: white;
  border-radius: 12px;
  padding: 10px 12px;
  text-align: right;
  cursor: pointer;
  font-size: 14px;
}

.lightbox-menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.lightbox-menu-item-danger {
  color: #fecaca;
}

.lightbox-menu-item-danger:hover {
  background: rgba(185, 28, 28, 0.18);
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
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  max-height: 100%;
  padding: clamp(2px, 0.4vw, 8px);
  box-sizing: border-box;
  border-radius: 28px;
  overflow: hidden;
  display: grid;
  place-items: center;
  background: rgba(10, 14, 20, 0.46);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.2);
}

.lightbox-stage-scene {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  position: relative;
  overflow: hidden;
  will-change: transform;
}

.lightbox-stage-scene.is-3d-active {
  perspective: 1800px;
}

.lightbox-stage-track {
  position: relative;
  width: 100%;
  height: 100%;
}

.lightbox-stage-track.is-3d-active {
  transform-style: preserve-3d;
}

.lightbox-stage-card {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: clamp(4px, 0.55vw, 10px);
  box-sizing: border-box;
  overflow: hidden;
  min-width: 0;
  min-height: 0;
  transform-origin: center center;
  transition: transform 0.42s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.28s ease, filter 0.28s ease;
}

.lightbox-stage-card.active {
  transition-duration: 0.34s;
}

.lightbox-media-frame {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 100%;
  max-height: 100%;
  min-width: 0;
  min-height: 0;
}

.lightbox-stage-counter {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(10, 14, 20, 0.6);
  color: white;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.02em;
  backdrop-filter: blur(12px);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.2);
  pointer-events: none;
}

.lightbox-media {
  display: block;
  /* width/height auto with max-width/max-height resolved against the flex
   * container (the stage card) reliably letterboxes the media. We explicitly
   * set min-width/min-height: 0 so the flex item can shrink below its
   * intrinsic size when the source image is larger than the stage. Without
   * this, large or extreme-aspect crops overflow the card and the parent's
   * overflow:hidden makes only a corner visible ("zoomed corner" bug). */
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  min-width: 0;
  min-height: 0;
  flex: 0 1 auto;
  object-fit: contain;
  object-position: center center;
  background: transparent;
  border-radius: 18px;
  box-shadow: 0 22px 40px rgba(0, 0, 0, 0.24);
}

.lightbox-media.is-zoomable {
  touch-action: none;
  user-select: none;
  -webkit-user-drag: none;
  cursor: zoom-in;
  will-change: transform;
}

.lightbox-media.is-zoomed {
  cursor: grab;
}

.lightbox-media.is-zoomed:active {
  cursor: grabbing;
}

.lightbox-strip-slot {
  position: relative;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 82px;
  display: grid;
  place-items: center;
  padding: 4px 12px 2px;
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
  width: min(100%, 640px);
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
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.thumb-video-badge::before {
  content: '';
  position: absolute;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.56);
  backdrop-filter: blur(8px);
}

.thumb-video-badge svg {
  position: relative;
  z-index: 1;
  transform: translateX(1px);
  filter: drop-shadow(0 1px 4px rgba(0, 0, 0, 0.28));
}

.album-download-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(6, 9, 14, 0.42);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 20px;
  z-index: 10001;
}

.album-download-sheet {
  width: min(100%, 560px);
  max-height: min(76vh, 720px);
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 14px;
  background: rgba(10, 14, 20, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 28px;
  padding: 18px;
  box-shadow: 0 28px 52px rgba(0, 0, 0, 0.38);
  backdrop-filter: blur(22px);
}

.album-download-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.album-download-title {
  font-size: 18px;
  font-weight: 700;
}

.album-download-subtitle {
  margin-top: 6px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.68);
}

.album-download-close {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.08);
  color: white;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.album-download-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.album-download-chip {
  border: none;
  background: rgba(255, 255, 255, 0.1);
  color: white;
  padding: 9px 14px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 13px;
}

.album-download-list {
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-inline-end: 4px;
}

.album-download-item {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.05);
  cursor: pointer;
}

.album-download-checkbox {
  width: 18px;
  height: 18px;
  accent-color: #59b4ff;
}

.album-download-thumb-wrap {
  position: relative;
  width: 58px;
  height: 58px;
  border-radius: 16px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.08);
  flex-shrink: 0;
}

.album-download-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.album-download-kind {
  position: absolute;
  left: 6px;
  bottom: 6px;
  padding: 3px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.56);
  font-size: 10px;
}

.album-download-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.album-download-name {
  font-size: 14px;
  font-weight: 600;
}

.album-download-current {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.64);
}

.album-download-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.album-download-secondary,
.album-download-primary {
  border: none;
  border-radius: 16px;
  padding: 12px 18px;
  cursor: pointer;
  font-weight: 600;
}

.album-download-secondary {
  background: rgba(255, 255, 255, 0.08);
  color: white;
}

.album-download-primary {
  background: linear-gradient(135deg, #4ea8ff, #6bc7ff);
  color: #081019;
}

.album-download-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
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
    padding: max(6px, env(safe-area-inset-top)) max(6px, env(safe-area-inset-right)) max(8px, env(safe-area-inset-bottom)) max(6px, env(safe-area-inset-left));
  }

  .lightbox-shell {
    width: 100%;
    height: 100%;
    gap: 6px;
  }

  .lightbox-toolbar {
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
  }

  .lightbox-actions {
    width: 100%;
    justify-content: space-between;
    gap: 8px;
  }

  .lightbox-action-group {
    flex-wrap: nowrap;
  }

  .lightbox-action-group-primary {
    gap: 6px;
    padding: 4px;
  }

  .lightbox-btn {
    width: 40px;
    height: 40px;
  }

  .lightbox-btn-labeled {
    min-width: 40px;
    width: 40px;
    padding-inline: 0;
    font-size: 0;
    border-radius: 50%;
  }

  .lightbox-btn-label {
    display: none;
  }

  .lightbox-stage {
    width: 100%;
    padding: 2px;
    border-radius: 20px;
  }

  .lightbox-stage-card {
    padding: 4px;
  }

  .lightbox-stage-counter {
    top: 10px;
    right: 10px;
    padding: 7px 10px;
    font-size: 11px;
  }

  .lightbox-menu-panel {
    inset-inline-end: 0;
  }

  .lightbox-strip-slot {
    width: 100%;
    min-height: 74px;
    padding-inline: 8px;
  }

  .lightbox-thumb {
    border-radius: 16px;
  }

  .lightbox-strip {
    --thumb-size: 60px;
    width: min(100%, 520px);
  }

  .album-download-backdrop {
    padding: 10px;
  }

  .album-download-sheet {
    width: 100%;
    max-height: min(82vh, 720px);
    border-radius: 24px;
    padding: 16px;
  }

  .album-download-item {
    grid-template-columns: auto auto minmax(0, 1fr);
    padding: 10px;
  }
}
</style>
