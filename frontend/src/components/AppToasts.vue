<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'

const store = useNotificationStore()
const router = useRouter()

// Swipe to dismiss logic
const dragState = ref<Record<number, { startX: number, currentX: number }>>({})

const onTouchStart = (e: TouchEvent, id: number) => {
  if (!e.touches[0]) return
  dragState.value[id] = {
    startX: e.touches[0].clientX,
    currentX: e.touches[0].clientX
  }
}

const onTouchMove = (e: TouchEvent, id: number) => {
  if (!dragState.value[id] || !e.touches[0]) return
  dragState.value[id].currentX = e.touches[0].clientX
}

const onTouchEnd = (id: number) => {
  if (!dragState.value[id]) return
  
  const diff = dragState.value[id].currentX - dragState.value[id].startX
  if (Math.abs(diff) > 50) {
    // Swiped enough to dismiss
    store.removeToast(id)
  }
  
  // Clean up
  delete dragState.value[id]
}

const getToastStyle = (id: number) => {
  if (!dragState.value[id]) return {}
  const diff = dragState.value[id].currentX - dragState.value[id].startX
  return {
    transform: `translateX(${diff}px)`,
    transition: 'none' // Remove transition while dragging
  }
}

const handleToastClick = (toast: any) => {
  if (toast.route) {
    router.push(toast.route)
  }
  store.removeToast(toast.id)
}
</script>

<template>
  <div class="fixed top-4 left-0 right-0 z-[9999] flex flex-col items-center gap-2 pointer-events-none px-4">
    <transition-group name="toast">
      <div 
        v-for="toast in store.activeToasts" 
        :key="toast.id"
        class="bg-white rounded-2xl shadow-lg border border-gray-100 p-3 max-w-sm w-full pointer-events-auto flex items-start gap-3 cursor-pointer"
        :style="getToastStyle(toast.id)"
        @touchstart="onTouchStart($event, toast.id)"
        @touchmove="onTouchMove($event, toast.id)"
        @touchend="onTouchEnd(toast.id)"
        @click="handleToastClick(toast)"
      >
        <div class="bg-primary-100 text-primary-600 rounded-full p-2 shrink-0">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"></path>
            <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"></path>
          </svg>
        </div>
        <div class="flex-1 min-w-0">
          <h4 class="font-bold text-gray-900 text-sm truncate">{{ toast.title }}</h4>
          <p class="text-xs text-gray-500 mt-0.5 line-clamp-2">{{ toast.body }}</p>
        </div>
        <button @click.stop="store.removeToast(toast.id)" class="text-gray-400 p-1 -mr-2">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.toast-enter-from {
  opacity: 0;
  transform: translateY(-20px) scale(0.95);
}
.toast-leave-to {
  opacity: 0;
  transform: translateY(-10px) scale(0.95);
}
</style>
