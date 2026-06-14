<script setup lang="ts">
import { ref, onBeforeUnmount, onMounted, computed, watch } from 'vue'
import { usePWAInstall } from '../utils/pwaInstall'
import AppButton from './ui/AppButton.vue'
import AppCard from './ui/AppCard.vue'

const { isInstallable, isInstalled, installApp } = usePWAInstall()
const showOverlay = ref(false)
const showIosGuide = ref(false)
const isPromptDelayElapsed = ref(false)
let promptDelayTimer: number | undefined

const PROMPT_DISMISSED_KEY = 'pwa_install_prompt_dismissed_at_v2'
const PROMPT_DISMISS_TTL_MS = 24 * 60 * 60 * 1000

// تشخیص سیستم‌عامل برای نمایش راهنمای اختصاصی
const isIOS = computed(() => {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as any).MSStream
})

const wasRecentlyDismissed = () => {
  const lastDismissed = localStorage.getItem(PROMPT_DISMISSED_KEY)
  if (!lastDismissed) return false

  const timestamp = Number.parseInt(lastDismissed, 10)
  if (Number.isNaN(timestamp)) return false

  return Date.now() - timestamp <= PROMPT_DISMISS_TTL_MS
}

const maybeShowOverlay = () => {
  if (!isPromptDelayElapsed.value) return
  if (isInstalled.value) {
    showOverlay.value = false
    return
  }

  // در اندروید/دسکتاپ از isInstallable استفاده می‌کنیم (Chrome/Edge).
  // در iOS چون رویداد beforeinstallprompt نداریم، فقط راهنمای نصب نشان می‌دهیم.
  const shouldShowForAndroid = isInstallable.value
  const shouldShowForIOS = isIOS.value && !((window.navigator as any).standalone)

  if ((shouldShowForAndroid || shouldShowForIOS) && !wasRecentlyDismissed()) {
    showOverlay.value = true
  }
}

const handleInstallReady = () => {
  maybeShowOverlay()
}

onMounted(() => {
  window.addEventListener('pwa-install-ready', handleInstallReady)

  promptDelayTimer = window.setTimeout(() => {
    isPromptDelayElapsed.value = true
    maybeShowOverlay()
  }, 3000)
})

onBeforeUnmount(() => {
  window.removeEventListener('pwa-install-ready', handleInstallReady)
  if (promptDelayTimer !== undefined) window.clearTimeout(promptDelayTimer)
})

watch([() => isInstallable.value, () => isInstalled.value], () => {
  maybeShowOverlay()
})

const dismiss = () => {
    showOverlay.value = false
    showIosGuide.value = false
    localStorage.setItem(PROMPT_DISMISSED_KEY, Date.now().toString())
}

const handleInstall = async () => {
    if (isIOS.value) {
        showIosGuide.value = true
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
      <AppCard class="pwa-card">
        <div class="pwa-icon">
          <img src="/pwa-192x192.png" alt="App Icon" />
        </div>
        <div class="pwa-info">
          <span class="pwa-eyebrow">نسخه اپلیکیشن</span>
          <h3>نصب روی صفحه اصلی</h3>
          <p v-if="isIOS && !showIosGuide">برای نصب در آیفون، راهنمای کوتاه نصب را باز کنید.</p>
          <p v-else-if="isIOS" class="ios-guide">در Safari دکمه Share را بزنید و سپس Add to Home Screen را انتخاب کنید.</p>
          <p v-else>برای ورود سریع‌تر و تجربه پایدارتر، نسخه اپلیکیشن را نصب کنید.</p>
        </div>
        <div class="pwa-actions">
          <AppButton class="pwa-action-dismiss" variant="ghost" size="sm" @click="dismiss">بعداً</AppButton>
          <AppButton class="pwa-action-install" size="sm" @click="handleInstall">
            {{ isIOS ? 'راهنما' : 'نصب' }}
          </AppButton>
        </div>
      </AppCard>
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
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 400px;
  pointer-events: auto;
}

.pwa-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--ds-bg-inset);
}

.pwa-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pwa-info {
  flex: 1;
  min-width: 0;
}

.pwa-eyebrow {
  display: block;
  margin-bottom: 0.15rem;
  color: var(--ds-text-placeholder);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.pwa-info h3 {
  font-size: var(--ds-font-sm);
  font-weight: 900;
  margin: 0 0 2px 0;
  color: var(--ds-text-primary);
}

.pwa-info p {
  font-size: var(--ds-font-xs);
  color: var(--ds-text-muted);
  margin: 0;
  line-height: 1.4;
}

.pwa-info .ios-guide {
  color: var(--ds-text-secondary);
  font-weight: 700;
}

.pwa-actions {
  display: flex;
  gap: 8px;
}

.slide-up-enter-active, .slide-up-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.slide-up-enter-from, .slide-up-leave-to {
  transform: translateY(20px);
  opacity: 0;
}
</style>
