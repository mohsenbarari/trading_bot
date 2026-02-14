<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Home, Store, User, MessageCircle, Shield, Menu, X } from 'lucide-vue-next'

const route = useRoute()
const userRole = ref<string>('')
const isExpanded = ref(false)

// Auto-collapse on the market page
const isMarketPage = computed(() => route.name === 'market')

// Close when navigating
watch(() => route.name, () => {
  isExpanded.value = false
})

onMounted(async () => {
  try {
    const token = localStorage.getItem('auth_token')
    if (!token) return
    const { apiFetch } = await import('../utils/auth')
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      const data = await res.json()
      userRole.value = data.role
    }
  } catch (e) {
    // silent
  }
})

const isAdmin = computed(() => ['مدیر ارشد', 'مدیر میانی'].includes(userRole.value))

const baseItems = [
  { name: 'dashboard', label: 'خانه', icon: Home, path: '/' },
  { name: 'market', label: 'بازار', icon: Store, path: '/market' },
  { name: 'messenger', label: 'پیام‌رسان', icon: MessageCircle, path: '#', disabled: true },
  { name: 'profile', label: 'پروفایل', icon: User, path: '/profile' },
]

const navItems = computed(() => {
  const items = [...baseItems]
  if (isAdmin.value) {
    items.push({ name: 'admin', label: 'مدیریت', icon: Shield, path: '/admin', disabled: false })
  }
  return items
})

function toggleNav() {
  isExpanded.value = !isExpanded.value
}
</script>

<template>
  <!-- ═══ Normal Bottom Nav (non-market pages) ═══ -->
  <nav v-if="!isMarketPage" class="bottom-nav-wrapper">
    <div class="bottom-nav-bar">
      <template v-for="item in navItems" :key="item.name">
        <div v-if="item.disabled" class="nav-item disabled">
          <component :is="item.icon" :size="22" />
          <span class="nav-label">{{ item.label }}</span>
          <span class="soon-dot"></span>
        </div>
        <router-link v-else :to="item.path" class="nav-item" :class="{ active: route.name === item.name }">
          <div class="nav-icon-wrap" :class="{ 'icon-active': route.name === item.name }">
            <component :is="item.icon" :size="22" :stroke-width="route.name === item.name ? 2.5 : 1.8" />
          </div>
          <span class="nav-label">{{ item.label }}</span>
        </router-link>
      </template>
    </div>
  </nav>

  <!-- ═══ Collapsed FAB on market page ═══ -->
  <div v-else class="fab-container">
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
          <router-link v-else :to="item.path" class="fab-item" :class="{ active: route.name === item.name }" @click="isExpanded = false">
            <component :is="item.icon" :size="20" />
            <span>{{ item.label }}</span>
          </router-link>
        </template>
      </div>
    </transition>

    <!-- Toggle button -->
    <button class="fab-btn" @click="toggleNav" :class="{ 'fab-open': isExpanded }">
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
  max-width: 480px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 0.5rem 0.25rem;
  border-radius: 1.25rem;
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
  border-radius: 0.75rem;
  text-decoration: none;
  color: #9ca3af;
  transition: all 0.25s;
  -webkit-tap-highlight-color: transparent;
  position: relative;
}
.nav-item.active { color: #d97706; }
.nav-item.disabled { opacity: 0.4; cursor: default; }

.nav-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 0.75rem;
  transition: all 0.25s;
}
.icon-active {
  background: #fffbeb;
  color: #d97706;
}

.nav-label {
  font-size: 0.6rem;
  font-weight: 600;
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
  background: white;
  color: #6b7280;
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
  background: #f59e0b;
  color: white;
  box-shadow: 0 4px 16px rgba(245, 158, 11, 0.3);
}

.fab-nav {
  position: absolute;
  bottom: 56px;
  left: 0;
  background: white;
  border-radius: 1rem;
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
  border-radius: 0.75rem;
  text-decoration: none;
  color: #6b7280;
  font-size: 0.8rem;
  font-weight: 600;
  transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.fab-item:active {
  background: #f9fafb;
}
.fab-item.active {
  color: #d97706;
  background: #fffbeb;
}
.fab-item.disabled {
  opacity: 0.4;
  cursor: default;
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
