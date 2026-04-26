<template>
  <teleport to="body">
    <input v-if="modelValue" ref="cameraPhotoInput" type="file" accept="image/*" capture="environment" style="display:none" @change="onNativeCameraFile" />
    <input v-if="modelValue" ref="cameraVideoInput" type="file" accept="video/*" capture="environment" style="display:none" @change="onNativeCameraFile" />

    <!-- Backdrop -->
    <transition name="fade">
      <div v-if="modelValue && !showCameraCapture" class="attachment-backdrop" @click="close"></div>
    </transition>

    <transition name="fade">
      <div v-if="modelValue && showCameraCapture" class="camera-capture-overlay">
        <div class="camera-capture-shell">
          <div class="camera-topbar">
            <button class="camera-icon-btn" @click="closeCameraCapture" title="بازگشت">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="15 18 9 12 15 6"></polyline>
              </svg>
            </button>

            <div class="camera-mode-switch">
              <button
                class="camera-mode-btn"
                :class="{ active: cameraMode === 'photo' }"
                :disabled="isRecording"
                @click="setCameraMode('photo')"
              >
                عکس
              </button>
              <button
                class="camera-mode-btn"
                :class="{ active: cameraMode === 'video' }"
                :disabled="isRecording"
                @click="setCameraMode('video')"
              >
                ویدئو
              </button>
            </div>

            <button class="camera-icon-btn" @click="toggleFacingMode" :disabled="isRecording || isCameraStarting || isUsingNativeCameraFallback" title="تغییر دوربین">
              <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17 1l4 4-4 4"></path>
                <path d="M3 11V9a4 4 0 0 1 4-4h14"></path>
                <path d="M7 23l-4-4 4-4"></path>
                <path d="M21 13v2a4 4 0 0 1-4 4H3"></path>
              </svg>
            </button>
          </div>

          <div class="camera-preview-frame">
            <video
              ref="cameraPreviewRef"
              class="camera-preview"
              autoplay
              playsinline
              muted
            ></video>

            <div v-if="isCameraStarting" class="camera-status-overlay">
              در حال آماده‌سازی دوربین...
            </div>

            <div v-else-if="isUsingNativeCameraFallback" class="camera-status-overlay native">
              <div>{{ nativeCameraFallbackTitle }}</div>
              <div class="camera-fallback-hint">{{ nativeCameraFallbackHint }}</div>
            </div>

            <div v-else-if="cameraError" class="camera-status-overlay error">
              <div>{{ cameraError }}</div>
              <button class="camera-error-btn" @click="startCameraStream">تلاش مجدد</button>
            </div>

            <div v-if="isRecording" class="camera-recording-badge">
              <span class="recording-dot"></span>
              {{ formattedRecordingTime }}
            </div>

            <div v-if="hasCameraZoomControl" class="camera-zoom-panel">
              <button
                type="button"
                class="camera-zoom-btn"
                :disabled="!canZoomOut"
                @click="nudgeCameraZoom(-1)"
                title="کاهش زوم"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
              </button>

              <div class="camera-zoom-slider-wrap">
                <div class="camera-zoom-label">{{ cameraZoomDisplay }}</div>
                <input
                  class="camera-zoom-slider"
                  type="range"
                  :min="cameraZoomCapability?.min ?? 1"
                  :max="cameraZoomCapability?.max ?? 1"
                  :step="cameraZoomCapability?.step ?? 0.1"
                  :value="cameraZoomValue"
                  @input="handleCameraZoomInput"
                />
              </div>

              <button
                type="button"
                class="camera-zoom-btn"
                :disabled="!canZoomIn"
                @click="nudgeCameraZoom(1)"
                title="افزایش زوم"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
              </button>
            </div>

            <div v-if="hasCapturedMediaQueue" class="camera-captured-strip">
              <div
                v-for="item in capturedCameraMedia"
                :key="item.id"
                class="camera-captured-item"
                :class="{ editable: item.type === 'photo' }"
                :title="item.type === 'photo' ? 'ویرایش عکس' : 'پیش‌نمایش ویدئو'"
                @click="item.type === 'photo' ? editCapturedMedia(item.id) : undefined"
              >
                <img
                  v-if="item.type === 'photo'"
                  :src="item.previewUrl"
                  alt="پیش نمایش عکس"
                  class="camera-captured-thumb"
                />
                <div v-else class="camera-captured-video-wrap">
                  <video :src="item.previewUrl" class="camera-captured-thumb" muted loop playsinline autoplay></video>
                  <span class="camera-captured-video-badge">ویدئو</span>
                </div>
                <button
                  type="button"
                  class="camera-captured-remove"
                  aria-label="حذف"
                  @click.stop="removeCapturedMedia(item.id)"
                >×</button>
              </div>
            </div>
          </div>

          <div class="camera-control-bar">
            <div class="camera-control-row">
              <button
                type="button"
                class="camera-queue-action camera-clear-btn"
                :disabled="!hasCapturedMediaQueue || isRecording"
                @click="clearCapturedMediaQueue"
              >
                پاک کردن
              </button>

              <button
                class="camera-shutter-btn"
                :class="{ recording: isRecording, video: cameraMode === 'video' }"
                :disabled="!isCameraReady"
                @click="handlePrimaryCameraAction"
              >
                <span class="camera-shutter-core"></span>
              </button>

              <button
                type="button"
                class="camera-queue-action camera-send-btn"
                :disabled="!canSendCapturedMedia"
                @click="sendCapturedMediaQueue"
              >
                <span v-if="hasCapturedMediaQueue" class="camera-send-count">{{ capturedMediaCount }}</span>
                <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"></line>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
              </button>
            </div>
            <div class="camera-capture-meta">
              <div class="camera-capture-label">
                {{ cameraMode === 'photo' ? 'ثبت عکس' : (isRecording ? 'توقف ضبط' : 'شروع ضبط ویدئو') }}
              </div>
              <div v-if="hasCapturedMediaQueue" class="camera-capture-queue-label">
                {{ capturedMediaQueueLabel }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <!-- Bottom Sheet -->
    <transition name="slide-up">
      <div
        v-if="modelValue && !showCameraCapture"
        ref="sheetRef"
        :class="['attachment-sheet', { 'full-screen-sheet': activeTab === 'location' }]"
        @touchstart="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
      >
        <!-- Drag Handle -->
        <div class="sheet-handle"><div class="handle-bar"></div></div>

        <!-- Tabs -->
        <div class="sheet-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            :class="['tab-btn', { active: activeTab === tab.id }]"
            @click="activeTab = tab.id"
          >
            <span class="tab-icon" v-html="tab.icon"></span>
            <span class="tab-label">{{ tab.label }}</span>
          </button>
        </div>

        <!-- Tab Content -->
        <div class="sheet-content">
          <!-- Gallery Tab -->
          <div v-if="activeTab === 'gallery'" class="tab-panel gallery-panel">
            <input ref="galleryInput" type="file" accept="image/*,video/*" multiple style="display:none" @change="onGalleryFile" />

            <button class="action-card" @click="openCameraCapture()">
              <div class="action-icon camera-icon">
                <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                  <circle cx="12" cy="13" r="4"/>
                </svg>
              </div>
              <span class="action-label">دوربین</span>
            </button>

            <button class="action-card" @click="galleryInput?.click()">
              <div class="action-icon gallery-icon">
                <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                  <circle cx="8.5" cy="8.5" r="1.5"/>
                  <polyline points="21 15 16 10 5 21"/>
                </svg>
              </div>
              <span class="action-label">گالری</span>
            </button>
          </div>

          <!-- File Tab -->
          <div v-if="activeTab === 'file'" class="tab-panel file-panel">
            <input ref="fileInput" type="file" accept="*" style="display:none" @change="onFileSelected" />

            <button class="action-card" @click="fileInput?.click()">
              <div class="action-icon file-icon">
                <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                  <polyline points="10 9 9 9 8 9"/>
                </svg>
              </div>
              <span class="action-label">فایل</span>
            </button>
          </div>

          <!-- Location Tab -->
          <div v-if="activeTab === 'location'" class="tab-panel location-panel">
            <div class="map-wrapper">
              <l-map
                ref="mapRef"
                :zoom="15"
                :center="mapCenter"
                :use-global-leaflet="false"
                @moveend="onMapMoveEnd"
                class="location-map"
              >
                <l-tile-layer
                  :url="tileUrl"
                  attribution="&copy; OpenStreetMap"
                />
              </l-map>
              <!-- Return to my location button -->
              <button class="my-location-btn" @click="goToMyLocation(false)" title="مکان من">
                <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 2v2M12 20v2M2 12h2M20 12h2M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z"/>
                  <circle cx="12" cy="12" r="2" fill="currentColor"/>
                </svg>
              </button>
              <!-- Fixed center pin -->
              <div class="center-pin">
                <svg viewBox="0 0 24 36" width="36" height="48" fill="#E53935">
                  <path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 24 12 24s12-15 12-24C24 5.4 18.6 0 12 0zm0 18c-3.3 0-6-2.7-6-6s2.7-6 6-6 6 2.7 6 6-2.7 6-6 6z"/>
                </svg>
              </div>
            </div>
            <button class="send-location-btn" @click="sendLocation">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
                <circle cx="12" cy="10" r="3"/>
              </svg>
              ارسال موقعیت مکانی
            </button>
          </div>
        </div>
      </div>
    </transition>
  </teleport>

  <!-- Phase A: Image editor modal for single-image gallery picks -->
  <teleport to="body">
    <ImageEditorModal
      v-if="editingFile"
      :key="singleEditorKey"
      :file="editingFile"
      @confirm="onEditorConfirm"
      @cancel="onEditorCancel"
    />
  </teleport>

  <!-- Phase B: Multi-image gallery preview + per-item edit -->
  <GalleryPreviewModal
    v-if="multiPreviewFiles"
    :files="multiPreviewFiles"
    @confirm="onMultiPreviewConfirm"
    @cancel="onMultiPreviewCancel"
  />

  <!-- Phase B: Per-item editor for camera queue photos -->
  <teleport to="body">
    <ImageEditorModal
      v-if="cameraEditingItem"
      :key="cameraEditingItem.id"
      :file="cameraEditingItem.file"
      @confirm="onCameraEditConfirm"
      @cancel="onCameraEditCancel"
    />
  </teleport>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import 'leaflet/dist/leaflet.css'
import { LMap, LTileLayer } from '@vue-leaflet/vue-leaflet'

// Lazy-load the image editor so Cropper.js (~40KB) and its CSS are only
// downloaded when the user actually edits an image. Keeps the main Messenger
// bundle lean for users who never edit.
const ImageEditorModal = defineAsyncComponent(() => import('./ImageEditorModal.vue'))
const GalleryPreviewModal = defineAsyncComponent(() => import('./GalleryPreviewModal.vue'))

type CameraZoomCapability = {
  min: number
  max: number
  step: number
}

type MediaTrackCapabilitiesWithZoom = MediaTrackCapabilities & {
  zoom?: {
    min?: number
    max?: number
    step?: number
  }
}

type ZoomCapableTrack = MediaStreamTrack & {
  getCapabilities?: () => MediaTrackCapabilities
  getSettings?: () => MediaTrackSettings & { zoom?: number }
}

type CapturedCameraMediaItem = {
  id: string
  file: File
  previewUrl: string
  type: 'photo' | 'video'
}

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
  (e: 'select-media', file: File, albumId?: string | null, albumIndex?: number, albumSize?: number): void
  (e: 'select-file', file: File): void
  (e: 'select-location', lat: number, lng: number): void
}>()

