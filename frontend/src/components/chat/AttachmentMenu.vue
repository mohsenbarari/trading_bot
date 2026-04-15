<template>
  <teleport to="body">
    <!-- Backdrop -->
    <transition name="fade">
      <div v-if="modelValue" class="attachment-backdrop" @click="close"></div>
    </transition>

    <!-- Bottom Sheet -->
    <transition name="slide-up">
      <div
        v-if="modelValue"
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
            <input ref="cameraInput" type="file" accept="image/*" capture="environment" style="display:none" @change="onGalleryFile" />
            <input ref="galleryInput" type="file" accept="image/*,video/*" multiple style="display:none" @change="onGalleryFile" />

            <button class="action-card" @click="cameraInput?.click()">
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
import { ref, watch } from 'vue'
import imageCompression from 'browser-image-compression'
import 'leaflet/dist/leaflet.css'
import { LMap, LTileLayer } from '@vue-leaflet/vue-leaflet'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
  (e: 'select-media', file: File): void
  (e: 'select-file', file: File): void
  (e: 'select-location', lat: number, lng: number): void
}>()

const activeTab = ref<'gallery' | 'file' | 'location'>('gallery')
const cameraInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const sheetRef = ref<HTMLElement | null>(null)
const mapRef = ref<any>(null)

// Default: Tehran
const mapCenter = ref<[number, number]>([35.6892, 51.3890])
const selectedLatLng = ref<{ lat: number; lng: number }>({ lat: 35.6892, lng: 51.3890 })

const tileUrl = ref('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')

const tabs = [
  { id: 'gallery' as const, label: 'گالری', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>' },
  { id: 'file' as const, label: 'فایل', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' },
  { id: 'location' as const, label: 'موقعیت', icon: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>' },
]

// Swipe to dismiss
let startY = 0
let currentTranslateY = 0

function onTouchStart(e: TouchEvent) {
  const touch = e.touches[0]
  if (!touch) return
  startY = touch.clientY
  currentTranslateY = 0
}

function onTouchMove(e: TouchEvent) {
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
  if (currentTranslateY > 100) {
    close()
  } else if (sheetRef.value) {
    sheetRef.value.style.transform = ''
  }
  currentTranslateY = 0
}

function close() {
  emit('update:modelValue', false)
}

// Reset tab on open
watch(() => props.modelValue, (val) => {
  if (val) activeTab.value = 'gallery'
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

// Gallery file handler (compresses images)
async function onGalleryFile(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return

  for (const file of Array.from(input.files)) {
    if (file.type.startsWith('image/') && !file.type.includes('gif')) {
      try {
        const compressed = await imageCompression(file, {
          maxSizeMB: 0.5,
          maxWidthOrHeight: 1280,
          useWebWorker: false
        })
        emit('select-media', compressed)
      } catch {
        emit('select-media', file)
      }
    } else {
      emit('select-media', file)
    }
  }
  input.value = ''
  close()
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
</script>

<style scoped>
.attachment-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 999;
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
  will-change: transform, height, max-height, border-radius;
}

.full-screen-sheet {
  max-height: 100vh !important;
  height: 100dvh !important;
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
</style>
