<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  userRole: string;
}>()

const emit = defineEmits(['navigate'])

// بر اساس نقش کاربر، دکمه‌های مجاز را مشخص می‌کنیم
const menuItems = computed(() => {
  const role = props.userRole;
  let items = [
    { id: 'home', icon: 'home', label: 'خانه' },
    { id: 'profile', icon: 'profile', label: 'پروفایل' }
  ];

  if (role === 'SUPER_ADMIN') {
    items.push({ id: 'create_invitation', icon: 'add', label: 'دعوت' });
  } else if (role !== 'WATCH') {
    items.push({ id: 'view_my_trades', icon: 'trades', label: 'معاملات' });
    items.push({ id: 'create_trade_offer', icon: 'offer', label: 'پیشنهاد' });
    if (role === 'MIDDLE_MANAGER') {
      items.push({ id: 'manage_users', icon: 'users', label: 'کاربران' });
    }
  }
  
  return items;
});

// آیکون‌های SVG برای دکمه‌ها
const icons = {
  home: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`,
  profile: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  add: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>`,
  trades: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>`,
  offer: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 15-4-4h8l-4 4zM12 7v8"/></svg>`,
  users: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
};
</script>

<template>
  <nav class="bottom-nav">
    <button
      v-for="item in menuItems"
      :key="item.id"
      class="nav-button"
      @click="emit('navigate', item.id)"
    >
      <span class="nav-icon" v-html="icons[item.icon]"></span>
      <span class="nav-label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.bottom-nav {
  display: flex;
  justify-content: space-around;
  align-items: center;
  background-color: var(--card-bg);
  border-top: 1px solid var(--border-color);
  padding: 8px 0;
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.05);
  flex-shrink: 0;
  z-index: 1000;
}

.nav-button {
  background: none;
  border: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 8px;
  cursor: pointer;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 10px;
  font-weight: 500;
  transition: color 0.2s ease;
}

.nav-button:hover {
  color: var(--primary-color);
}

.nav-icon {
  margin-bottom: 2px;
}

.nav-icon :deep(svg) {
  width: 24px;
  height: 24px;
}
</style>