const activeTab = ref<'gallery' | 'file' | 'location'>('gallery')
const cameraPhotoInput = ref<HTMLInputElement | null>(null)
const cameraVideoInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

// Phase A image editor state.
// editingFile: holds the single image currently in the editor (null when
// editor is closed). We only route through the editor for single-image
// gallery picks.
const editingFile = ref<File | null>(null)
// Bumped each time we open the single-image editor so Vue remounts the
// component cleanly between sessions (prevents stale Cropper/blob state
// when the user opens the editor multiple times in the same sheet visit).
const singleEditorKey = ref<number>(0)

// Phase B state.
// multiPreviewFiles: holds the multi-image gallery pick while the user
// reviews/edits/removes items before dispatch. Null = preview closed.
const multiPreviewFiles = ref<File[] | null>(null)

// cameraEditingItemId: id of the camera-queue photo currently in the
// per-item editor. Null = editor closed.
const cameraEditingItemId = ref<string | null>(null)
const sheetRef = ref<HTMLElement | null>(null)
const mapRef = ref<any>(null)
const cameraPreviewRef = ref<HTMLVideoElement | null>(null)

const showCameraCapture = ref(false)
const cameraMode = ref<'photo' | 'video'>('photo')
const activeFacingMode = ref<'environment' | 'user'>('environment')
const cameraCaptureMode = ref<'inline' | 'native'>('inline')
const isCameraStarting = ref(false)
const cameraError = ref('')
const isRecording = ref(false)
const recordingSeconds = ref(0)
const capturedCameraMedia = ref<CapturedCameraMediaItem[]>([])
const cameraZoomCapability = ref<CameraZoomCapability | null>(null)
const cameraZoomValue = ref(1)

