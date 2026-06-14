<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { BriefcaseBusiness, Home, MessageCircle, Menu, Store, UserRound, X } from 'lucide-vue-next'
import { currentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'
import { useNotificationStore } from '../stores/notifications'
import { isMarketRuntimeClosed, startMarketRuntimeUpdates, stopMarketRuntimeUpdates } from '../composables/useMarketRuntime'

const route = useRoute()
const isExpanded = ref(false)
const notificationStore = useNotificationStore()

type DragPoint = { x: number; y: number }

// Draggable FAB state
const fabPosition = ref<DragPoint | null>(null)
const isDragging = ref(false)
const dragStart = ref<DragPoint>({ x: 0, y: 0 })
const startPos = ref<DragPoint>({ x: 0, y: 0 })

function getDragClientPoint(e: TouchEvent | MouseEvent) {
  if (e instanceof MouseEvent) {
    return e
  }

  return e.touches[0] ?? e.changedTouches[0] ?? null
}

function loadFabPosition() {
  const saved = localStorage.getItem('fab_position')
  if (saved) {
    try {
      const parsed = JSON.parse(saved)
      if (typeof parsed.x === 'number' && typeof parsed.y === 'number') {
        fabPosition.value = parsed
      }
    } catch(e){}
  }
}

const fabStyle = computed(() => {
  if (fabPosition.value) {
    return {
      left: `${fabPosition.value.x}px`,
      top: `${fabPosition.value.y}px`,
      bottom: 'auto',
      right: 'auto'
    }
  }
  return {}
})

function onDragStart(e: TouchEvent | MouseEvent) {
  const evt = getDragClientPoint(e)
  if (!evt) return

  dragStart.value = { x: evt.clientX, y: evt.clientY }
  
  if (!fabPosition.value) {
    const el = document.querySelector('.fab-container') as HTMLElement
    if (el) {
      const rect = el.getBoundingClientRect()
      startPos.value = { x: rect.left, y: rect.top }
    } else {
      startPos.value = { x: 16, y: window.innerHeight - 60 }
    }
  } else {
    startPos.value = { ...fabPosition.value }
  }
  
  isDragging.value = false
  
  if (e instanceof MouseEvent) {
    document.addEventListener('mousemove', onDragMove)
    document.addEventListener('mouseup', onDragEnd)
  } else {
    document.addEventListener('touchmove', onDragMove, { passive: false })
    document.addEventListener('touchend', onDragEnd)
  }
}

function onDragMove(e: TouchEvent | MouseEvent) {
  const evt = getDragClientPoint(e)
  if (!evt) return

  const dx = evt.clientX - dragStart.value.x
  const dy = evt.clientY - dragStart.value.y
  
  if (!isDragging.value && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
    isDragging.value = true
  }
  
  if (isDragging.value) {
    if (e.cancelable) e.preventDefault()
    let newX = startPos.value.x + dx
    let newY = startPos.value.y + dy
    
    const maxX = window.innerWidth - 44
    const maxY = window.innerHeight - 44
    newX = Math.max(0, Math.min(newX, maxX))
    newY = Math.max(0, Math.min(newY, maxY))
    
    fabPosition.value = { x: newX, y: newY }
  }
}

function onDragEnd(e: Event) {
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  document.removeEventListener('touchmove', onDragMove)
  document.removeEventListener('touchend', onDragEnd)
  
  if (isDragging.value) {
    localStorage.setItem('fab_position', JSON.stringify(fabPosition.value))
    setTimeout(() => { isDragging.value = false }, 50)
  }
}

// Auto-collapse on the market page
const isMarketPage = computed(() => route.name === 'market')
const isMessengerPage = computed(() => route.name === 'messenger')

// Close when navigating
watch(() => route.name, () => {
  isExpanded.value = false
})

onMounted(async () => {
  loadFabPosition()
  const token = localStorage.getItem('auth_token')
  if (!token) return
  startMarketRuntimeUpdates()
  void primeCurrentUserSummary()
})

onUnmounted(() => {
  stopMarketRuntimeUpdates()
})

const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)
const isMarketClosed = computed(() => isMarketRuntimeClosed.value)

const baseItems = [
  { name: 'home', label: 'خانه', icon: Home, path: '/', routeNames: ['home'] },
  { name: 'market', label: 'بازار', icon: Store, path: '/market', routeNames: ['market'] },
  { name: 'messenger', label: 'پیام‌رسان', icon: MessageCircle, path: '/chat', routeNames: ['messenger'], disabled: false },
  {
    name: 'operations',
    label: 'عملیات',
    icon: BriefcaseBusiness,
    path: '/operations',
    routeNames: [
      'operations',
      'operations-customers',
      'operations-customers-detail',
      'operations-accountants',
      'operations-accountants-detail',
      'admin',
      'admin-invitations',
      'admin-channels',
      'admin-users',
      'admin-user-profile',
      'admin-commodities',
      'admin-messages',
      'admin-system',
    ],
  },
  {
    name: 'account',
    label: 'حساب',
    icon: UserRound,
    path: '/account',
    routeNames: [
      'account',
      'account-security',
      'account-storage',
      'account-notifications',
      'profile',
      'settings',
      'notifications',
      'public-profile',
    ],
  },
]

