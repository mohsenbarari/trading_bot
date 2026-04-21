<template>
  <teleport to="body">
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

            <button class="camera-icon-btn" @click="toggleFacingMode" :disabled="isRecording || isCameraStarting" title="تغییر دوربین">
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

            <div v-else-if="cameraError" class="camera-status-overlay error">
              <div>{{ cameraError }}</div>
              <button class="camera-error-btn" @click="startCameraStream">تلاش مجدد</button>
            </div>

            <div v-if="isRecording" class="camera-recording-badge">
              <span class="recording-dot"></span>
              {{ formattedRecordingTime }}
            </div>
          </div>

          <div class="camera-control-bar">
            <button
              class="camera-shutter-btn"
              :class="{ recording: isRecording, video: cameraMode === 'video' }"
              :disabled="!isCameraReady"
              @click="handlePrimaryCameraAction"
            >
              <span class="camera-shutter-core"></span>
            </button>
            <div class="camera-capture-label">
              {{ cameraMode === 'photo' ? 'ثبت عکس' : (isRecording ? 'توقف ضبط' : 'شروع ضبط ویدئو') }}
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
            <input ref="cameraInput" type="file" accept="image/*,video/*" capture="environment" style="display:none" @change="onGalleryFile" />
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
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import 'leaflet/dist/leaflet.css'
import { LMap, LTileLayer } from '@vue-leaflet/vue-leaflet'

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
const cameraInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const sheetRef = ref<HTMLElement | null>(null)
const mapRef = ref<any>(null)
const cameraPreviewRef = ref<HTMLVideoElement | null>(null)

const showCameraCapture = ref(false)
const cameraMode = ref<'photo' | 'video'>('photo')
const activeFacingMode = ref<'environment' | 'user'>('environment')
const isCameraStarting = ref(false)
const cameraError = ref('')
const isRecording = ref(false)
const recordingSeconds = ref(0)

let cameraStream: MediaStream | null = null
let mediaRecorder: MediaRecorder | null = null
let recordedChunks: BlobPart[] = []
let recordingTimer: number | null = null

// Default: Tehran
const mapCenter = ref<[number, number]>([35.6892, 51.3890])
const selectedLatLng = ref<{ lat: number; lng: number }>({ lat: 35.6892, lng: 51.3890 })

const tileUrl = ref('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')

const isCameraReady = computed(() => Boolean(cameraStream) && !isCameraStarting.value && !cameraError.value)
const formattedRecordingTime = computed(() => {
  const mins = Math.floor(recordingSeconds.value / 60)
  const secs = recordingSeconds.value % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
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

function stopCameraTracks() {
  if (cameraPreviewRef.value) {
    try {
      cameraPreviewRef.value.pause()
      cameraPreviewRef.value.srcObject = null
    } catch {
      // Ignore preview cleanup failures.
    }
  }

  cameraStream?.getTracks().forEach((track) => track.stop())
  cameraStream = null
}

function cleanupCamera(discardRecording = true) {
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
  cameraError.value = ''
  isCameraStarting.value = false
  showCameraCapture.value = false
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
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraInput.value?.click()
    return
  }

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

    cameraStream = stream
    showCameraCapture.value = true
    await attachCameraStream(stream)
  } catch (error) {
    console.error('Camera start error:', error)
    cameraError.value = 'امکان دسترسی به دوربین وجود ندارد. لطفا دسترسی مرورگر را بررسی کنید.'
    showCameraCapture.value = true
  } finally {
    isCameraStarting.value = false
  }
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

async function emitCapturedMedia(file: File) {
  cleanupCamera(true)
  emit('update:modelValue', false)
  emit('select-media', file)
}

async function capturePhoto() {
  const preview = cameraPreviewRef.value
  if (!preview || !cameraStream) return

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
  await emitCapturedMedia(file)
}

function startVideoRecording() {
  if (!cameraStream) return
  if (typeof MediaRecorder === 'undefined') {
    alert('مرورگر شما از فیلم‌برداری پشتیبانی نمی‌کند.')
    return
  }

  recordedChunks = []
  const mimeType = getSupportedVideoMimeType()

  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(cameraStream, { mimeType })
      : new MediaRecorder(cameraStream)
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
    await emitCapturedMedia(file)
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
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraInput.value?.click()
    return
  }

  cameraMode.value = 'photo'
  activeFacingMode.value = 'environment'
  showCameraCapture.value = true
  void startCameraStream()
}

async function setCameraMode(mode: 'photo' | 'video') {
  if (cameraMode.value === mode || isRecording.value) return
  cameraMode.value = mode

  if (showCameraCapture.value) {
    await startCameraStream()
  }
}

async function toggleFacingMode() {
  if (isRecording.value) return
  activeFacingMode.value = activeFacingMode.value === 'environment' ? 'user' : 'environment'

  if (showCameraCapture.value) {
    await startCameraStream()
  }
}

function closeCameraCapture() {
  cleanupCamera(true)
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
async function onGalleryFile(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return

  const files = Array.from(input.files)
  const albumId = files.length > 1
    ? (globalThis.crypto?.randomUUID?.() ?? `album_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`)
    : null

  input.value = ''
  close()

  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame !== 'function') {
      setTimeout(resolve, 0)
      return
    }

    requestAnimationFrame(() => resolve())
  })

  files.forEach((file, index) => {
    emit('select-media', file, albumId, index, files.length)
  })
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

  .camera-mode-btn {
    padding: 8px 12px;
  }

  .camera-control-bar {
    padding: 14px 14px calc(18px + env(safe-area-inset-bottom));
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