const cameraStream = ref<MediaStream | null>(null)
let mediaRecorder: MediaRecorder | null = null
let recordedChunks: BlobPart[] = []
let recordingTimer: number | null = null

// Default: Tehran
const mapCenter = ref<[number, number]>([35.6892, 51.3890])
const selectedLatLng = ref<{ lat: number; lng: number }>({ lat: 35.6892, lng: 51.3890 })

const tileUrl = ref('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')

const isUsingNativeCameraFallback = computed(() => cameraCaptureMode.value === 'native')
const nativeCameraFallbackTitle = computed(() => (
  cameraMode.value === 'photo'
    ? 'پیش نمایش زنده دوربین در این مرورگر در دسترس نیست.'
    : 'پیش نمایش زنده فیلم برداری در این مرورگر در دسترس نیست.'
))
const nativeCameraFallbackHint = computed(() => (
  cameraMode.value === 'photo'
    ? 'با دکمه پایین، دوربین سیستم برای گرفتن عکس باز می شود.'
    : 'با دکمه پایین، دوربین سیستم برای ضبط ویدئو باز می شود.'
))
const capturedMediaCount = computed(() => capturedCameraMedia.value.length)
const hasCapturedMediaQueue = computed(() => capturedMediaCount.value > 0)
const canSendCapturedMedia = computed(() => hasCapturedMediaQueue.value && !isRecording.value)
const capturedMediaQueueLabel = computed(() => {
  const count = capturedMediaCount.value
  return count === 1 ? '۱ مورد آماده ارسال' : `${count} مورد آماده ارسال`
})
const hasCameraZoomControl = computed(() => {
  if (isUsingNativeCameraFallback.value) return false
  const capability = cameraZoomCapability.value
  return Boolean(capability && capability.max > capability.min + 0.001)
})
const canZoomOut = computed(() => {
  const capability = cameraZoomCapability.value
  return Boolean(capability && cameraZoomValue.value > capability.min + 0.001)
})
const canZoomIn = computed(() => {
  const capability = cameraZoomCapability.value
  return Boolean(capability && cameraZoomValue.value < capability.max - 0.001)
})
const isCameraReady = computed(() => (
  isUsingNativeCameraFallback.value
  || (Boolean(cameraStream.value) && !isCameraStarting.value && !cameraError.value)
))
const formattedRecordingTime = computed(() => {
  const mins = Math.floor(recordingSeconds.value / 60)
  const secs = recordingSeconds.value % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
})
const cameraZoomDisplay = computed(() => {
  const normalizedZoom = cameraZoomValue.value >= 10
    ? cameraZoomValue.value.toFixed(0)
    : cameraZoomValue.value.toFixed(1)
  return `${normalizedZoom}x`
})

