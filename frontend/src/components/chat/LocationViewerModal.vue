<template>
  <div v-if="modelValue" class="location-viewer-overlay">
    <div class="location-header">
      <button class="back-btn" @click="close">
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"></path>
        </svg>
      </button>
      <span class="header-title">موقعیت مکانی</span>
      <div class="header-actions">
        <button class="external-btn" title="باز کردن در برنامه نقشه" @click="openExternal">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
            <polyline points="15 3 21 3 21 9"></polyline>
            <line x1="10" y1="14" x2="21" y2="3"></line>
          </svg>
        </button>
      </div>
    </div>
    
    <div class="map-container">
      <l-map
        ref="mapRef"
        :zoom="15"
        :center="[location.lat, location.lng]"
        :use-global-leaflet="false"
      >
        <l-tile-layer
          :url="tileUrl"
          attribution="&copy; OpenStreetMap"
        />
        <l-marker :lat-lng="[location.lat, location.lng]">
          <l-icon :icon-size="[32, 32]" :icon-anchor="[16, 32]">
            <div class="custom-marker">
              <svg viewBox="0 0 24 24" fill="#E53935">
                <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>
              </svg>
            </div>
          </l-icon>
        </l-marker>
      </l-map>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import 'leaflet/dist/leaflet.css'
import { LMap, LTileLayer, LMarker, LIcon } from '@vue-leaflet/vue-leaflet'

const props = defineProps<{
  modelValue: boolean
  location: { lat: number, lng: number }
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

const tileUrl = ref('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')

function close() {
  emit('update:modelValue', false)
}

function openExternal() {
  window.open(`https://www.google.com/maps?q=${props.location.lat},${props.location.lng}`, '_blank')
}
</script>

<style scoped>
.location-viewer-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 10000;
  background: white;
  display: flex;
  flex-direction: column;
}

.location-header {
  height: 56px;
  background: white;
  display: flex;
  align-items: center;
  padding: 0 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  z-index: 10;
}

.back-btn, .external-btn {
  background: none;
  border: none;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 50%;
  color: #64748b;
  padding: 0;
}

.back-btn:active, .external-btn:active {
  background-color: #f1f5f9;
}

.back-btn svg { width: 24px; height: 24px; }
.external-btn svg { width: 20px; height: 20px; margin-right: -4px; }

.header-title {
  flex: 1;
  font-size: 18px;
  font-weight: 500;
  margin: 0 16px;
  color: #1e293b;
}

.map-container {
  flex: 1;
  position: relative;
  z-index: 1;
}

.custom-marker {
  width: 32px;
  height: 32px;
  display: flex;
  justify-content: center;
  align-items: flex-end;
  filter: drop-shadow(0px 2px 4px rgba(0,0,0,0.3));
}
.custom-marker svg {
  width: 100%;
  height: 100%;
}
</style>
