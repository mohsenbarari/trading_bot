<script setup lang="ts">
import { useRoute } from 'vue-router'
import { onMounted } from 'vue'
import BottomNav from './components/BottomNav.vue'

const route = useRoute()

onMounted(() => {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    (window as any).deferredPrompt = e;
    window.dispatchEvent(new Event('pwa-install-ready'));
  });
})
</script>


<template>
  <div class="min-h-screen bg-gray-50 pb-24 font-sans text-gray-900 antialiased selection:bg-primary-500 selection:text-white">
    
    <!-- Page Content -->
    <RouterView v-slot="{ Component }">
      <transition name="fade" mode="out-in">
        <component :is="Component" />
      </transition>
    </RouterView>

    <!-- Bottom Navigation (Hidden on Login) -->
    <BottomNav v-if="route.name !== 'login'" />
    
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