const tabs = [
  { id: 'gallery' as const, label: 'گالری', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>' },
  { id: 'file' as const, label: 'فایل', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' },
  { id: 'location' as const, label: 'موقعیت', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>' },
]

// Swipe to dismiss
let startY = 0
let currentTranslateY = 0
let isDraggingAllowed = true

function stopRecordingTimer() {
  if (recordingTimer !== null) {
    window.clearInterval(recordingTimer)
    recordingTimer = null
  }
}

function resetCameraZoomState() {
  cameraZoomCapability.value = null
  cameraZoomValue.value = 1
}

function getActiveCameraVideoTrack(): ZoomCapableTrack | null {
  return (cameraStream.value?.getVideoTracks?.()[0] as ZoomCapableTrack | undefined) ?? null
}

function clampCameraZoom(value: number, capability: CameraZoomCapability) {
  const bounded = Math.min(capability.max, Math.max(capability.min, value))
  const steps = Math.round((bounded - capability.min) / capability.step)
  return Number((capability.min + (steps * capability.step)).toFixed(2))
}

function getCameraZoomNudgeStep(capability: CameraZoomCapability) {
  const coarseStep = Number(((capability.max - capability.min) / 8).toFixed(2))
  return Math.max(capability.step, coarseStep)
}

async function syncCameraZoomCapability(track: ZoomCapableTrack | null = getActiveCameraVideoTrack()) {
  if (!track || typeof track.getCapabilities !== 'function') {
    resetCameraZoomState()
    return
  }

  const capabilities = track.getCapabilities() as MediaTrackCapabilitiesWithZoom
  const zoomRange = capabilities.zoom
  const min = Number(zoomRange?.min)
  const max = Number(zoomRange?.max)

  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) {
    resetCameraZoomState()
    return
  }

  const rawStep = Number(zoomRange?.step)
  const capability = {
    min,
    max,
    step: Number.isFinite(rawStep) && rawStep > 0 ? rawStep : 0.1,
  }

  cameraZoomCapability.value = capability

  const settingsZoom = Number(track.getSettings?.()?.zoom)
  cameraZoomValue.value = clampCameraZoom(Number.isFinite(settingsZoom) ? settingsZoom : capability.min, capability)
}

async function applyCameraZoom(nextValue: number) {
  const track = getActiveCameraVideoTrack()
  const capability = cameraZoomCapability.value

  if (!track || !capability || typeof track.applyConstraints !== 'function') return

  const zoom = clampCameraZoom(nextValue, capability)
  cameraZoomValue.value = zoom

  try {
    await track.applyConstraints({
      advanced: [{ zoom } as any],
    })

    const appliedZoom = Number(track.getSettings?.()?.zoom)
    if (Number.isFinite(appliedZoom)) {
      cameraZoomValue.value = clampCameraZoom(appliedZoom, capability)
    }
  } catch (error) {
    console.warn('Camera zoom apply failed:', error)
    await syncCameraZoomCapability(track)
  }
}

function handleCameraZoomInput(event: Event) {
  const target = event.target as HTMLInputElement | null
  if (!target) return

  const nextValue = Number(target.value)
  if (!Number.isFinite(nextValue)) return

  void applyCameraZoom(nextValue)
}

function nudgeCameraZoom(direction: -1 | 1) {
  const capability = cameraZoomCapability.value
  if (!capability) return

  const delta = getCameraZoomNudgeStep(capability) * direction
  void applyCameraZoom(cameraZoomValue.value + delta)
}

function createCapturedMediaId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `camera_capture_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

function revokeCapturedMediaPreview(url?: string) {
  if (url?.startsWith('blob:')) {
    URL.revokeObjectURL(url)
  }
}

function removeCapturedMedia(itemId: string) {
  const index = capturedCameraMedia.value.findIndex((item) => item.id === itemId)
  if (index === -1) return

  const [removedItem] = capturedCameraMedia.value.splice(index, 1)
  revokeCapturedMediaPreview(removedItem?.previewUrl)
}

function clearCapturedMediaQueue() {
  capturedCameraMedia.value.forEach((item) => revokeCapturedMediaPreview(item.previewUrl))
  capturedCameraMedia.value = []
}

function queueCapturedMedia(file: File) {
  const mediaType = file.type.startsWith('video/') ? 'video' : 'photo'
  capturedCameraMedia.value.push({
    id: createCapturedMediaId(),
    file,
    previewUrl: URL.createObjectURL(file),
    type: mediaType,
  })
}

function stopCameraTracks() {
  if (cameraPreviewRef.value) {
    try {
      cameraPreviewRef.value.pause()
      cameraPreviewRef.value.srcObject = null
    } catch {
      // Ignore preview cleanup failures.
    }
  }

  cameraStream.value?.getTracks().forEach((track) => track.stop())
  cameraStream.value = null
  resetCameraZoomState()
}

function cleanupCamera(discardRecording = true, clearCapturedMedia = true) {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.onstop = null
    try {
      mediaRecorder.stop()
    } catch {
      // Ignore recorder stop failures.
    }
  }

  if (discardRecording) {
    recordedChunks = []
  }

  mediaRecorder = null
  isRecording.value = false
  recordingSeconds.value = 0
  stopRecordingTimer()
  stopCameraTracks()
  cameraCaptureMode.value = 'inline'
  cameraError.value = ''
  isCameraStarting.value = false
  showCameraCapture.value = false

  if (clearCapturedMedia) {
    clearCapturedMediaQueue()
  }
}

function supportsInlineCameraPreview() {
  return typeof window !== 'undefined'
    && window.isSecureContext
    && !!navigator.mediaDevices?.getUserMedia
}

function openNativeCameraCapture(mode: 'photo' | 'video' = cameraMode.value) {
  if (mode === 'video') {
    cameraVideoInput.value?.click()
    return
  }

  cameraPhotoInput.value?.click()
}

function enableNativeCameraFallback() {
  stopCameraTracks()
  cameraCaptureMode.value = 'native'
  cameraError.value = ''
  isCameraStarting.value = false
  showCameraCapture.value = true
}

async function attachCameraStream(stream: MediaStream) {
  await nextTick()
  const preview = cameraPreviewRef.value
  if (!preview) return

  preview.srcObject = stream
  preview.muted = true

  try {
    await preview.play()
  } catch (error) {
    console.warn('Camera preview play failed:', error)
  }
}

async function requestCameraStream(includeAudio: boolean) {
  return navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: { ideal: activeFacingMode.value },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    },
    audio: includeAudio,
  })
}

async function startCameraStream() {
  if (!supportsInlineCameraPreview()) {
    enableNativeCameraFallback()
    return
  }

  cameraCaptureMode.value = 'inline'
  isCameraStarting.value = true
  cameraError.value = ''
  stopCameraTracks()

  try {
    const wantsAudio = cameraMode.value === 'video'
    let stream: MediaStream

    try {
      stream = await requestCameraStream(wantsAudio)
    } catch (error) {
      if (!wantsAudio) throw error
      stream = await requestCameraStream(false)
    }

    cameraStream.value = stream
    showCameraCapture.value = true
    await attachCameraStream(stream)
    await syncCameraZoomCapability(getActiveCameraVideoTrack())
  } catch (error) {
    console.error('Camera start error:', error)
    enableNativeCameraFallback()
  } finally {
    isCameraStarting.value = false
  }
}

function createCameraAlbumId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `camera_album_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

function getSupportedVideoMimeType() {
  if (typeof MediaRecorder === 'undefined') return ''

  const candidates = [
    'video/mp4;codecs=h264,aac',
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm',
  ]

  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || ''
}

async function sendCapturedMediaQueue() {
  if (!hasCapturedMediaQueue.value) return

  const queuedItems = capturedCameraMedia.value.map((item) => ({
    file: item.file,
    type: item.type,
  }))
  const albumId = queuedItems.length > 1 ? createCameraAlbumId() : null

  cleanupCamera(true, true)
  emit('update:modelValue', false)

  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame !== 'function') {
      setTimeout(resolve, 0)
      return
    }

    requestAnimationFrame(() => resolve())
  })

  queuedItems.forEach((item, index) => {
    emit('select-media', item.file, albumId, index, queuedItems.length)
  })
}

