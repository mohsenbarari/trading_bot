<script setup lang="ts">
import { ref, onBeforeUnmount, onMounted, computed, watch } from 'vue'
import { usePWAInstall } from '../utils/pwaInstall'
import { isSecurityLayerActive } from '../utils/securityLayerState'
import AppButton from './ui/AppButton.vue'
import AppCard from './ui/AppCard.vue'

const { isInstallable, isInstalled, installApp } = usePWAInstall()
const props = withDefaults(defineProps<{ eligible?: boolean }>(), { eligible: false })
const showOverlay = ref(false)
const showIosGuide = ref(false)
const isPromptDelayElapsed = ref(false)
let promptDelayTimer: number | undefined
const isOnline = ref(typeof navigator === 'undefined' ? true : navigator.onLine)

const PROMPT_DISMISSED_KEY = 'pwa_install_prompt_dismissed_at_v2'
const PROMPT_DISMISS_TTL_MS = 24 * 60 * 60 * 1000
type IOSWindow = Window & { MSStream?: unknown }
type IOSNavigator = Navigator & { standalone?: boolean }

// تشخیص سیستم‌عامل برای نمایش راهنمای اختصاصی
const isIOS = computed(() => {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as IOSWindow).MSStream
})

const wasRecentlyDismissed = () => {
  let lastDismissed: string | null
  try {
    lastDismissed = localStorage.getItem(PROMPT_DISMISSED_KEY)
  } catch {
    return false
  }
  if (!lastDismissed) return false

  const timestamp = Number.parseInt(lastDismissed, 10)
  if (Number.isNaN(timestamp)) return false

  return Date.now() - timestamp <= PROMPT_DISMISS_TTL_MS
}

const maybeShowOverlay = () => {
  if (!isPromptDelayElapsed.value) return
  if (!props.eligible || !isOnline.value || isSecurityLayerActive.value || isInstalled.value) {
    showOverlay.value = false
    return
  }

  // در اندروید/دسکتاپ از isInstallable استفاده می‌کنیم (Chrome/Edge).
  // در iOS چون رویداد beforeinstallprompt نداریم، فقط راهنمای نصب نشان می‌دهیم.
  const shouldShowForAndroid = isInstallable.value
  const shouldShowForIOS = isIOS.value && !(window.navigator as IOSNavigator).standalone

  showOverlay.value = (shouldShowForAndroid || shouldShowForIOS) && !wasRecentlyDismissed()
}

const handleInstallReady = () => {
  maybeShowOverlay()
}

onMounted(() => {
  window.addEventListener('pwa-install-ready', handleInstallReady)
  window.addEventListener('online', handleConnectivityChange)
  window.addEventListener('offline', handleConnectivityChange)

  promptDelayTimer = window.setTimeout(() => {
    isPromptDelayElapsed.value = true
    maybeShowOverlay()
  }, 4000)
})

onBeforeUnmount(() => {
  window.removeEventListener('pwa-install-ready', handleInstallReady)
  window.removeEventListener('online', handleConnectivityChange)
  window.removeEventListener('offline', handleConnectivityChange)
  if (promptDelayTimer !== undefined) window.clearTimeout(promptDelayTimer)
})

function handleConnectivityChange() {
  isOnline.value = navigator.onLine
  maybeShowOverlay()
}

watch(
  [
    () => props.eligible,
    () => isInstallable.value,
    () => isInstalled.value,
    () => isSecurityLayerActive.value,
  ],
  () => {
    maybeShowOverlay()
  },
)

const dismiss = () => {
  showOverlay.value = false
  showIosGuide.value = false
  try {
    localStorage.setItem(PROMPT_DISMISSED_KEY, Date.now().toString())
  } catch {
    // Browser storage is best-effort; the visible prompt has already closed.
  }
}

const handleInstall = async () => {
  if (isIOS.value) {
    showIosGuide.value = true
    return
  }
  try {
    const success = await installApp()
    if (success) {
      showOverlay.value = false
      return
    }
  } catch {
    // A rejected browser prompt is not actionable from the consumed event.
  }
  // A browser-level dismissal invalidates the consumed prompt event. Close the
  // card and respect the same bounded quiet period as an explicit “later”.
  dismiss()
}
</script>

<template>
  <transition name="slide-up">
    <aside v-if="showOverlay" class="ui-v2-pwa-install" aria-label="نصب اپلیکیشن">
      <AppCard class="ui-v2-pwa-card">
        <div class="ui-v2-pwa-icon">
          <img src="/pwa-192x192.png" alt="App Icon" />
        </div>
        <div class="ui-v2-pwa-copy">
          <h3>نصب روی صفحه اصلی</h3>
          <p v-if="isIOS && !showIosGuide">برای نصب در آیفون، راهنمای کوتاه نصب را باز کنید.</p>
          <p v-else-if="isIOS" class="ui-v2-pwa-ios-guide">
            در Safari دکمه Share را بزنید و سپس Add to Home Screen را انتخاب کنید.
          </p>
          <p v-else>برای ورود سریع‌تر و تجربه پایدارتر، نسخه اپلیکیشن را نصب کنید.</p>
        </div>
        <div class="ui-v2-pwa-actions">
          <AppButton class="pwa-action-dismiss" variant="ghost" size="sm" @click="dismiss"
            >بعداً</AppButton
          >
          <AppButton class="pwa-action-install" size="sm" @click="handleInstall">
            {{ isIOS ? 'راهنما' : 'نصب' }}
          </AppButton>
        </div>
      </AppCard>
    </aside>
  </transition>
</template>
