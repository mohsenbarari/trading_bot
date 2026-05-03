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
            <input ref="fileInput" type="file" accept="*" multiple style="display:none" @change="onFileSelected" />

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
                <l-circle
                  v-if="detectedLocationLatLng && detectedLocationAccuracyM"
                  :lat-lng="detectedLocationLatLng"
                  :radius="detectedLocationAccuracyM"
                  :color="'#3390ec'"
                  :weight="1"
                  :opacity="0.35"
                  :fill-color="'#3390ec'"
                  :fill-opacity="0.14"
                />
                <l-circle-marker
                  v-if="detectedLocationLatLng"
                  :lat-lng="detectedLocationLatLng"
                  :radius="9"
                  :color="'#ffffff'"
                  :weight="3"
                  :fill-color="'#2f80ed'"
                  :fill-opacity="1"
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
            <div
              v-if="locationStatusMessage"
              class="location-status"
              :class="{ 'is-error': locationStatusTone === 'error' }"
            >
              <span>{{ locationStatusMessage }}</span>
              <button
                v-if="locationStatusTone === 'error' && !isLocating"
                type="button"
                class="location-status-action"
                @click="goToMyLocation(false)"
              >
                تلاش مجدد
              </button>
            </div>
            <div v-if="shouldShowPreciseLocationGuide" class="precise-location-guide">
              <div class="precise-location-guide-header">
                <div>
                  <div class="precise-location-guide-badge">
                    {{ preciseLocationGuideDetectedLabel }}
                  </div>
                  <div class="precise-location-guide-title">برای دقت چندمتری، موقعیت دقیق را روشن کنید</div>
                  <p class="precise-location-guide-description">
                    اگر این صفحه را داخل تلگرام، کروم، سافاری یا مرورگر دیگری باز کرده‌اید، باید اجازه Location را برای همان اپ روی حالت دقیق قرار دهید.
                  </p>
                </div>
              </div>

              <div v-if="preciseLocationGuideNeedsPlatformChoice" class="precise-location-guide-chooser">
                <div class="precise-location-guide-chooser-title">سیستم عامل دستگاه به صورت قطعی شناسایی نشد. یکی را انتخاب کنید:</div>
                <div class="precise-location-guide-choice-row">
                  <button type="button" class="precise-location-guide-choice" @click="selectPreciseLocationGuidePlatform('android')">
                    آموزش اندروید
                  </button>
                  <button type="button" class="precise-location-guide-choice" @click="selectPreciseLocationGuidePlatform('ios')">
                    آموزش آيفون
                  </button>
                </div>
              </div>

              <ol v-else class="precise-location-guide-steps">
                <li v-for="step in preciseLocationGuideSteps" :key="step">{{ step }}</li>
              </ol>

              <div v-if="!preciseLocationGuideNeedsPlatformChoice" class="precise-location-guide-note">
                بعد از انجام مراحل بالا به همین صفحه برگردید و یک بار روی «تلاش مجدد» بزنید.
              </div>

              <label class="precise-location-guide-dismiss">
                <input
                  type="checkbox"
                  :checked="hidePreciseLocationGuideForever"
                  @change="handlePreciseLocationGuideDismissChange"
                />
                <span>دیگر این راهنما را نشان نده</span>
              </label>
            </div>
            <button class="send-location-btn" :disabled="!canSendLocation" @click="sendLocation">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
                <circle cx="12" cy="10" r="3"/>
              </svg>
              {{ isLocating ? 'در حال یافتن موقعیت...' : 'ارسال موقعیت مکانی' }}
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
import { LCircle, LCircleMarker, LMap, LTileLayer } from '@vue-leaflet/vue-leaflet'

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

type PreciseLocationGuidePlatform = 'android' | 'ios'

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
const recordingDeciseconds = ref(0)
const capturedCameraMedia = ref<CapturedCameraMediaItem[]>([])
const cameraZoomCapability = ref<CameraZoomCapability | null>(null)
const cameraZoomValue = ref(1)

const cameraStream = ref<MediaStream | null>(null)
let mediaRecorder: MediaRecorder | null = null
let recordedChunks: BlobPart[] = []
let recordingTimer: number | null = null

// Default: Tehran
const DEFAULT_LOCATION_CENTER: [number, number] = [35.6892, 51.3890]
const mapCenter = ref<[number, number]>([...DEFAULT_LOCATION_CENTER])
const selectedLatLng = ref<{ lat: number; lng: number } | null>(null)
const detectedLocationLatLng = ref<[number, number] | null>(null)
const detectedLocationAccuracyM = ref<number | null>(null)
const isLocating = ref(false)
const locationStatusMessage = ref('')
const locationStatusTone = ref<'info' | 'error'>('info')
const hasManualLocationSelection = ref(false)
const hasConfirmedAutoLocation = ref(false)
let locationWatchId: number | null = null
let isProgrammaticMapMove = false
let activeLocationLookupId = 0