const navItems = computed(() => {
  return baseItems.filter(item => item.name !== 'market' || !isAccountant.value)
})

function isActiveNavItem(item: { routeNames?: string[]; name: string }) {
  const currentRouteName = typeof route.name === 'string' ? route.name : ''
  return item.routeNames?.includes(currentRouteName) || currentRouteName === item.name
}

function toggleNav() {
  if (isDragging.value) return;
  isExpanded.value = !isExpanded.value
}
</script>

<template>
  <!-- ═══ Normal Bottom Nav (non-market, non-messenger pages) ═══ -->
  <nav v-if="!isMarketPage && !isMessengerPage" class="bottom-nav-wrapper">
    <div class="bottom-nav-bar">
      <template v-for="item in navItems" :key="item.name">
        <div v-if="item.disabled" class="nav-item disabled">
          <component :is="item.icon" :size="22" />
          <span class="nav-label">{{ item.label }}</span>
          <span class="soon-dot"></span>
        </div>
        <router-link
          v-else
          :to="item.path"
          class="nav-item"
          :class="{ active: isActiveNavItem(item), 'market-closed': item.name === 'market' && isMarketClosed }"
        >
          <div class="nav-icon-wrap" :class="{ 'icon-active': isActiveNavItem(item) }">
            <component :is="item.icon" :size="22" :stroke-width="isActiveNavItem(item) ? 2.5 : 1.8" />
            
            <!-- Unread Badge for Messenger -->
            <div v-if="item.name === 'messenger' && notificationStore.chatUnreadCount > 0" class="nav-unread-badge" :class="{ 'has-mention': notificationStore.unreadMentionCount > 0 }">
              <span v-if="notificationStore.unreadMentionCount > 0" class="mention-at">@</span>
              {{ notificationStore.chatUnreadCount > 99 ? '99+' : notificationStore.chatUnreadCount }}
            </div>
          </div>
          <span class="nav-label" :class="{ 'nav-label--market': item.name === 'market' && isMarketClosed }">
            <span>{{ item.label }}</span>
            <span v-if="item.name === 'market' && isMarketClosed" class="market-closed-text">بسته</span>
          </span>
        </router-link>
      </template>
    </div>
  </nav>

  <!-- ═══ Collapsed FAB on market & messenger ═══ -->
  <div v-else class="fab-container" :style="fabStyle">
    <!-- Overlay -->
    <transition name="fade">
      <div v-if="isExpanded" class="fab-overlay" @click="isExpanded = false"></div>
    </transition>

    <!-- Expanded nav -->
    <transition name="slide-up">
      <div v-if="isExpanded" class="fab-nav">
        <template v-for="item in navItems" :key="item.name">
          <div v-if="item.disabled" class="fab-item disabled">
            <component :is="item.icon" :size="20" />
            <span>{{ item.label }}</span>
          </div>
          <router-link
            v-else
            :to="item.path"
            class="fab-item"
            :class="{ active: isActiveNavItem(item), 'market-closed': item.name === 'market' && isMarketClosed }"
            @click="isExpanded = false"
          >
            <div class="relative">
              <component :is="item.icon" :size="20" />
              <!-- Unread Badge for FAB menu -->
              <div v-if="item.name === 'messenger' && notificationStore.chatUnreadCount > 0" class="fab-unread-badge" :class="{ 'has-mention': notificationStore.unreadMentionCount > 0 }">
                 <span v-if="notificationStore.unreadMentionCount > 0" class="mention-at">@</span>
                 {{ notificationStore.chatUnreadCount > 9 ? '9+' : notificationStore.chatUnreadCount }}
              </div>
            </div>
            <span class="fab-label">
              <span>{{ item.label }}</span>
              <span v-if="item.name === 'market' && isMarketClosed" class="fab-market-closed-text">بسته</span>
            </span>
          </router-link>
        </template>
      </div>
    </transition>

    <!-- Toggle button -->
    <button 
      class="fab-btn" 
      @click="toggleNav" 
      @mousedown="onDragStart"
      @touchstart="onDragStart"
      :class="{ 'fab-open': isExpanded }"
    >
      <transition name="spin" mode="out-in">
        <X v-if="isExpanded" :size="20" key="close" />
        <Menu v-else :size="20" key="menu" />
      </transition>
    </button>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════
   Normal Bottom Nav
   ═══════════════════════════════ */
.bottom-nav-wrapper {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 50;
  padding: 0 0.75rem 0.75rem;
  pointer-events: none;
}

.bottom-nav-bar {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 0.5rem 0.25rem;
  border-radius: var(--ds-radius-xl);
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 4px 20px rgba(0,0,0,0.08);
  pointer-events: auto;
}