async function capturePhoto() {
  const preview = cameraPreviewRef.value
  if (!preview || !cameraStream.value) return

  const width = Math.max(1, preview.videoWidth || 1080)
  const height = Math.max(1, preview.videoHeight || 1920)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height

  const ctx = canvas.getContext('2d')
  if (!ctx) {
    alert('خطا در ثبت تصویر')
    return
  }

  ctx.drawImage(preview, 0, 0, width, height)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
  if (!blob) {
    alert('خطا در ثبت تصویر')
    return
  }

  const file = new File([blob], `camera_${Date.now()}.jpg`, { type: 'image/jpeg' })
  queueCapturedMedia(file)
}

function startVideoRecording() {
  if (!cameraStream.value) return
  if (typeof MediaRecorder === 'undefined') {
    alert('مرورگر شما از فیلم‌برداری پشتیبانی نمی‌کند.')
    return
  }

  recordedChunks = []
  const mimeType = getSupportedVideoMimeType()

  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(cameraStream.value, { mimeType })
      : new MediaRecorder(cameraStream.value)
  } catch (error) {
    console.error('MediaRecorder init failed:', error)
    alert('مرورگر شما از فیلم‌برداری پشتیبانی نمی‌کند.')
    return
  }

  mediaRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size > 0) {
      recordedChunks.push(event.data)
    }
  }

  mediaRecorder.onstop = async () => {
    const blob = new Blob(recordedChunks, { type: mediaRecorder?.mimeType || mimeType || 'video/webm' })
    if (blob.size <= 0) return

    const extension = blob.type.includes('mp4') ? 'mp4' : 'webm'
    const file = new File([blob], `camera_${Date.now()}.${extension}`, { type: blob.type || 'video/webm' })
    queueCapturedMedia(file)
  }

  mediaRecorder.start(200)
  isRecording.value = true
  recordingSeconds.value = 0
  stopRecordingTimer()
  recordingTimer = window.setInterval(() => {
    recordingSeconds.value += 1
  }, 1000)
}

function stopVideoRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') return

  isRecording.value = false
  stopRecordingTimer()
  mediaRecorder.stop()
}

function handlePrimaryCameraAction() {
  if (isUsingNativeCameraFallback.value) {
    openNativeCameraCapture(cameraMode.value)
    return
  }

  if (!isCameraReady.value) return

  if (cameraMode.value === 'photo') {
    void capturePhoto()
    return
  }

  if (isRecording.value) {
    stopVideoRecording()
    return
  }

  startVideoRecording()
}

function openCameraCapture() {
  cameraMode.value = 'photo'
  activeFacingMode.value = 'environment'
  showCameraCapture.value = true

  if (!supportsInlineCameraPreview()) {
    enableNativeCameraFallback()
    return
  }

  void startCameraStream()
}

async function setCameraMode(mode: 'photo' | 'video') {
  if (cameraMode.value === mode || isRecording.value) return
  cameraMode.value = mode

  if (showCameraCapture.value && !isUsingNativeCameraFallback.value) {
    await startCameraStream()
  }
}

async function toggleFacingMode() {
  if (isRecording.value || isUsingNativeCameraFallback.value) return
  activeFacingMode.value = activeFacingMode.value === 'environment' ? 'user' : 'environment'

  if (showCameraCapture.value) {
    await startCameraStream()
  }
}

function closeCameraCapture() {
  cleanupCamera(true)
}

