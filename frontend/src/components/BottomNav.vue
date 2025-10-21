<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  userRole: string;
}>()

const emit = defineEmits(['navigate'])

// بر اساس نقش کاربر، دکمه‌های جدید را مشخص می‌کنیم
const menuItems = computed(() => {
  // برای نقش "تماشا"، هیچ دکمه‌ای در نوار ناوبری نمایش داده نمی‌شود
  if (props.userRole === 'WATCH') {
    return [];
  }
  
  // برای سایر نقش‌ها، منوی اصلی و ساده شده نمایش داده می‌شود
  return [
    { id: 'home', icon: 'home', label: 'خانه' },
    { id: 'trade', icon: 'trades', label: 'معامله' },
    { id: 'profile', icon: 'profile', label: 'پنل کاربر' },
    { id: 'settings', icon: 'settings', label: 'تنظیمات' },
  ];
});

// آیکون‌های SVG برای دکمه‌ها (آیکون تنظیمات اضافه شد)
const icons = {
  home: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>`,
  trades: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>`,
  profile: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  settings: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
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