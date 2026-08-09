<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { X } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { getNotificationIconComponent } from '../utils/notificationUi'
import { getNotificationDisplayKind, type ToastNotification } from '../types/notifications'
import { isSecurityLayerActive } from '../utils/securityLayerState'
import { validateIntendedRoute } from '../utils/authNavigation'
import { assertSuccessfulNavigation } from '../utils/navigationResult'
import AppToast from './ui/AppToast.vue'

const props = withDefaults(defineProps<{ v2Scope?: boolean }>(), { v2Scope: false })

const store = useNotificationStore()
const router = useRouter()
const toastLayerBlocked = computed(() => props.v2Scope && isSecurityLayerActive.value)
const transitionName = computed(() => (props.v2Scope ? 'ui-v2-toast' : 'toast'))

watch(
  [toastLayerBlocked, () => store.activeToasts.map((toast) => toast.id).join(',')],
  ([blocked]) => {
    for (const toast of store.activeToasts) {
      if (blocked) store.pauseToast(toast.id, 'security-layer')
      else store.resumeToast(toast.id, 'security-layer')
    }
  },
  { immediate: true },
)

const pauseToastInteraction = (id: number, reason: 'focus' | 'hover') => {
  store.pauseToast(id, reason)
}

const resumeToastInteraction = (
  event: FocusEvent | MouseEvent,
  id: number,
  reason: 'focus' | 'hover',
) => {
  const currentTarget = event.currentTarget as HTMLElement | null
  const relatedTarget = event.relatedTarget
  if (currentTarget && relatedTarget instanceof Node && currentTarget.contains(relatedTarget))
    return
  store.resumeToast(id, reason)
}

onBeforeUnmount(() => {
  for (const toast of store.activeToasts) {
    store.resumeToast(toast.id, 'focus')
    store.resumeToast(toast.id, 'hover')
    store.resumeToast(toast.id, 'security-layer')
  }
})

// Swipe to dismiss logic
const dragState = ref<Record<number, { startX: number; currentX: number }>>({})

const onTouchStart = (e: TouchEvent, id: number) => {
  if (!e.touches[0]) return
  dragState.value[id] = {
    startX: e.touches[0].clientX,
    currentX: e.touches[0].clientX,
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
  if (!dragState.value[id])
    return {
      transition: props.v2Scope
        ? 'opacity var(--ui-v2-motion-state)'
        : 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
      animation: props.v2Scope ? 'none' : undefined,
    }
  const diff = dragState.value[id].currentX - dragState.value[id].startX
  const opacity = Math.max(0, 1 - Math.abs(diff) / 200)
  const scale = Math.max(0.9, 1 - Math.abs(diff) / 1000)

  return {
    transform: props.v2Scope ? `translateX(${diff}px)` : `translateX(${diff}px) scale(${scale})`,
    opacity: opacity,
    transition: 'none',
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

const resolveToastRoute = (toast: ToastNotification): string | null => {
  const safePath = validateIntendedRoute({ fullPath: toast.route })
  if (!safePath) return null
  try {
    const resolved = router.resolve(safePath)
    if (!resolved.matched.length || resolved.name === 'system-recovery') return null
    return safePath
  } catch {
    return null
  }
}

const hasToastRouteAction = (toast: ToastNotification) => resolveToastRoute(toast) !== null

const getToastRouteActionLabel = (toast: ToastNotification) =>
  toast.title.trim() ? `باز کردن اعلان «${toast.title.trim()}»` : 'باز کردن اعلان'

const handleToastClick = async (toast: ToastNotification) => {
  // If user was swiping, don't trigger click navigation
  const state = dragState.value[toast.id]
  if (state) {
    const diff = Math.abs(state.currentX - state.startX)
    if (diff > 5) return
  }

  if (toast.route) {
    const routePath = resolveToastRoute(toast)
    if (!routePath) return
    const previousPath = router.currentRoute.value.fullPath
    try {
      assertSuccessfulNavigation(await router.push(routePath))
      if (router.currentRoute.value.name === 'system-recovery') {
        if (previousPath) assertSuccessfulNavigation(await router.replace(previousPath))
        return
      }
    } catch {
      return
    }
  }
  store.removeToast(toast.id)
}
</script>

<template>
  <div
    class="fixed top-6 left-0 right-0 z-[9999] flex flex-col items-center gap-3 pointer-events-none px-6"
    :class="{
      'ui-v2-toast-layer': v2Scope,
      'ui-v2-toast-layer--blocked': toastLayerBlocked,
    }"
    :aria-hidden="toastLayerBlocked ? 'true' : undefined"
    :inert="toastLayerBlocked ? true : undefined"
  >
    <transition-group :name="transitionName">
      <div
        v-for="toast in store.activeToasts"
        :key="toast.id"
        class="toast-card-floating pointer-events-auto"
        :class="[`toast-card-floating--${getToastTone(toast)}`, { 'ui-v2-toast-item': v2Scope }]"
        :style="getToastStyle(toast.id)"
        @touchstart="onTouchStart($event, toast.id)"
        @touchmove="onTouchMove($event, toast.id)"
        @touchend="onTouchEnd(toast.id)"
        @mouseenter="pauseToastInteraction(toast.id, 'hover')"
        @mouseleave="resumeToastInteraction($event, toast.id, 'hover')"
        @focusin="pauseToastInteraction(toast.id, 'focus')"
        @focusout="resumeToastInteraction($event, toast.id, 'focus')"
      >
        <component
          :is="hasToastRouteAction(toast) ? 'button' : 'div'"
          class="toast-card-floating__action"
          :class="{ 'toast-card-floating__action--interactive': hasToastRouteAction(toast) }"
          :type="hasToastRouteAction(toast) ? 'button' : undefined"
          :aria-label="hasToastRouteAction(toast) ? getToastRouteActionLabel(toast) : undefined"
          @click="hasToastRouteAction(toast) && handleToastClick(toast)"
        >
          <div class="notif-icon-circle" :class="{ 'ui-v2-toast-icon': v2Scope }">
            <component :is="getNotificationIconComponent(toast)" :size="20" />
          </div>
          <AppToast
            class="toast-card-floating__surface"
            :title="toast.title"
            :message="toast.body"
            :tone="getToastTone(toast)"
          />
        </component>
        <button
          class="close-btn-minimal"
          :class="{ 'ui-v2-toast-dismiss': v2Scope }"
          :type="v2Scope ? 'button' : undefined"
          :aria-label="v2Scope ? 'بستن اعلان' : undefined"
          @click.stop="store.removeToast(toast.id)"
        >
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
  user-select: none;
  touch-action: pan-y;
  will-change: transform, opacity;
}

.toast-card-floating__action {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: stretch;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: inherit;
}

.toast-card-floating__action--interactive {
  cursor: pointer;
}

.toast-card-floating__action--interactive:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 3px;
  border-radius: var(--ds-radius-md);
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
