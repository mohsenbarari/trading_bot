<script setup lang="ts">
import { useRoute } from 'vue-router'
import { onMounted } from 'vue'
import BottomNav from './components/BottomNav.vue'
import SessionApprovalModal from './components/SessionApprovalModal.vue'
import PWAInstallOverlay from './components/PWAInstallOverlay.vue'
import AppToasts from './components/AppToasts.vue'
import { setupExpiryTimer, apiFetch, isAppConnecting } from './utils/auth'
import { useWebSocket } from './composables/useWebSocket'
import { useNotificationRuntime } from './composables/useNotificationRuntime'
import { initChatUploadBackground } from './services/chatUploadBackground'
import { initChatDocumentDownloadBackground } from './services/chatDocumentDownloadBackground'
import { initChatFileDebugOverlay } from './composables/chat/useChatFileHandler'


const route = useRoute()
const { on, off, connect } = useWebSocket()

const ensureSessionValidation = async () => {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return
  try {
    await apiFetch('/api/sessions/verify', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken })
    })
  } catch (e) {
    // If 401, apiFetch will automatically log the user out
  }
}

onMounted(() => {
  // راه‌اندازی تایمر انقضای توکن — ریدایرکت خودکار به لاگین
  setupExpiryTimer()

  // Show the on-screen [chat-file] diagnostic overlay when ?chatFileDebug=1
  // is set (or persisted in localStorage). No-op otherwise.
  initChatFileDebugOverlay()

  // Initialize the background chat upload service. This restores any
  // pending uploads saved to IndexedDB from a previous session and resumes
  // them in the background — so media sends continue across page reloads
  // and in-app navigation away from the messenger.
  void initChatUploadBackground({
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
    getAuthToken: () => localStorage.getItem('auth_token'),
  })

  void initChatDocumentDownloadBackground({
    apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '',
    getAuthToken: () => localStorage.getItem('auth_token'),
  })

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    (window as any).deferredPrompt = e;
    window.dispatchEvent(new Event('pwa-install-ready'));
  });
})

useNotificationRuntime({ connect, on, off, ensureSessionValidation })
</script>


<template>
  <div class="h-full flex flex-col font-sans text-gray-900 antialiased selection:bg-primary-500 selection:text-white overflow-hidden" style="background: linear-gradient(160deg, #fefce8 0%, #ffffff 40%, #fffbeb 100%)">
    
    <!-- Global Connecting State -->
    <div v-if="isAppConnecting" class="fixed top-0 left-0 w-full bg-amber-500 text-white text-sm py-1.5 flex items-center justify-center z-[200] gap-2 font-medium shadow-md">
      <svg class="h-4 w-4 animate-spin text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      در حال اتصال...
    </div>

    <!-- Page Content Container -->
    <div class="flex-1 relative overflow-y-auto overflow-x-hidden min-h-0 bg-transparent">
      <RouterView v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </RouterView>
    </div>

    <!-- Bottom Navigation (Hidden on Login) -->
    <BottomNav v-if="route.name !== 'login'" />

    <!-- Session Approval Modal (always mounted for logged-in users) -->
    <SessionApprovalModal v-if="route.name !== 'login'" />

    <!-- Global In-App Toast Notifications -->
    <AppToasts v-if="route.name !== 'login'" />

    <!-- PWA Install Overlay -->
    <PWAInstallOverlay />
    
  </div>
</template>

<style>
/* Global Transitions */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>