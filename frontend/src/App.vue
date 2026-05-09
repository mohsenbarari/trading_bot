<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { isAppConnecting } from './utils/auth'


const route = useRoute()
const router = useRouter()
const AuthenticatedShell = defineAsyncComponent(() => import('./components/AppAuthenticatedShell.vue'))
const PWAInstallOverlay = defineAsyncComponent(() => import('./components/PWAInstallOverlay.vue'))

// Track whether the router's FIRST navigation (which includes loading the
// lazy-loaded route component chunk from the network) has completed.
// Until then we show a full-screen spinner instead of a blank white page.
const isFirstRouteReady = ref(false)
router.isReady().then(() => { isFirstRouteReady.value = true })
const shouldRenderAuthenticatedShell = computed(() => isFirstRouteReady.value && route.name !== 'login')

watch(isFirstRouteReady, (ready) => {
  if (!ready) return

  document.documentElement.setAttribute('data-app-mounted', '1')
  document.documentElement.removeAttribute('data-app-boot-timeout')

  const bootTimeoutId = (window as any).__appBootTimeoutId
  if (typeof bootTimeoutId === 'number') {
    window.clearTimeout(bootTimeoutId)
    delete (window as any).__appBootTimeoutId
  }
}, { immediate: true })
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
      <!-- Full-screen spinner shown while the first route's JS chunk loads from
           the network (only visible on first incognito/cold load). Without this,
           the RouterView renders nothing during the async component download → blank white page. -->
      <div v-if="!isFirstRouteReady" class="flex items-center justify-center h-full min-h-screen">
        <div class="w-10 h-10 border-4 border-amber-400 border-t-transparent rounded-full animate-spin"></div>
      </div>
      <RouterView v-else v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </RouterView>
    </div>

    <AuthenticatedShell v-if="shouldRenderAuthenticatedShell" />
    <PWAInstallOverlay v-if="isFirstRouteReady" />
    
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