const DESIRED_LOCATION_ACCURACY_METERS = 120
const MAX_ACCEPTABLE_AUTO_LOCATION_ACCURACY_METERS = 120
const MAX_AUTO_SELECTION_PROMOTION_ACCURACY_METERS = 600
const MAX_COARSE_PREVIEW_ACCURACY_METERS = 5000
const MAX_COARSE_PREVIEW_DISTANCE_METERS = 120000
const MIN_AUTO_LOCATION_CONFIRM_DISTANCE_METERS = 75
const MAX_AUTO_LOCATION_CONFIRM_DISTANCE_METERS = 300
const AUTO_LOCATION_CONFIRM_TIMEOUT_MS = 12000
const MAX_LOCATION_DEBUG_ENTRIES = 18
const PRECISE_LOCATION_GUIDE_STORAGE_KEY = 'chat_precise_location_guide_hidden_v1'
const IRAN_LOCATION_BOUNDS = {
  minLat: 24,
  maxLat: 40.5,
  minLng: 44,
  maxLng: 64.5,
} as const

const detectedPreciseLocationGuidePlatform = detectPreciseLocationGuidePlatform()
const hidePreciseLocationGuideForever = ref(readPreciseLocationGuideDismissed())
const selectedPreciseLocationGuidePlatform = ref<PreciseLocationGuidePlatform | null>(detectedPreciseLocationGuidePlatform)

const tileUrl = ref('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')
const locationDebugEntries = ref<Array<{ id: number; time: string; label: string; details: string }>>([])
let locationDebugSequence = 0

const isUsingNativeCameraFallback = computed(() => cameraCaptureMode.value === 'native')
const nativeCameraFallbackTitle = computed(() => (
  cameraMode.value === 'photo'
    ? 'پیش نمایش زنده دوربین در این مرورگر در دسترس نیست.'
    : 'فیلم برداری زنده داخل اپ در این مرورگر در دسترس نیست.'
))
const nativeCameraFallbackHint = computed(() => (
  cameraMode.value === 'photo'
    ? 'با دکمه پایین، دوربین سیستم برای گرفتن عکس باز می شود.'
    : 'با دکمه پایین، دوربین سیستم برای ضبط ویدئو باز می شود. در این حالت ثانیه شمار سفارشی اپ نمایش داده نمی شود، چون ضبط داخل دوربین سیستم انجام می شود. برای دیدن ثانیه شمار باید صفحه روی نسخه امن HTTPS باز شود تا پیش نمایش داخلی دوربین فعال باشد.'
))
const capturedMediaCount = computed(() => capturedCameraMedia.value.length)
const hasCapturedMediaQueue = computed(() => capturedMediaCount.value > 0)
const canSendCapturedMedia = computed(() => hasCapturedMediaQueue.value && !isRecording.value)
const canSendLocation = computed(() => {
  if (isLocating.value || !selectedLatLng.value) {
    return false
  }

  if (hasManualLocationSelection.value) {
    return true
  }

  return hasConfirmedAutoLocation.value
    && detectedLocationAccuracyM.value !== null
    && detectedLocationAccuracyM.value <= MAX_ACCEPTABLE_AUTO_LOCATION_ACCURACY_METERS
})
const selectedLatLngDebugText = computed(() => formatLatLngDebugText(selectedLatLng.value))
const detectedLatLngDebugText = computed(() => formatLatLngDebugText(
  detectedLocationLatLng.value
    ? { lat: detectedLocationLatLng.value[0], lng: detectedLocationLatLng.value[1] }
    : null,
))
const detectedAccuracyDebugText = computed(() => formatAccuracyDebugText(detectedLocationAccuracyM.value))
const locationModeDebugText = computed(() => {
  if (isLocating.value) return 'در حال مکان‌یابی'
  if (hasManualLocationSelection.value) return 'انتخاب دستی پین'
  if (hasConfirmedAutoLocation.value) return 'تایید خودکار GPS'
  return 'در انتظار تایید'
})
const resolvedPreciseLocationGuidePlatform = computed(() => selectedPreciseLocationGuidePlatform.value)
const preciseLocationGuideNeedsPlatformChoice = computed(() => resolvedPreciseLocationGuidePlatform.value === null)
const shouldShowPreciseLocationGuide = computed(() => (
  activeTab.value === 'location' && !hidePreciseLocationGuideForever.value
))
const preciseLocationGuideDetectedLabel = computed(() => {
  const platform = resolvedPreciseLocationGuidePlatform.value
  if (platform === 'android') {
    return 'راهنمای اندروید'
  }
  if (platform === 'ios') {
    return 'راهنمای آيفون'
  }
  return 'راهنمای فعال‌سازی موقعیت دقیق'
})
const preciseLocationGuideSteps = computed(() => {
  const platform = resolvedPreciseLocationGuidePlatform.value
  if (platform === 'android') {
    return [
      'تنظیمات گوشی را باز کنید و وارد Apps یا برنامه ها شوید.',
      'مرورگر یا اپی که این صفحه در آن باز شده را انتخاب کنید. اگر صفحه داخل تلگرام باز شده، روی Telegram بزنید.',
      'به Permissions > Location بروید و دسترسی مکان را روی Allow only while using the app یا گزینه مشابه قرار دهید.',
      'گزینه Use precise location یا موقعیت دقیق را روشن کنید.',
      'Location گوشی، GPS و در صورت امکان Wi-Fi scanning را روشن نگه دارید.',
    ]
  }

  if (platform === 'ios') {
    return [
      'وارد Settings شوید و به Privacy & Security > Location Services بروید.',
      'روی اپی که این صفحه در آن باز شده بزنید؛ معمولا Safari، Chrome یا Telegram.',
      'اجازه Location را روی While Using the App قرار دهید.',
      'گزینه Precise Location را روشن کنید.',
      'به برنامه برگردید و چند ثانیه صبر کنید تا GPS دقیق تر شود.',
    ]
  }

  return []
})
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
  const totalSeconds = Math.floor(recordingDeciseconds.value / 10)
  const mins = Math.floor(totalSeconds / 60)
  const secs = totalSeconds % 60
  const deciseconds = recordingDeciseconds.value % 10
  return `${mins}:${secs.toString().padStart(2, '0')}.${deciseconds}`
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
  recordingDeciseconds.value = 0
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
  recordingDeciseconds.value = 0
  stopRecordingTimer()
  recordingTimer = window.setInterval(() => {
    recordingDeciseconds.value += 1
  }, 100)
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

