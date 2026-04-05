<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { onMounted } from 'vue'
import BottomNav from './components/BottomNav.vue'
import SessionApprovalModal from './components/SessionApprovalModal.vue'
import { setupExpiryTimer, apiFetch, logout } from './utils/auth'
import { useWebSocket } from './composables/useWebSocket'

const route = useRoute()
const router = useRouter()
const { on, connect } = useWebSocket()

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
  }
  
  on('session:revoked', ensureSessionValidation)

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    (window as any).deferredPrompt = e;
    window.dispatchEvent(new Event('pwa-install-ready'));
  });
})
</script>


<template>
  <div class="min-h-screen pb-24 font-sans text-gray-900 antialiased selection:bg-primary-500 selection:text-white" style="background: linear-gradient(160deg, #fefce8 0%, #ffffff 40%, #fffbeb 100%)">
    
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