function onNativeCameraFile(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return

  Array.from(input.files).forEach((file) => {
    queueCapturedMedia(file)
  })

  input.value = ''
}

function onTouchStart(e: TouchEvent) {
  isDraggingAllowed = true
  const target = e.target as HTMLElement
  
  // Prevent drag-to-dismiss if the user is interacting with the map
  if (target.closest('.map-wrapper')) {
    isDraggingAllowed = false
    return
  }

  const touch = e.touches[0]
  if (!touch) return
  startY = touch.clientY
  currentTranslateY = 0
}

function onTouchMove(e: TouchEvent) {
  if (!isDraggingAllowed) return
  
  const touch = e.touches[0]
  if (!touch) return
  const dy = touch.clientY - startY
  if (dy > 0) {
    currentTranslateY = dy
    if (sheetRef.value) {
      sheetRef.value.style.transform = `translateY(${dy}px)`
    }
  }
}

function onTouchEnd() {
  if (!isDraggingAllowed) return
  
  if (currentTranslateY > 100) {
    close()
  } else if (sheetRef.value) {
    sheetRef.value.style.transform = ''
  }
  currentTranslateY = 0
}

function close() {
  cleanupCamera(true)
  emit('update:modelValue', false)
}

// Reset tab on open
watch(() => props.modelValue, (val) => {
  if (val) activeTab.value = 'gallery'
  if (!val) cleanupCamera(true)
})

watch(() => activeTab.value, (val) => {
  if (val === 'location') {
    // Optionally trigger map resize to fix leaflet gray rendering
    setTimeout(() => {
      mapRef.value?.leafletObject?.invalidateSize()
      goToMyLocation(true) // Automatically try to fetch location on open
    }, 300)
  }
})

// Gallery file handler.
// Do not pre-compress here: useChatMedia.ts performs the EXIF-safe pipeline.
//
// Phase A routing:
//   - Single image pick (and not HEIC) → open the crop editor. User can
//     confirm edited output, send unedited, or cancel entirely.
//   - Multi-image album OR video included → bypass editor (Phase B will add
//     a preview grid with per-item pencil button).
async function onGalleryFile(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return

  const files = Array.from(input.files)
  input.value = ''

  // Phase A: single-image editing fast-path.
  const onlyOne = files.length === 1
  const onlyFile = files[0]
  const isImage = onlyOne && !!onlyFile && onlyFile.type.startsWith('image/')
  // HEIC needs to go through useChatMedia's normalize step first; we don't
  // try to render it inside Cropper directly.
  const isHeic =
    onlyOne &&
    !!onlyFile &&
    (onlyFile.type === 'image/heic' ||
      onlyFile.type === 'image/heif' ||
      /\.(heic|heif)$/i.test(onlyFile.name))

  if (onlyOne && isImage && !isHeic && onlyFile) {
    close()
    // Wait for sheet close animation before mounting editor to avoid a
    // visible overlap flash.
    await new Promise<void>((resolve) => setTimeout(resolve, 180))
    singleEditorKey.value += 1
    editingFile.value = onlyFile
    return
  }

  // Phase B: multi-file gallery picks go through the preview sheet so the
  // user can edit, remove, or reorder before the album is dispatched.
  if (files.length > 1) {
    close()
    await new Promise<void>((resolve) => setTimeout(resolve, 180))
    multiPreviewFiles.value = files
    return
  }

  // Single non-image pick (video or HEIC) → emit directly.
  close()
  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame !== 'function') {
      setTimeout(resolve, 0)
      return
    }
    requestAnimationFrame(() => resolve())
  })
  files.forEach((file, index) => {
    emit('select-media', file, null, index, files.length)
  })
}

function onMultiPreviewConfirm(finalFiles: File[]) {
  multiPreviewFiles.value = null
  if (!finalFiles.length) return
  const albumId = finalFiles.length > 1
    ? (globalThis.crypto?.randomUUID?.() ?? `album_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`)
    : null
  finalFiles.forEach((file, index) => {
    emit('select-media', file, albumId, index, finalFiles.length)
  })
}

function onMultiPreviewCancel() {
  multiPreviewFiles.value = null
}

// --- Camera queue per-item edit (Phase B) ---

const cameraEditingItem = computed(() => {
  const id = cameraEditingItemId.value
  if (!id) return null
  return capturedCameraMedia.value.find((it) => it.id === id) ?? null
})

function editCapturedMedia(itemId: string) {
  const item = capturedCameraMedia.value.find((it) => it.id === itemId)
  if (!item || item.type !== 'photo') return
  cameraEditingItemId.value = itemId
}

function onCameraEditConfirm(editedFile: File) {
  const id = cameraEditingItemId.value
  cameraEditingItemId.value = null
  if (!id) return
  const idx = capturedCameraMedia.value.findIndex((it) => it.id === id)
  if (idx < 0) return
  const old = capturedCameraMedia.value[idx]
  if (!old) return
  revokeCapturedMediaPreview(old.previewUrl)
  capturedCameraMedia.value[idx] = {
    ...old,
    file: editedFile,
    previewUrl: URL.createObjectURL(editedFile),
  }
}

function onCameraEditCancel() {
  cameraEditingItemId.value = null
}

function onEditorConfirm(editedFile: File) {
  // Single-image edited path: emit with no albumId so useChatMedia dispatches
  // it as a standalone message.
  editingFile.value = null
  emit('select-media', editedFile, null, 0, 1)
}

function onEditorCancel() {
  // User dismissed the editor entirely → do not send anything.
  editingFile.value = null
}