function getAutoPinnedLatLng(): [number, number] | null {
  if (hasManualLocationSelection.value) {
    return null
  }

  if (selectedLatLng.value) {
    return [selectedLatLng.value.lat, selectedLatLng.value.lng]
  }

  if (detectedLocationLatLng.value) {
    return [...detectedLocationLatLng.value] as [number, number]
  }

  return null
}

async function refreshLocationMapViewport(recenterAutoPin = false) {
  if (!props.modelValue || activeTab.value !== 'location') {
    return
  }

  await nextTick()

  const map = mapRef.value?.leafletObject
  if (!map) {
    return
  }

  map.invalidateSize()

  if (!recenterAutoPin) {
    return
  }

  const autoPinnedLatLng = getAutoPinnedLatLng()
  if (!autoPinnedLatLng) {
    return
  }

  isProgrammaticMapMove = true
  map.setView(autoPinnedLatLng, map.getZoom?.() ?? 15, { animate: false })
}

function resetLocationDraft() {
  activeLocationLookupId += 1
  locationDebugEntries.value = []
  locationDebugSequence = 0

  if (locationWatchId !== null) {
    navigator.geolocation.clearWatch(locationWatchId)
    locationWatchId = null
  }

  isProgrammaticMapMove = false
  mapCenter.value = [...DEFAULT_LOCATION_CENTER]
  selectedLatLng.value = null
  detectedLocationLatLng.value = null
  detectedLocationAccuracyM.value = null
  isLocating.value = false
  hasManualLocationSelection.value = false
  hasConfirmedAutoLocation.value = false
  clearLocationStatus()
}

// Reset tab on open
watch(() => props.modelValue, (val) => {
  if (val) {
    activeTab.value = 'gallery'
    resetLocationDraft()
  }
  if (!val) {
    cleanupCamera(true)
    resetLocationDraft()
  }
})

watch(() => activeTab.value, (val) => {
  if (val === 'location') {
    pushLocationDebug('location-tab-opened', {
      secure: typeof window !== 'undefined' ? window.isSecureContext : false,
    })
    // Optionally trigger map resize to fix leaflet gray rendering
    setTimeout(() => {
      void refreshLocationMapViewport(true)
      goToMyLocation(true) // Automatically try to fetch location on open
    }, 300)
  }
})

watch(
  [
    () => locationStatusMessage.value,
    () => shouldShowPreciseLocationGuide.value,
    () => preciseLocationGuideNeedsPlatformChoice.value,
  ],
  () => {
    if (!props.modelValue || activeTab.value !== 'location') {
      return
    }

    void refreshLocationMapViewport(!hasManualLocationSelection.value)
  },
)

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
    if (isProgrammaticMapMove) {
      isProgrammaticMapMove = false
      return
    }

    selectedLatLng.value = { lat: center.lat, lng: center.lng }
    hasManualLocationSelection.value = true
    pushLocationDebug('manual-pin', {
      lat: center.lat,
      lng: center.lng,
    })
    clearLocationStatus()
  }
}

function setLocationStatus(message: string, tone: 'info' | 'error' = 'info') {
  locationStatusMessage.value = message
  locationStatusTone.value = tone
  pushLocationDebug(tone === 'error' ? 'status-error' : 'status', message)
}

function clearLocationStatus() {
  locationStatusMessage.value = ''
  locationStatusTone.value = 'info'
}

function isCurrentLocationLookup(lookupId: number) {
  return lookupId === activeLocationLookupId && props.modelValue && activeTab.value === 'location'
}

function detectPreciseLocationGuidePlatform(): PreciseLocationGuidePlatform | null {
  if (typeof navigator === 'undefined') {
    return null
  }

  const userAgent = navigator.userAgent.toLowerCase()
  if (/android/.test(userAgent)) {
    return 'android'
  }

  const isIOS = /iphone|ipad|ipod/.test(userAgent)
  const isIPadDesktopMode = navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1
  if (isIOS || isIPadDesktopMode) {
    return 'ios'
  }

  return null
}

