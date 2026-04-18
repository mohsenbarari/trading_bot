<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { usePWAInstall } from '../utils/pwaInstall'

const { isInstallable, isInstalled, installApp } = usePWAInstall()
const showOverlay = ref(false)

onMounted(() => {
  // Check if we should show the prompt (not installed and haven't dismissed it recently)
  const lastDismissed = localStorage.getItem('pwa_prompt_dismissed')
  const now = Date.now()
  
  // Wait 3 seconds after load to show the prompt for better UX
  setTimeout(() => {
    if (isInstallable.value && !isInstalled.value) {
        if (!lastDismissed || (now - parseInt(lastDismissed)) > 24 * 60 * 60 * 1000) {
            showOverlay.value = true
        }
    }
  }, 3000)
})

const dismiss = () => {
    showOverlay.value = false
    localStorage.setItem('pwa_prompt_dismissed', Date.now().toString())
}

const handleInstall = async () => {
    const success = await installApp()
    if (success) {
        showOverlay.value = false
    }
}
</script>

<template>
  <transition name="slide-up">
    <div v-if="showOverlay" class="pwa-install-overlay">
      <div class="pwa-card">
        <div class="pwa-icon">
          <img src="/pwa-192x192.png" alt="App Icon" />
        </div>
        <div class="pwa-info">
          <h3>نصب اپلیکیشن</h3>
          <p>برای دسترسی سریع‌تر و تجربه بهتر، نسخه اپلیکیشن را نصب کنید.</p>
        </div>
        <div class="pwa-actions">
          <button class="btn-dismiss" @click="dismiss">بعداً</button>
          <button class="btn-install" @click="handleInstall">نصب</button>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.pwa-install-overlay {
  position: fixed;
  bottom: 100px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  padding: 0 16px;
  z-index: 1000;
  pointer-events: none;
}

.pwa-card {
  background: white;
  border-radius: 16px;
  padding: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  width: 100%;
  max-width: 400px;
  pointer-events: auto;
  border: 1px solid rgba(0,0,0,0.05);
}

.pwa-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  overflow: hidden;
  flex-shrink: 0;
  background: #f3f4f6;
}

.pwa-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pwa-info {
  flex: 1;
}

.pwa-info h3 {
  font-size: 15px;
  font-weight: 700;
  margin: 0 0 2px 0;
  color: #1f2937;
}

.pwa-info p {
  font-size: 13px;
  color: #6b7280;
  margin: 0;
  line-height: 1.4;
}

.pwa-actions {
  display: flex;
  gap: 8px;
}

.btn-dismiss {
  padding: 8px 12px;
  font-size: 14px;
  color: #6b7280;
  background: transparent;
  border: none;
  border-radius: 8px;
}

.btn-install {
  padding: 8px 16px;
  font-size: 14px;
  font-weight: 600;
  color: white;
  background: #f59e0b;
  border: none;
  border-radius: 8px;
}

.slide-up-enter-active, .slide-up-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.slide-up-enter-from, .slide-up-leave-to {
  transform: translateY(20px);
  opacity: 0;
}
</style>
