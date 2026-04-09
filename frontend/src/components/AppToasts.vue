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
  if (!dragState.value[id]) return {
    transition: 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1)'
  }
  const diff = dragState.value[id].currentX - dragState.value[id].startX
  const opacity = Math.max(0, 1 - Math.abs(diff) / 200)
  const scale = Math.max(0.9, 1 - Math.abs(diff) / 1000)
  
  return {
    transform: `translateX(${diff}px) scale(${scale})`,
    opacity: opacity,
    transition: 'none'
  }
}

const handleToastClick = (toast: any) => {
  // If user was swiping, don't trigger click navigation
  const state = dragState.value[toast.id]
  if (state) {
    const diff = Math.abs(state.currentX - state.startX)
    if (diff > 5) return
  }
  
  if (toast.route) {
    router.push(toast.route)
  }
  store.removeToast(toast.id)
}
</script>

<template>
  <div class="fixed top-6 left-0 right-0 z-[9999] flex flex-col items-center gap-3 pointer-events-none px-6">
    <transition-group name="toast">
      <div 
        v-for="toast in store.activeToasts" 
        :key="toast.id"
        class="toast-card-floating pointer-events-auto"
        :style="getToastStyle(toast.id)"
        @touchstart="onTouchStart($event, toast.id)"
        @touchmove="onTouchMove($event, toast.id)"
        @touchend="onTouchEnd(toast.id)"
        @click="handleToastClick(toast)"
      >
        <div class="notif-icon-circle">
          <svg v-if="toast.route?.includes('chat')" xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"></path>
            <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"></path>
          </svg>
        </div>
        <div class="notif-content">
          <h4 class="notif-title">{{ toast.title }}</h4>
          <p class="notif-body-text">{{ toast.body }}</p>
        </div>
        <button @click.stop="store.removeToast(toast.id)" class="close-btn-minimal">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-card-floating {
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  padding: 0.875rem 1rem;
  max-width: 400px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.875rem;
  box-shadow: 
    0 10px 25px -5px rgba(0, 0, 0, 0.1),
    0 8px 10px -6px rgba(0, 0, 0, 0.05);
  cursor: pointer;
  user-select: none;
  touch-action: pan-y;
  will-change: transform, opacity;
}

.notif-icon-circle {
  width: 40px;
  height: 40px;
  background: white;
  color: #f59e0b;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.05);
}

.notif-content {
  flex: 1;
  min-width: 0;
}

.notif-title {
  font-size: 0.875rem;
  font-weight: 800;
  color: #111827;
  margin: 0;
  line-height: 1.2;
}

.notif-body-text {
  font-size: 0.75rem;
  color: #4b5563;
  margin: 0.2rem 0 0 0;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
}

.close-btn-minimal {
  background: none;
  border: none;
  color: #9ca3af;
  padding: 0.25rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  transition: opacity 0.2s;
}
.close-btn-minimal:hover {
  opacity: 1;
}

/* Animations */
.toast-enter-active {
  animation: slide-in 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.toast-leave-active {
  animation: slide-out 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes slide-in {
  from {
    opacity: 0;
    transform: translateY(-40px) scale(0.9);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes slide-out {
  from {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
  to {
    opacity: 0;
    transform: translateY(-20px) scale(0.95);
  }
}
</style>
