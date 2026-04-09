<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { onMounted, watch } from 'vue'
import BottomNav from './components/BottomNav.vue'
import SessionApprovalModal from './components/SessionApprovalModal.vue'
import { setupExpiryTimer, apiFetch, logout, isAppConnecting } from './utils/auth'
import { useWebSocket } from './composables/useWebSocket'
import { useNotificationStore } from './stores/notifications'
import { requestNotificationPermission, showBrowserNotification } from './utils/browserNotifications'

const route = useRoute()
const router = useRouter()
const { on, connect } = useWebSocket()
const notificationStore = useNotificationStore()

onMounted(() => {
  // راه‌اندازی تایمر انقضای توکن — ریدایرکت خودکار به لاگین
  setupExpiryTimer()

  const ensureSessionValidation = async () => {
    const refreshToken = localStorage.getItem('refresh_token')
    if (!refreshToken) return
    try {
      await apiFetch('/api/sessions/verify', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken })
      })
    } catch(e) {
      // If 401, apiFetch will automatically log the user out
    }
  }

  // وقتی کاربر احراز هویت شد، وب‌سوکت وصل می‌شود و بررسی نشست انجام می‌گیرد
  if (localStorage.getItem('auth_token')) {
    connect()
    ensureSessionValidation()
    notificationStore.fetchInitialCounts()
    requestNotificationPermission()
  }
  
  on('session:revoked', ensureSessionValidation)

  // --- Global Notification Listeners ---
  
  // 1. General App Notifications
  on('message', (payload: any) => {
    notificationStore.addAppNotification(payload)
    
    // Show browser notification if tab is hidden or user requested it
    if (document.hidden) {
        showBrowserNotification(payload.title || 'اعلان جدید', payload.content || '')
    }
  })

  // 2. Chat Messages
  on('chat:message', (payload: any) => {
    // Increment unread count globally
    notificationStore.incrementChatUnread()
    
    // Show browser notification ONLY if we are NOT on the chat page or tab is hidden
    const isChatOpen = route.path.startsWith('/chat')
    // We could be even more specific and check if we are chat with THAT user, 
    // but a global check is safer for now.
    
    if (document.hidden || !isChatOpen) {
        const sender = payload.sender_name || 'پیام جدید'
        showBrowserNotification(sender, payload.content || 'تصویر یا فایل')
    }
  })

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    (window as any).deferredPrompt = e;
    window.dispatchEvent(new Event('pwa-install-ready'));
  });
})
</script>


<template>
  <div class="min-h-screen pb-24 font-sans text-gray-900 antialiased selection:bg-primary-500 selection:text-white" style="background: linear-gradient(160deg, #fefce8 0%, #ffffff 40%, #fffbeb 100%)">
    
    <!-- Global Connecting State -->
    <div v-if="isAppConnecting" class="fixed top-0 left-0 w-full bg-amber-500 text-white text-sm py-1.5 flex items-center justify-center z-[200] gap-2 font-medium shadow-md">
      <svg class="h-4 w-4 animate-spin text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      در حال اتصال...
    </div>

    <!-- Page Content -->
    <RouterView v-slot="{ Component }">
      <transition name="fade" mode="out-in">
        <component :is="Component" />
      </transition>
    </RouterView>

    <!-- Bottom Navigation (Hidden on Login) -->
    <BottomNav v-if="route.name !== 'login'" />

    <!-- Session Approval Modal (always mounted for logged-in users) -->
    <SessionApprovalModal v-if="route.name !== 'login'" />
    
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