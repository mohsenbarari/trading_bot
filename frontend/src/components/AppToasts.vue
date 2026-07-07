<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { X } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { getNotificationIconComponent } from '../utils/notificationUi'
import {
  getNotificationDisplayKind,
  type ToastNotification,
} from '../types/notifications'
import AppToast from './ui/AppToast.vue'

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

type ToastTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const getToastTone = (toast: ToastNotification): ToastTone => {
  if (toast.level === 'success') return 'success'
  if (toast.level === 'warning') return 'warning'
  if (toast.level === 'error') return 'danger'
  if (toast.level === 'info' && toast.kind !== 'chat') return 'info'

  const displayKind = getNotificationDisplayKind(toast)
  if (displayKind === 'success') return 'success'
  if (displayKind === 'warning') return 'warning'
  if (displayKind === 'error') return 'danger'
  if (displayKind === 'info' || displayKind === 'chat') return 'info'
  return 'neutral'
}

const handleToastClick = (toast: ToastNotification) => {
  // If user was swiping, don't trigger click navigation
  const state = dragState.value[toast.id]
  if (state) {
    const diff = Math.abs(state.currentX - state.startX)
    if (diff > 5) return
  }
  
  if (toast.route) {
    void router.push(toast.route)
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
        :class="`toast-card-floating--${getToastTone(toast)}`"
        :style="getToastStyle(toast.id)"
        @touchstart="onTouchStart($event, toast.id)"
        @touchmove="onTouchMove($event, toast.id)"
        @touchend="onTouchEnd(toast.id)"
        @click="handleToastClick(toast)"
      >
        <div class="notif-icon-circle">
          <component :is="getNotificationIconComponent(toast)" :size="20" />
        </div>
        <AppToast
          class="toast-card-floating__surface"
          :title="toast.title"
          :message="toast.body"
          :tone="getToastTone(toast)"
        />
        <button @click.stop="store.removeToast(toast.id)" class="close-btn-minimal">
          <X :size="16" :stroke-width="2.5" />
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-card-floating {
  position: relative;
  max-width: 400px;
  width: 100%;
  display: flex;
  align-items: stretch;
  gap: 0.75rem;
  cursor: pointer;
  user-select: none;
  touch-action: pan-y;
  will-change: transform, opacity;
}

.toast-card-floating__surface {
  min-width: 0;
  flex: 1;
}

.toast-card-floating :deep(.ui-toast) {
  width: 100%;
  max-width: none;
  min-height: 64px;
  padding-inline: 3.6rem 2.75rem;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

.toast-card-floating :deep(.ui-toast span) {
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
}

.notif-icon-circle {
  width: 40px;
  height: 40px;
  position: absolute;
  margin: 0.78rem 0.85rem 0 0;
  background: var(--ds-bg-card);
  color: var(--ds-primary-500);
  border-radius: var(--ds-radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: var(--ds-shadow-sm);
  z-index: 1;
}

.toast-card-floating--success .notif-icon-circle {
  color: var(--ds-success-700);
}

.toast-card-floating--warning .notif-icon-circle {
  color: var(--ds-warning-700);
}

.toast-card-floating--danger .notif-icon-circle {
  color: var(--ds-danger-700);
}

.toast-card-floating--info .notif-icon-circle {
  color: var(--ds-info-700);
}

.close-btn-minimal {
  background: none;
  border: none;
  color: var(--ds-text-placeholder);
  padding: 0.25rem;
  position: absolute;
  left: 0.85rem;
  top: 0.85rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  transition: opacity 0.2s;
  z-index: 1;
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
