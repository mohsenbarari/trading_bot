<script setup lang="ts">
import { onMounted } from 'vue'
import BottomNav from './BottomNav.vue'
import SessionApprovalModal from './SessionApprovalModal.vue'
import PWAInstallOverlay from './PWAInstallOverlay.vue'
import AppToasts from './AppToasts.vue'
import { setupExpiryTimer, apiFetch } from '../utils/auth'
import { useWebSocket } from '../composables/useWebSocket'
import { useNotificationRuntime } from '../composables/useNotificationRuntime'
import { initChatUploadBackground } from '../services/chatUploadBackground'
import { initChatDocumentDownloadBackground } from '../services/chatDocumentDownloadBackground'
import { initChatFileDebugOverlay } from '../composables/chat/useChatFileHandler'

const { on, off, connect } = useWebSocket()

const ensureSessionValidation = async () => {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return

  try {
    await apiFetch('/api/sessions/verify', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken })
    })
  } catch {
    // If 401, apiFetch will handle logout/suspension centrally.
  }
}

onMounted(() => {
  setupExpiryTimer()

  initChatFileDebugOverlay()

  void initChatUploadBackground({
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
    getAuthToken: () => localStorage.getItem('auth_token'),
  })

  void initChatDocumentDownloadBackground({
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
    getAuthToken: () => localStorage.getItem('auth_token'),
  })

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    ;(window as any).deferredPrompt = event
    window.dispatchEvent(new Event('pwa-install-ready'))
  })
})

useNotificationRuntime({ connect, on, off, ensureSessionValidation })
</script>

<template>
  <BottomNav />
  <SessionApprovalModal />
  <AppToasts />
  <PWAInstallOverlay />
</template>