function readPreciseLocationGuideDismissed() {
  if (typeof window === 'undefined') {
    return false
  }

  try {
    return window.localStorage.getItem(PRECISE_LOCATION_GUIDE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function writePreciseLocationGuideDismissed(hidden: boolean) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    if (hidden) {
      window.localStorage.setItem(PRECISE_LOCATION_GUIDE_STORAGE_KEY, '1')
      return
    }

    window.localStorage.removeItem(PRECISE_LOCATION_GUIDE_STORAGE_KEY)
  } catch {
    // Ignore storage failures and keep guide visible by default.
  }
}

function selectPreciseLocationGuidePlatform(platform: PreciseLocationGuidePlatform) {
  selectedPreciseLocationGuidePlatform.value = platform
}

function handlePreciseLocationGuideDismissChange(event: Event) {
  const checked = Boolean((event.target as HTMLInputElement | null)?.checked)
  hidePreciseLocationGuideForever.value = checked
  writePreciseLocationGuideDismissed(checked)
}

function formatDebugNumber(value: number | null | undefined, digits = 6) {
  return Number.isFinite(value as number) ? Number(value).toFixed(digits) : '—'
}

function formatLatLngDebugText(value: { lat: number; lng: number } | null) {
  if (!value) {
    return '—'
  }

  return `${formatDebugNumber(value.lat)}, ${formatDebugNumber(value.lng)}`
}

function formatAccuracyDebugText(value: number | null) {
  if (!Number.isFinite(value as number)) {
    return '—'
  }

  return `${Math.round(Number(value))}m`
}

function stringifyLocationDebugDetails(details?: string | Record<string, unknown>) {
  if (!details) {
    return ''
  }

  if (typeof details === 'string') {
    return details
  }

  return Object.entries(details)
    .map(([key, value]) => {
      if (typeof value === 'number') {
        return `${key}=${Number.isInteger(value) ? value : value.toFixed(6)}`
      }

      if (typeof value === 'boolean') {
        return `${key}=${value ? 'yes' : 'no'}`
      }

      return `${key}=${String(value)}`
    })
    .join(' | ')
}

function pushLocationDebug(label: string, details?: string | Record<string, unknown>) {
  const entry = {
    id: ++locationDebugSequence,
    time: new Date().toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }),
    label,
    details: stringifyLocationDebugDetails(details),
  }

  locationDebugEntries.value = [entry, ...locationDebugEntries.value].slice(0, MAX_LOCATION_DEBUG_ENTRIES)
  console.info('[location-debug]', label, details ?? '')
}

function pushPositionDebug(label: string, position: GeolocationPosition, extra?: Record<string, unknown>) {
  pushLocationDebug(label, {
    lat: position.coords.latitude,
    lng: position.coords.longitude,
    accuracy: position.coords.accuracy,
    ...(extra ?? {}),
  })
}

function formatLocationAccuracy(accuracyM: number) {
  if (accuracyM >= 1000) {
    return `${(accuracyM / 1000).toFixed(1)} کیلومتر`
  }

  return `${Math.round(accuracyM)} متر`
}

function isAccurateEnough(position: GeolocationPosition) {
  return Number.isFinite(position.coords.accuracy) && position.coords.accuracy <= DESIRED_LOCATION_ACCURACY_METERS
}

function getBetterPosition(currentBest: GeolocationPosition | null, candidate: GeolocationPosition) {
  if (!currentBest) {
    return candidate
  }

  return candidate.coords.accuracy < currentBest.coords.accuracy ? candidate : currentBest
}

function getDistanceBetweenCoordinatesMeters(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
) {
  const toRad = (value: number) => (value * Math.PI) / 180
  const earthRadiusM = 6371000
  const dLat = toRad(lat2 - lat1)
  const dLng = toRad(lng2 - lng1)
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
  return earthRadiusM * c
}

function getPositionConsistencyThresholdMeters(primary: GeolocationPosition, confirmation: GeolocationPosition) {
  const combinedAccuracy = Math.round((primary.coords.accuracy || 0) + (confirmation.coords.accuracy || 0))
  return Math.max(
    MIN_AUTO_LOCATION_CONFIRM_DISTANCE_METERS,
    Math.min(MAX_AUTO_LOCATION_CONFIRM_DISTANCE_METERS, combinedAccuracy),
  )
}

function isWithinIranLocationBounds(lat: number, lng: number) {
  return lat >= IRAN_LOCATION_BOUNDS.minLat
    && lat <= IRAN_LOCATION_BOUNDS.maxLat
    && lng >= IRAN_LOCATION_BOUNDS.minLng
    && lng <= IRAN_LOCATION_BOUNDS.maxLng
}

function getCurrentLocationMapCenter() {
  const center = mapRef.value?.leafletObject?.getCenter()
  if (center) {
    return { lat: center.lat, lng: center.lng }
  }

  return { lat: mapCenter.value[0], lng: mapCenter.value[1] }
}

function shouldPromoteDetectedLocation(accuracy: number) {
  return hasConfirmedAutoLocation.value || accuracy <= MAX_AUTO_SELECTION_PROMOTION_ACCURACY_METERS
}

