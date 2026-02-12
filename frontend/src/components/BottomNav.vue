<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Home, Store, User, MessageCircle, Shield } from 'lucide-vue-next'

const route = useRoute()
const userRole = ref<string>('')

onMounted(async () => {
  try {
    const token = localStorage.getItem('auth_token')
    if (!token) return
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` }
    })
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
</script>

<template>
  <nav class="bottom-nav-wrapper">
    <div class="bottom-nav-bar">
      
      <template v-for="item in navItems" :key="item.name">
        <!-- Disabled item (messenger) -->
        <div
          v-if="item.disabled"
          class="nav-item disabled"
        >
          <component :is="item.icon" :size="22" />
          <span class="nav-label">{{ item.label }}</span>
          <span class="soon-dot"></span>
        </div>

        <!-- Active item -->
        <router-link
          v-else
          :to="item.path"
          class="nav-item"
          :class="{ active: route.name === item.name }"
        >
          <div class="nav-icon-wrap" :class="{ 'icon-active': route.name === item.name }">
            <component :is="item.icon" :size="22" :stroke-width="route.name === item.name ? 2.5 : 1.8" />
          </div>
          <span class="nav-label">{{ item.label }}</span>
        </router-link>
      </template>

    </div>
  </nav>
</template>

<style scoped>
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
  box-shadow: 
    0 -1px 1px rgba(0,0,0,0.02),
    0 4px 20px rgba(0,0,0,0.08);
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
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  -webkit-tap-highlight-color: transparent;
  position: relative;
}

.nav-item.active {
  color: #d97706;
}

.nav-item.disabled {
  opacity: 0.4;
  cursor: default;
}

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
  letter-spacing: -0.01em;
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
</style>
