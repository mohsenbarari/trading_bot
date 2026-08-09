<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import BottomNav from './BottomNav.vue'
import SessionApprovalModal from './SessionApprovalModal.vue'
import AppToasts from './AppToasts.vue'
import { setupExpiryTimer, apiFetch } from '../utils/auth'
import { useWebSocket } from '../composables/useWebSocket'
import { useNotificationRuntime } from '../composables/useNotificationRuntime'
import {
  hasPendingDocumentDownloadResumeHint,
  hasPendingUploadResumeHint,
} from '../services/chatTransferResumeHints'
import { initChatFileDebugOverlay } from '../composables/chat/useChatFileHandler'

withDefaults(
  defineProps<{
    v2Scope?: boolean
    showDailyNavigation?: boolean
  }>(),
  {
    v2Scope: false,
    showDailyNavigation: true,
  },
)

const route = useRoute()
const { on, off, connect, sendPresenceUpdate } = useWebSocket()

const publishRoutePresence = () => {
  sendPresenceUpdate(route.path, !document.hidden)
}

const handleVisibilityChange = () => {
  publishRoutePresence()
}

const handleWsReconnect = () => {
  publishRoutePresence()
}

const ensureSessionValidation = async () => {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return

  try {
    await apiFetch('/api/sessions/verify', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  } catch {
    // If 401, apiFetch will handle logout/suspension centrally.
  }
}

onMounted(() => {
  setupExpiryTimer()

  if (hasPendingUploadResumeHint()) {
    void import('../services/chatUploadBackground').then(({ initChatUploadBackground }) =>
      initChatUploadBackground({
        apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
        getAuthToken: () => localStorage.getItem('auth_token'),
      }),
    )
  }

  if (hasPendingDocumentDownloadResumeHint()) {
    void import('../services/chatDocumentDownloadBackground').then(
      ({ initChatDocumentDownloadBackground }) =>
        initChatDocumentDownloadBackground({
          apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
          getAuthToken: () => localStorage.getItem('auth_token'),
        }),
    )
  }

  initChatFileDebugOverlay()
  publishRoutePresence()
  document.addEventListener('visibilitychange', handleVisibilityChange)
  on('ws:reconnect', handleWsReconnect)
})

watch(
  () => route.path,
  () => {
    publishRoutePresence()
  },
)

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  off('ws:reconnect', handleWsReconnect)
  sendPresenceUpdate(route.path, false)
})

useNotificationRuntime({ connect, on, off, ensureSessionValidation })
</script>

<template>
  <BottomNav v-if="showDailyNavigation" :v2-scope="v2Scope" />
  <SessionApprovalModal :v2-portal="v2Scope" />
  <AppToasts :v2-scope="v2Scope" />
</template>