function shouldPreviewDetectedLocation(lat: number, lng: number, accuracy: number) {
  if (accuracy <= MAX_AUTO_SELECTION_PROMOTION_ACCURACY_METERS) {
    return true
  }

  if (accuracy > MAX_COARSE_PREVIEW_ACCURACY_METERS) {
    return false
  }

  if (isWithinIranLocationBounds(lat, lng)) {
    return true
  }

  const currentCenter = getCurrentLocationMapCenter()
  const distanceToCurrentCenter = getDistanceBetweenCoordinatesMeters(
    currentCenter.lat,
    currentCenter.lng,
    lat,
    lng,
  )

  return distanceToCurrentCenter <= MAX_COARSE_PREVIEW_DISTANCE_METERS
}

function updateResolvedLocationStatus(position: GeolocationPosition) {
  const accuracyLabel = formatLocationAccuracy(position.coords.accuracy)
  if (isAccurateEnough(position)) {
    setLocationStatus(`موقعیت شما پیدا شد. دقت فعلی حدود ${accuracyLabel} است.`, 'info')
    return
  }

  setLocationStatus(`موقعیت خودکار هنوز دقیق نیست و دقت فعلی حدود ${accuracyLabel} است. GPS دقیق دستگاه را روشن کنید یا پین را دستی روی نقطه درست تنظیم کنید.`, 'error')
}

function applyDetectedLocation(position: GeolocationPosition) {
  const lat = position.coords.latitude
  const lng = position.coords.longitude
  const accuracy = Math.max(15, Math.round(position.coords.accuracy || 0))
  const shouldPromoteSelection = shouldPromoteDetectedLocation(accuracy)
  const shouldPreviewMap = shouldPreviewDetectedLocation(lat, lng, accuracy)
  const preserveManualSelection = hasManualLocationSelection.value && !shouldPromoteSelection

  pushPositionDebug('apply-detected', position)

  detectedLocationLatLng.value = [lat, lng]
  detectedLocationAccuracyM.value = accuracy

  if (shouldPromoteSelection) {
    selectedLatLng.value = { lat, lng }
    hasManualLocationSelection.value = false
  } else if (!hasManualLocationSelection.value) {
    selectedLatLng.value = null
  }

  if (!shouldPromoteSelection) {
    pushLocationDebug('detected-preview-only', {
      accuracy,
      inIran: isWithinIranLocationBounds(lat, lng),
      preview: shouldPreviewMap,
      preserveManual: preserveManualSelection,
    })
  }

  if (shouldPreviewMap && !preserveManualSelection) {
    mapCenter.value = [lat, lng]
    const map = mapRef.value?.leafletObject
    if (map) {
      const targetZoom = accuracy <= 60 ? 18 : accuracy <= 150 ? 17 : accuracy <= 400 ? 16 : 15
      isProgrammaticMapMove = true
      map.setView([lat, lng], targetZoom)
    }
  } else if (!shouldPreviewMap) {
    pushLocationDebug('detected-ignored', {
      lat,
      lng,
      accuracy,
      reason: 'coarse-outlier',
    })
  }
}

function applyConfirmedAutoLocation(position: GeolocationPosition) {
  hasConfirmedAutoLocation.value = true
  pushPositionDebug('auto-confirmed', position)
  applyDetectedLocation(position)
}

function requestCurrentPosition(options: PositionOptions) {
  return new Promise<GeolocationPosition>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, options)
  })
}

async function requestStableAutoConfirmation(basePosition: GeolocationPosition, lookupId: number) {
  try {
    const confirmationPosition = await requestCurrentPosition({
      enableHighAccuracy: true,
      timeout: AUTO_LOCATION_CONFIRM_TIMEOUT_MS,
      maximumAge: 0,
    })

    if (!isCurrentLocationLookup(lookupId)) {
      return { position: null, unstable: false }
    }

    pushPositionDebug('confirm-reading', confirmationPosition)

    if (!isAccurateEnough(confirmationPosition)) {
      pushLocationDebug('confirm-rejected', {
        reason: 'accuracy',
        accuracy: confirmationPosition.coords.accuracy,
      })
      return { position: null, unstable: false }
    }

    const distanceMeters = getDistanceBetweenCoordinatesMeters(
      basePosition.coords.latitude,
      basePosition.coords.longitude,
      confirmationPosition.coords.latitude,
      confirmationPosition.coords.longitude,
    )
    const allowedDistanceMeters = getPositionConsistencyThresholdMeters(basePosition, confirmationPosition)
    if (distanceMeters > allowedDistanceMeters) {
      pushLocationDebug('confirm-rejected', {
        reason: 'distance-mismatch',
        distance: distanceMeters,
        allowed: allowedDistanceMeters,
      })
      return { position: null, unstable: true }
    }

    const confirmedPosition = getBetterPosition(basePosition, confirmationPosition)
    applyConfirmedAutoLocation(confirmedPosition)
    return { position: confirmedPosition, unstable: false }
  } catch (error) {
    const geoError = error as GeolocationPositionError
    pushLocationDebug('confirm-error', {
      code: geoError?.code ?? 'unknown',
      message: geoError?.message ?? 'unknown',
    })
    return { position: null, unstable: false }
  }
}

