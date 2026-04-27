<script setup lang="ts">
import { ref, computed } from 'vue'
import 'leaflet/dist/leaflet.css'
import { LMap, LTileLayer } from '@vue-leaflet/vue-leaflet'

const emit = defineEmits<{
  (e: 'close'): void
}>()

const props = defineProps<{
  location: {
    lat?: number
    lng?: number
    latitude?: number
    longitude?: number
  } | null
}>()

const normalizedLocation = computed(() => {
  if (!props.location) {
    return null
  }

  const lat = Number(props.location.lat ?? props.location.latitude)
  const lng = Number(props.location.lng ?? props.location.longitude)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null
  }

  return { lat, lng }
})

const locationKey = computed(() => (
  normalizedLocation.value
    ? `${normalizedLocation.value.lat}:${normalizedLocation.value.lng}`
    : 'location-empty'
))

const tileUrl = computed(() => 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')

function openExternalMap() {
  if (!normalizedLocation.value) return
  window.open(`https://www.google.com/maps?q=${normalizedLocation.value.lat},${normalizedLocation.value.lng}`, '_blank')
}

</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="normalizedLocation" class="location-overlay" @click="emit('close')">
        <div class="location-content" @click.stop>
          <div class="location-header">
            <span class="location-title" style="flex:1">موقعیت مکانی</span>
            
            <button class="header-btn" title="باز کردن در برنامه‌ی دیگر" @click="openExternalMap" style="margin-left: 8px;">
               <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                 <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line>
               </svg>
            </button>
            <button class="header-btn" @click="emit('close')">
              <svg viewBox="0 0 24 24" width="24" height="24" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
            </button>
          </div>
          <div class="map-container">
            <l-map
              :key="locationKey"
              :zoom="15"
              :center="[normalizedLocation.lat, normalizedLocation.lng]"
              :use-global-leaflet="false"
              class="location-map"
              :options="{ zoomControl: true }"
            >
              <l-tile-layer :url="tileUrl"></l-tile-layer>
            </l-map>
            <!-- Fixed center pin instead of LMarker to avoid Vite icon bug -->
            <div class="center-pin">
              <svg viewBox="0 0 24 36" width="36" height="48" fill="#E53935">
                <path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 24 12 24s12-15 12-24C24 5.4 18.6 0 12 0zm0 18c-3.3 0-6-2.7-6-6s2.7-6 6-6 6 2.7 6 6-2.7 6-6 6z"/>
              </svg>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.location-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  backdrop-filter: blur(4px);
}
.location-content {
  position: relative;
  width: 90vw;
  max-width: 500px;
  height: 70vh;
  max-height: 600px;
  background: #fff;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 10px 40px rgba(0,0,0,0.3);
}
.location-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f8f9fa;
  border-bottom: 1px solid #e5e7eb;
}
.location-title {
  font-weight: 600;
  font-size: 16px;
  color: #111827;
}
.header-btn {
  background: none;
  border: none;
  color: #4b5563;
  cursor: pointer;
  padding: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  transition: background 0.2s;
}
.header-btn:hover {
  background: #e5e7eb;
}
.map-container {
  flex: 1;
  position: relative;
  width: 100%;
  height: 100%;
}
.location-map {
  width: 100%;
  height: 100%;
  z-index: 0;
}
.center-pin {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -100%);
  pointer-events: none;
  z-index: 1000;
  filter: drop-shadow(0px 4px 4px rgba(0,0,0,0.3));
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