// File handler (no compression)
function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return

  for (const file of Array.from(input.files)) {
    emit('select-file', file)
  }
  input.value = ''
  close()
}

// Map
function onMapMoveEnd() {
  const map = mapRef.value?.leafletObject
  if (map) {
    const center = map.getCenter()
    selectedLatLng.value = { lat: center.lat, lng: center.lng }
  }
}

function goToMyLocation(silent = false) {
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude
        const lng = position.coords.longitude
        mapCenter.value = [lat, lng]
        selectedLatLng.value = { lat, lng }
        
        const map = mapRef.value?.leafletObject
        if (map) {
          map.setView([lat, lng], 15)
        }
      },
      (error) => {
        console.error('Geolocation error:', error)
        if (silent !== true) {
          alert('امکان دریافت مکان شما وجود ندارد. لطفا دسترسی مرورگر را بررسی کنید.')
        }
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    )
  } else {
    if (silent !== true) {
      alert('مرورگر شما از مکان‌یابی پشتیبانی نمی‌کند.')
    }
  }
}

function sendLocation() {
  emit('select-location', selectedLatLng.value.lat, selectedLatLng.value.lng)
  close()
}

onBeforeUnmount(() => {
  cleanupCamera(true)
})
</script>

<style scoped>
.attachment-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 999;
}

.camera-capture-overlay {
  position: fixed;
  inset: 0;
  z-index: 1001;
  background: #000;
}

.camera-capture-shell {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #000;
  color: #fff;
}

.camera-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
}

.camera-icon-btn {
  width: 42px;
  height: 42px;
  border-radius: 999px;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.12);
  color: white;
  cursor: pointer;
}

.camera-icon-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.camera-mode-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
}

.camera-mode-btn {
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.82);
  padding: 8px 14px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.camera-mode-btn.active {
  background: rgba(255, 255, 255, 0.22);
  color: white;
}

.camera-mode-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.camera-preview-frame {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.camera-preview {
  width: 100%;
  height: 100%;
  object-fit: cover;
  background: #050505;
}

.camera-status-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 20px;
  text-align: center;
  background: rgba(0, 0, 0, 0.58);
  backdrop-filter: blur(10px);
}

.camera-status-overlay.error {
  background: rgba(15, 15, 15, 0.82);
}

.camera-status-overlay.native {
  background: linear-gradient(180deg, rgba(10, 10, 10, 0.7), rgba(10, 10, 10, 0.88));
}

.camera-fallback-hint {
  max-width: 320px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 14px;
  line-height: 1.6;
}

.camera-error-btn {
  border: none;
  border-radius: 999px;
  padding: 10px 16px;
  background: #3390ec;
  color: white;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.camera-recording-badge {
  position: absolute;
  top: 16px;
  left: 16px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border-radius: 999px;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.58);
  color: white;
  font-size: 13px;
  font-weight: 600;
  backdrop-filter: blur(8px);
}

.camera-zoom-panel {
  position: absolute;
  top: 16px;
  right: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 18px;
  background: rgba(0, 0, 0, 0.48);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.24);
  backdrop-filter: blur(10px);
  z-index: 2;
}

.camera-zoom-slider-wrap {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  min-width: 126px;
}

.camera-zoom-label {
  text-align: center;
  color: rgba(255, 255, 255, 0.92);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.camera-zoom-slider {
  width: 100%;
  accent-color: #39a0ff;
}

.camera-zoom-btn {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  border: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: white;
  background: rgba(255, 255, 255, 0.14);
  cursor: pointer;
}

.camera-zoom-btn:disabled {
  opacity: 0.38;
  cursor: default;
}

.camera-captured-strip {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
  z-index: 2;
}

.camera-captured-strip::-webkit-scrollbar {
  display: none;
}

.camera-captured-item {
  position: relative;
  width: 68px;
  min-width: 68px;
  height: 92px;
  border: none;
  border-radius: 18px;
  overflow: hidden;
  padding: 0;
  background: rgba(0, 0, 0, 0.38);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
}

.camera-captured-thumb,
.camera-captured-video-wrap {
  width: 100%;
  height: 100%;
}

.camera-captured-thumb {
  object-fit: cover;
  display: block;
}

.camera-captured-video-wrap {
  position: relative;
}

.camera-captured-video-wrap .camera-captured-thumb {
  width: 100%;
  height: 100%;
}

.camera-captured-video-badge {
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: 8px;
  border-radius: 999px;
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 700;
  background: rgba(0, 0, 0, 0.68);
  color: white;
}

.camera-captured-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.62);
  color: white;
  font-size: 16px;
  line-height: 1;
  border: none;
  cursor: pointer;
  padding: 0;
}
.camera-captured-remove:active { background: rgba(0, 0, 0, 0.85); }

.camera-captured-item.editable { cursor: pointer; }

.recording-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #ef4444;
  box-shadow: 0 0 0 5px rgba(239, 68, 68, 0.16);
}

.camera-control-bar {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 18px 16px calc(18px + env(safe-area-inset-bottom));
  background: linear-gradient(to top, rgba(0, 0, 0, 0.76), rgba(0, 0, 0, 0.36));
}

.camera-control-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 16px;
}

.camera-queue-action {
  height: 48px;
  border: none;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: white;
  background: rgba(255, 255, 255, 0.16);
  font-size: 13px;
  font-weight: 700;
  padding: 0 16px;
}

.camera-queue-action:disabled {
  opacity: 0.38;
}

.camera-clear-btn {
  justify-self: start;
}