function clearLocationWatch() {
  if (locationWatchId !== null) {
    navigator.geolocation.clearWatch(locationWatchId)
    locationWatchId = null
  }
}

function requestWatchPosition(options: PositionOptions, waitMs = 25000) {
  return new Promise<GeolocationPosition>((resolve, reject) => {
    let settled = false
    const timeoutId = window.setTimeout(() => {
      if (settled) return
      settled = true
      clearLocationWatch()
      reject({
        code: 3,
        message: 'watchPosition timed out',
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
      } as GeolocationPositionError)
    }, waitMs)

    locationWatchId = navigator.geolocation.watchPosition(
      (position) => {
        if (settled) return
        settled = true
        window.clearTimeout(timeoutId)
        clearLocationWatch()
        resolve(position)
      },
      (error) => {
        if (settled) return
        settled = true
        window.clearTimeout(timeoutId)
        clearLocationWatch()
        reject(error)
      },
      options,
    )
  })
}

function requestBestWatchPosition(
  options: PositionOptions,
  waitMs = 30000,
  desiredAccuracyM = DESIRED_LOCATION_ACCURACY_METERS,
  onProgress?: (position: GeolocationPosition) => void,
) {
  return new Promise<GeolocationPosition>((resolve, reject) => {
    let settled = false
    let bestPosition: GeolocationPosition | null = null

    const finish = () => {
      if (settled) return
      settled = true
      window.clearTimeout(timeoutId)
      clearLocationWatch()
      if (bestPosition) {
        resolve(bestPosition)
        return
      }

      reject({
        code: 3,
        message: 'watchPosition timed out',
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
      } as GeolocationPositionError)
    }

    const timeoutId = window.setTimeout(() => {
      finish()
    }, waitMs)

    locationWatchId = navigator.geolocation.watchPosition(
      (position) => {
        bestPosition = getBetterPosition(bestPosition, position)
        onProgress?.(bestPosition)
        if (bestPosition.coords.accuracy <= desiredAccuracyM) {
          finish()
        }
      },
      (error) => {
        if (settled) return
        window.clearTimeout(timeoutId)
        clearLocationWatch()
        if (bestPosition) {
          settled = true
          resolve(bestPosition)
          return
        }

        settled = true
        reject(error)
      },
      options,
    )
  })
}

async function getGeolocationPermissionState() {
  try {
    if (!('permissions' in navigator) || !navigator.permissions?.query) {
      return null
    }

    const status = await navigator.permissions.query({ name: 'geolocation' })
    return status.state
  } catch {
    return null
  }
}

function getLocationErrorMessage(error: GeolocationPositionError | null) {
  if (!error) {
    return 'امکان دریافت موقعیت شما وجود ندارد. GPS دستگاه را بررسی کنید یا پین را دستی روی نقشه تنظیم کنید.'
  }

  if (error.code === error.PERMISSION_DENIED) {
    return 'دسترسی به موقعیت مکانی مسدود است. مجوز Location را برای این سایت دوباره فعال کنید یا پین را دستی روی نقشه تنظیم کنید.'
  }

  if (error.code === error.TIMEOUT) {
    return 'موقعیت شما در زمان مناسب پیدا نشد. GPS یا اینترنت را بررسی کنید یا پین را دستی روی نقشه تنظیم کنید.'
  }

  if (error.code === error.POSITION_UNAVAILABLE) {
    return 'مرورگر نتوانست موقعیت دقیق شما را پیدا کند. GPS دقیق دستگاه را روشن کنید یا پین را دستی روی نقشه تنظیم کنید.'
  }

  return 'امکان دریافت موقعیت شما وجود ندارد. GPS دستگاه را بررسی کنید یا پین را دستی روی نقشه تنظیم کنید.'
}