.nav-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.2rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--ds-radius-md);
  text-decoration: none;
  color: var(--ds-text-placeholder);
  transition: all 0.25s;
  -webkit-tap-highlight-color: transparent;
  position: relative;
  cursor: pointer;
}
.nav-item * {
  pointer-events: none;
}
.nav-item.active { color: var(--ds-primary-600); }
.nav-item.disabled { opacity: 0.4; cursor: default; }

.nav-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--ds-radius-md);
  transition: all 0.25s;
}
.icon-active {
  background: var(--ds-primary-50);
  color: var(--ds-primary-600);
}

.nav-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 0.9rem;
  font-size: 0.6rem;
  font-weight: 600;
  line-height: 1.1;
}

.nav-label--market {
  gap: 0.05rem;
}

.market-closed-text {
  color: var(--ds-danger-600);
  font-size: 0.56rem;
  font-weight: 800;
}

.nav-item.market-closed .nav-icon-wrap {
  background: var(--ds-danger-50);
  color: var(--ds-danger-600);
}

.nav-item.market-closed.active {
  color: var(--ds-danger-600);
}

.soon-dot {
  position: absolute;
  top: 4px;
  left: 50%;
  transform: translateX(8px);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3b82f6;
}

/* ═══ Unread Badges ═══ */
.nav-unread-badge {
  position: absolute;
  top: -4px;
  right: -6px;
  background: var(--ds-danger-500);
  color: white;
  font-size: 0.65rem;
  font-weight: 700;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 4px;
  border: 1.5px solid var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
  z-index: 10;
}

.nav-unread-badge.has-mention,
.fab-unread-badge.has-mention {
  background: #7c3aed !important;
  box-shadow: 0 0 8px rgba(124, 58, 237, 0.6);
  animation: pulse-mention 2s infinite;
}

.mention-at {
  font-size: 0.6rem;
  margin-right: 1px;
  font-weight: 800;
}

@keyframes pulse-mention {
  0% {
    transform: scale(1);
    box-shadow: 0 0 0 0 rgba(124, 58, 237, 0.7);
  }
  70% {
    transform: scale(1.05);
    box-shadow: 0 0 0 6px rgba(124, 58, 237, 0);
  }
  100% {
    transform: scale(1);
    box-shadow: 0 0 0 0 rgba(124, 58, 237, 0);
  }
}

.fab-unread-badge {
  position: absolute;
  top: -6px;
  right: -8px;
  background: var(--ds-danger-500);
  color: white;
  font-size: 0.6rem;
  font-weight: 700;
  min-width: 14px;
  height: 14px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 3px;
  border: 1px solid var(--ds-bg-card);
  z-index: 10;
}

.relative {
  position: relative;
}

/* ═══════════════════════════════
   FAB (Market Page)
   ═══════════════════════════════ */
.fab-container {
  position: fixed;
  bottom: 1rem;
  left: 1rem;
  z-index: 50;
}

.fab-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.2);
  z-index: -1;
}

.fab-btn {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  border: none;
  background: var(--ds-bg-card);
  color: var(--ds-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 2px 12px rgba(0,0,0,0.12);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  -webkit-tap-highlight-color: transparent;
}
.fab-btn:active {
  transform: scale(0.9);
}
.fab-open {
  background: var(--ds-primary-500);
  color: white;
  box-shadow: 0 4px 16px rgba(245, 158, 11, 0.3);
}

.fab-nav {
  position: absolute;
  bottom: 56px;
  left: 0;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-lg);
  padding: 0.5rem;
  box-shadow: 0 4px 24px rgba(0,0,0,0.12);
  border: 1px solid rgba(0,0,0,0.04);
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 140px;
}

.fab-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border-radius: var(--ds-radius-md);
  text-decoration: none;
  color: var(--ds-text-muted);
  font-size: 0.8rem;
  font-weight: 600;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
  cursor: pointer;
}
.fab-item * {
  pointer-events: none;
}
.fab-item:active {
  background: var(--ds-bg-page);
}
.fab-item.active {
  color: var(--ds-primary-600);
  background: var(--ds-primary-50);
}
.fab-item.market-closed {
  color: var(--ds-danger-600);
}
.fab-item.market-closed.active {
  background: var(--ds-danger-50);
}
.fab-item.disabled {
  opacity: 0.4;
  cursor: default;
}

.fab-label {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 0;
}

.fab-market-closed-text {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.1rem 0.36rem;
  border-radius: 999px;
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
  font-size: 0.66rem;
  font-weight: 800;
}

/* ═══ Transitions ═══ */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

.slide-up-enter-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-up-leave-active {
  transition: all 0.2s ease-in;
}
.slide-up-enter-from {
  opacity: 0;
  transform: translateY(10px) scale(0.95);
}
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.95);
}

.spin-enter-active, .spin-leave-active {
  transition: all 0.2s;
}
.spin-enter-from {
  opacity: 0;
  transform: rotate(-90deg);
}
.spin-leave-to {
  opacity: 0;
  transform: rotate(90deg);
}
</style>