.camera-send-btn {
  justify-self: end;
  min-width: 56px;
  padding-inline: 14px;
  background: linear-gradient(135deg, #28b463, #1e8f4e);
}

.camera-send-count {
  min-width: 18px;
  text-align: center;
}

.camera-shutter-btn {
  width: 88px;
  height: 88px;
  border-radius: 999px;
  border: 4px solid rgba(255, 255, 255, 0.95);
  background: rgba(255, 255, 255, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: transform 0.18s ease, background 0.18s ease;
}

.camera-shutter-btn:disabled {
  opacity: 0.45;
  cursor: default;
}

.camera-shutter-btn:not(:disabled):active {
  transform: scale(0.96);
}

.camera-shutter-core {
  width: 68px;
  height: 68px;
  border-radius: 999px;
  background: white;
  transition: all 0.18s ease;
}

.camera-shutter-btn.video .camera-shutter-core {
  width: 54px;
  height: 54px;
  background: #ef4444;
}

.camera-shutter-btn.recording .camera-shutter-core {
  width: 36px;
  height: 36px;
  border-radius: 12px;
}

.camera-capture-label {
  min-height: 20px;
  color: rgba(255, 255, 255, 0.84);
  font-size: 13px;
  font-weight: 500;
}

.camera-capture-meta {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.camera-capture-queue-label {
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  font-weight: 600;
}

.attachment-sheet {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #fff;
  border-radius: 16px 16px 0 0;
  z-index: 1000;
  max-height: 75vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.15);
  transition: all 0.2s ease-out;
  will-change: transform, height, max-height, border-radius, top;
}

.full-screen-sheet {
  top: 0 !important;
  max-height: 100% !important;
  height: 100% !important;
  border-radius: 0 !important;
}

.full-screen-sheet .sheet-content {
  padding: 0;
  display: flex;
  flex-direction: column;
}

.full-screen-sheet .location-panel {
  flex: 1;
  gap: 0;
}

.full-screen-sheet .map-wrapper {
  height: auto;
  border-radius: 0;
  border: none;
  flex: 1;
}

.full-screen-sheet .send-location-btn {
  border-radius: 0;
  padding: 16px 12px;
  font-size: 16px;
}

.sheet-handle {
  display: flex;
  justify-content: center;
  padding: 10px 0 4px;
}
.handle-bar {
  width: 36px;
  height: 4px;
  border-radius: 2px;
  background: #d1d5db;
}

.sheet-tabs {
  display: flex;
  border-bottom: 1px solid #f0f0f0;
  padding: 0 8px;
}
.tab-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 4px;
  background: none;
  border: none;
  cursor: pointer;
  color: #9ca3af;
  font-size: 12px;
  font-weight: 500;
  border-bottom: 2px solid transparent;
  transition: all 0.2s;
}
.tab-btn.active {
  color: #3390ec;
  border-bottom-color: #3390ec;
}
.tab-icon { display: flex; align-items: center; }

.sheet-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  min-height: 200px;
}

.tab-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  justify-content: center;
}

.action-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 20px 24px;
  background: #f9fafb;
  border: 1px solid #f0f0f0;
  border-radius: 16px;
  cursor: pointer;
  transition: all 0.2s;
  min-width: 110px;
}
.action-card:active {
  transform: scale(0.95);
  background: #f3f4f6;
}

.action-icon {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.camera-icon { background: #e0f2fe; color: #0284c7; }
.gallery-icon { background: #fce7f3; color: #db2777; }
.file-icon { background: #ede9fe; color: #7c3aed; }

.action-label {
  font-size: 13px;
  font-weight: 500;
  color: #374151;
}

/* Location panel */
.location-panel {
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
}

.map-wrapper {
  position: relative;
  width: 100%;
  height: 280px;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
}

.location-map {
  width: 100%;
  height: 100%;
  z-index: 1;
}

.my-location-btn {
  position: absolute;
  bottom: 24px;
  right: 16px;
  z-index: 1000;
  width: 48px;
  height: 48px;
  background: white;
  border: none;
  border-radius: 50%;
  border: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #3390ec;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  cursor: pointer;
  transition: all 0.2s;
}
.my-location-btn:active {
  transform: scale(0.9);
  background: #f3f4f6;
}

.center-pin {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -100%);
  z-index: 1000;
  pointer-events: none;
  filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));
}

.send-location-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 12px;
  background: #3390ec;
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
}
.send-location-btn:active {
  background: #2563eb;
}

/* Transitions */
.fade-enter-active, .fade-leave-active { transition: opacity 0.25s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.slide-up-enter-active { transition: transform 0.3s cubic-bezier(0.2, 0, 0, 1); }
.slide-up-leave-active { transition: transform 0.25s cubic-bezier(0.4, 0, 1, 1); }
.slide-up-enter-from, .slide-up-leave-to { transform: translateY(100%); }

@media (max-width: 640px) {
  .camera-topbar {
    padding: 10px 10px 0;
  }

  .camera-zoom-panel {
    top: 12px;
    right: 12px;
    left: 12px;
    gap: 8px;
    padding: 10px;
  }

  .camera-zoom-slider-wrap {
    min-width: 0;
    flex: 1;
  }

  .camera-mode-btn {
    padding: 8px 12px;
  }

  .camera-control-bar {
    padding: 14px 14px calc(18px + env(safe-area-inset-bottom));
  }

  .camera-control-row {
    gap: 10px;
  }

  .camera-queue-action {
    font-size: 12px;
    padding-inline: 12px;
  }

  .camera-shutter-btn {
    width: 82px;
    height: 82px;
  }

  .camera-shutter-core {
    width: 62px;
    height: 62px;
  }
}
</style>