async function goToMyLocation(silent = false) {
  const lookupId = ++activeLocationLookupId
  let autoLocationRejectedAsUnstable = false

  pushLocationDebug('lookup-start', {
    lookupId,
    silent,
    secure: typeof window !== 'undefined' ? window.isSecureContext : false,
  })

  if (!navigator.geolocation) {
    const message = 'مرورگر شما از مکان‌یابی پشتیبانی نمی‌کند.'
    setLocationStatus(message, 'error')
    return
  }

  if (typeof window !== 'undefined' && !window.isSecureContext) {
    const message = 'برای دریافت موقعیت خودکار، این صفحه باید روی HTTPS یا localhost باز شود.'
    setLocationStatus(message, 'error')
    return
  }

  isLocating.value = true
  hasConfirmedAutoLocation.value = false
  setLocationStatus('در حال یافتن موقعیت شما...', 'info')

  try {
    const permissionState = await getGeolocationPermissionState()
    if (!isCurrentLocationLookup(lookupId)) return

    pushLocationDebug('permission-state', {
      state: permissionState ?? 'unknown',
    })

    if (permissionState === 'denied') {
      throw {
        code: 1,
        message: 'Geolocation permission denied by Permissions API',
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
      } as GeolocationPositionError
    }

    let position: GeolocationPosition
    let bestPosition: GeolocationPosition | null = null

    try {
      position = await requestCurrentPosition({
        enableHighAccuracy: false,
        timeout: 12000,
        maximumAge: 0,
      })
      if (!isCurrentLocationLookup(lookupId)) return
      pushPositionDebug('initial-reading', position)
      bestPosition = getBetterPosition(bestPosition, position)
      applyDetectedLocation(bestPosition)
      setLocationStatus('موقعیت اولیه پیدا شد. در حال بهبود دقت...', 'info')
    } catch (error) {
      const geoError = error as GeolocationPositionError
      if (geoError?.code === geoError.PERMISSION_DENIED) {
        throw geoError
      }

      try {
        setLocationStatus('در حال تلاش برای دریافت موقعیت دقیق‌تر...', 'info')
        position = await requestCurrentPosition({
          enableHighAccuracy: true,
          timeout: 20000,
          maximumAge: 0,
        })
        if (!isCurrentLocationLookup(lookupId)) return
        pushPositionDebug('fallback-precise-reading', position)
      } catch (preciseError) {
        const preciseGeoError = preciseError as GeolocationPositionError
        if (preciseGeoError?.code === preciseGeoError.PERMISSION_DENIED) {
          throw preciseGeoError
        }

        setLocationStatus('در انتظار پاسخ GPS دستگاه...', 'info')
        position = await requestWatchPosition({
          enableHighAccuracy: true,
          timeout: 30000,
          maximumAge: 0,
        }, 30000)
        if (!isCurrentLocationLookup(lookupId)) return
        pushPositionDebug('watch-first-reading', position)
      }
    }

    if (!bestPosition) {
      bestPosition = position
      applyDetectedLocation(bestPosition)
    }

    if (isAccurateEnough(bestPosition)) {
      setLocationStatus('موقعیت دقیق پیدا شد. در حال تایید نهایی GPS...', 'info')
      const confirmation = await requestStableAutoConfirmation(bestPosition, lookupId)
      if (!isCurrentLocationLookup(lookupId)) return
      if (confirmation.position) {
        updateResolvedLocationStatus(confirmation.position)
        return
      }
      autoLocationRejectedAsUnstable = autoLocationRejectedAsUnstable || confirmation.unstable
    }

    try {
      setLocationStatus('در حال دریافت موقعیت دقیق و تازه...', 'info')
      const precisePosition = await requestCurrentPosition({
        enableHighAccuracy: true,
        timeout: 18000,
        maximumAge: 0,
      })
      if (!isCurrentLocationLookup(lookupId)) return
      pushPositionDebug('fresh-precise-reading', precisePosition)
      bestPosition = getBetterPosition(bestPosition, precisePosition)
      applyDetectedLocation(bestPosition)
      if (isAccurateEnough(bestPosition)) {
        setLocationStatus('موقعیت دقیق پیدا شد. در حال تایید نهایی GPS...', 'info')
        const confirmation = await requestStableAutoConfirmation(bestPosition, lookupId)
        if (!isCurrentLocationLookup(lookupId)) return
        if (confirmation.position) {
          updateResolvedLocationStatus(confirmation.position)
          return
        }
        autoLocationRejectedAsUnstable = autoLocationRejectedAsUnstable || confirmation.unstable
      }
    } catch (preciseError) {
      const preciseGeoError = preciseError as GeolocationPositionError
      if (preciseGeoError?.code === preciseGeoError.PERMISSION_DENIED) {
        throw preciseGeoError
      }
    }

    if (!isAccurateEnough(bestPosition)) {
      setLocationStatus('در انتظار بهبود دقت GPS دستگاه...', 'info')
      try {
        const watchedPosition = await requestBestWatchPosition(
          {
            enableHighAccuracy: true,
            timeout: 30000,
            maximumAge: 0,
          },
          30000,
          DESIRED_LOCATION_ACCURACY_METERS,
          (candidate) => {
            if (!isCurrentLocationLookup(lookupId)) return
            pushPositionDebug('watch-progress', candidate)
            bestPosition = getBetterPosition(bestPosition, candidate)
            applyDetectedLocation(bestPosition)
          },
        )
        if (!isCurrentLocationLookup(lookupId)) return
        bestPosition = getBetterPosition(bestPosition, watchedPosition)
        applyDetectedLocation(bestPosition)
        if (isAccurateEnough(bestPosition)) {
          setLocationStatus('موقعیت دقیق پیدا شد. در حال تایید نهایی GPS...', 'info')
          const confirmation = await requestStableAutoConfirmation(bestPosition, lookupId)
          if (!isCurrentLocationLookup(lookupId)) return
          if (confirmation.position) {
            updateResolvedLocationStatus(confirmation.position)
            return
          }
          autoLocationRejectedAsUnstable = autoLocationRejectedAsUnstable || confirmation.unstable
        }
      } catch (watchError) {
        const watchGeoError = watchError as GeolocationPositionError
        if (watchGeoError?.code === watchGeoError.PERMISSION_DENIED) {
          throw watchGeoError
        }
      }
    }

    if (!hasConfirmedAutoLocation.value && bestPosition) {
      if (autoLocationRejectedAsUnstable && isAccurateEnough(bestPosition)) {
        const message = 'مرورگر دو موقعیت متناقض برگرداند. برای جلوگیری از ارسال لوکیشن اشتباه، GPS را روشن نگه دارید و دوباره تلاش کنید یا پین را دستی روی نقطه درست تنظیم کنید.'
        setLocationStatus(message, 'error')
        return
      }

      if (isAccurateEnough(bestPosition)) {
        const message = 'یک موقعیت دقیق پیدا شد اما تایید دوم از GPS دریافت نشد. برای جلوگیری از لوکیشن اشتباه، کمی صبر کنید یا پین را دستی روی نقطه درست تنظیم کنید.'
        setLocationStatus(message, 'error')
        return
      }

      updateResolvedLocationStatus(bestPosition)
      return
    }

    if (!hasConfirmedAutoLocation.value) {
      const message = 'موقعیت دقیق و تازه از GPS دستگاه دریافت نشد. برای جلوگیری از لوکیشن اشتباه، پین را دستی روی نقطه درست تنظیم کنید.'
      setLocationStatus(message, 'error')
      return
    }

    if (!isCurrentLocationLookup(lookupId)) return
    updateResolvedLocationStatus(bestPosition)
  } catch (error) {
    if (!isCurrentLocationLookup(lookupId)) return
    const geoError = error as GeolocationPositionError
    const message = getLocationErrorMessage(geoError)
    console.error('Geolocation error:', geoError)
    pushLocationDebug('lookup-error', {
      code: geoError?.code ?? 'unknown',
      message: geoError?.message ?? 'unknown',
    })
    setLocationStatus(message, 'error')
  } finally {
    if (isCurrentLocationLookup(lookupId)) {
      clearLocationWatch()
      isLocating.value = false
      pushLocationDebug('lookup-finished', {
        sendable: canSendLocation.value,
      })
    }
  }
}

