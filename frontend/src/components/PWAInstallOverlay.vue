<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { usePWAInstall } from '../utils/pwaInstall'

const { isInstallable, isInstalled, installApp } = usePWAInstall()
const showOverlay = ref(false)

// تشخیص سیستم‌عامل برای نمایش راهنمای اختصاصی
const isIOS = computed(() => {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as any).MSStream
})

onMounted(() => {
  const lastDismissed = localStorage.getItem('pwa_prompt_dismissed')
  const now = Date.now()
  
  setTimeout(() => {
    // در اندروید/دسکتاپ از isInstallable استفاده می‌کنیم (Chrome/Edge)
    // در iOS چون رویداد beforeinstallprompt نداریم، همیشه دکمه راهنما را نشان می‌دهیم اگر نصب نشده باشد
    const shouldShowForAndroid = isInstallable.value && !isInstalled.value
    const shouldShowForIOS = isIOS.value && !isInstalled.value && !((window.navigator as any).standalone)

    if (shouldShowForAndroid || shouldShowForIOS) {
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
    if (isIOS.value) {
        // در iOS فقط راهنما نشان می‌دهیم چون API نصب خودکار وجود ندارد
        alert('در مرورگر Safari، روی دکمه Share (پایین صفحه) بزنید و سپس گزینه "Add to Home Screen" را انتخاب کنید.')
        return
    }
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
          <p v-if="isIOS">برای نصب در آیفون، از منوی پایین Safari گزینه Add to Home Screen را بزنید.</p>
          <p v-else>برای دسترسی سریع‌تر و تجربه بهتر، نسخه اپلیکیشن را نصب کنید.</p>
        </div>
        <div class="pwa-actions">
          <button class="btn-dismiss" @click="dismiss">بعداً</button>
          <button class="btn-install" @click="handleInstall">
            {{ isIOS ? 'راهنما' : 'نصب' }}
          </button>
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
