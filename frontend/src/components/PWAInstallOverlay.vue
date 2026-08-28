<script setup lang="ts">
import { ref, onBeforeUnmount, onMounted, computed, watch } from 'vue'
import { usePWAInstall } from '../utils/pwaInstall'
import { isSecurityLayerActive } from '../utils/securityLayerState'
import AppBottomSheet from './ui/AppBottomSheet.vue'
import AppButton from './ui/AppButton.vue'

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

const isIOS = computed(() => {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as IOSWindow).MSStream
})

const installCopy = computed(() => {
  if (isIOS.value && showIosGuide.value) {
    return 'در سافاری دکمه اشتراک را بزنید و «افزودن به صفحه اصلی» را انتخاب کنید.'
  }
  if (isIOS.value) return 'برای نصب در آیفون، راهنمای کوتاه نصب را باز کنید.'
  return 'روی صفحه اصلی بگذارید تا مثل یک برنامه باز شود.'
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
  dismiss()
}
</script>

<template>
  <AppBottomSheet
    :open="showOverlay"
    title="نصب روی صفحه اصلی"
    :description="installCopy"
    :show-close="false"
    close-label="بعداً"
    backdrop-class="ui-v2-pwa-install pwa-install-layer"
    panel-class="pwa-install-sheet"
    body-class="pwa-install-body"
    actions-class="ui-v2-pwa-actions pwa-install-actions"
    @close="dismiss"
  >
    <div class="ui-v2-pwa-icon pwa-install-icon">
      <img src="/pwa-192x192.png" alt="" />
    </div>
    <p v-if="isIOS && showIosGuide" class="ui-v2-pwa-ios-guide pwa-install-guide">
      {{ installCopy }}
    </p>
    <template #actions>
      <AppButton class="pwa-action-dismiss" variant="ghost" @click="dismiss">بعداً</AppButton>
      <AppButton class="pwa-action-install" @click="handleInstall">
        {{ isIOS ? 'راهنما' : 'نصب' }}
      </AppButton>
    </template>
  </AppBottomSheet>
</template>

<style scoped>
.pwa-install-icon {
  width: 3rem;
  height: 3rem;
  overflow: hidden;
  margin: 0 auto;
  border-radius: 12px;
  background: var(--ds-bg-inset, #f8fafc);
}

.pwa-install-icon img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pwa-install-guide {
  margin: 0.75rem 0 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  line-height: 1.7;
}

.pwa-install-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
</style>