function sendLocation() {
  pushLocationDebug('send-attempt', {
    selected: selectedLatLngDebugText.value,
    detected: detectedLatLngDebugText.value,
    sendable: canSendLocation.value,
    manual: hasManualLocationSelection.value,
    confirmed: hasConfirmedAutoLocation.value,
  })

  if (!selectedLatLng.value) {
    const message = 'ابتدا موقعیت خود را پیدا کنید یا نقشه را روی نقطه دلخواه جابه‌جا کنید.'
    setLocationStatus(message, 'error')
    return
  }

  if (!canSendLocation.value) {
    const message = 'تا وقتی موقعیت خودکار دقیق نشود، ابتدا پین را دستی روی نقطه درست تنظیم کنید.'
    setLocationStatus(message, 'error')
    return
  }

  emit('select-location', selectedLatLng.value.lat, selectedLatLng.value.lng)
  close()
}

onBeforeUnmount(() => {
  clearLocationWatch()
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

.location-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
  line-height: 1.5;
}

.location-status.is-error {
  background: #fef2f2;
  color: #b91c1c;
}

.location-status-action {
  flex-shrink: 0;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.78);
  color: inherit;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.precise-location-guide {
  margin: 12px 16px 0;
  padding: 14px 14px 12px;
  border-radius: 16px;
  background: linear-gradient(180deg, #fff7ed 0%, #fffbeb 100%);
  border: 1px solid #fed7aa;
  color: #7c2d12;
  box-shadow: 0 10px 24px rgba(194, 65, 12, 0.08);
}

.precise-location-guide-header {
  display: flex;
  gap: 10px;
}

.precise-location-guide-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(249, 115, 22, 0.12);
  color: #c2410c;
  font-size: 11px;
  font-weight: 700;
}

.precise-location-guide-title {
  margin-top: 8px;
  font-size: 15px;
  font-weight: 800;
  color: #9a3412;
}

.precise-location-guide-description {
  margin: 8px 0 0;
  color: #7c2d12;
  font-size: 13px;
  line-height: 1.7;
}

.precise-location-guide-chooser {
  margin-top: 12px;
  display: grid;
  gap: 10px;
}

.precise-location-guide-chooser-title {
  font-size: 13px;
  font-weight: 700;
  color: #9a3412;
}

.precise-location-guide-choice-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.precise-location-guide-choice {
  border: 1px solid #fdba74;
  background: rgba(255, 255, 255, 0.76);
  color: #9a3412;
  padding: 11px 10px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.precise-location-guide-choice:active {
  transform: scale(0.98);
}

.precise-location-guide-steps {
  margin: 12px 0 0;
  padding: 0 18px 0 0;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.75;
  color: #7c2d12;
}

.precise-location-guide-note {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.72);
  color: #9a3412;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.7;
}

.precise-location-guide-dismiss {
  margin-top: 12px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #7c2d12;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.precise-location-guide-dismiss input {
  width: 18px;
  height: 18px;
  accent-color: #ea580c;
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

.send-location-btn:disabled {
  background: #93c5fd;
  cursor: not-allowed;
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
