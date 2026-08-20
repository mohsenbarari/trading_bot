<script setup lang="ts">
import { ChevronLeft, Megaphone, Package, PlusCircle, Settings, Users } from 'lucide-vue-next'
import { computed } from 'vue'
import type { Component } from 'vue'
import { isCachedMiddleManager, isCachedSuperAdmin } from '../utils/adminAccess'
import AppListItem from './ui/AppListItem.vue'

defineEmits(['navigate'])

interface AdminAction {
  key: string
  label: string
  description: string
  variant: 'primary' | 'secondary'
  icon: Component
}

const actions = computed<AdminAction[]>(() => {
  if (isCachedMiddleManager()) {
    return [
      {
        key: 'create_invitation',
        label: 'ارسال لینک دعوت',
        description: 'ساخت لینک دعوت برای نقش‌های مجاز',
        variant: 'primary',
        icon: PlusCircle,
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
        description: 'جستجو، مشاهده و ورود به تنظیمات کاربران',
        variant: 'secondary',
        icon: Users,
      },
    ]
  }

  if (isCachedSuperAdmin()) {
    return [
      {
        key: 'create_invitation',
        label: 'ارسال لینک دعوت',
        description: 'ساخت لینک دعوت برای کاربران پروژه',
        variant: 'primary',
        icon: PlusCircle,
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
        description: 'جستجو، مشاهده و تنظیم کاربران',
        variant: 'secondary',
        icon: Users,
      },
      {
        key: 'manage_commodities',
        label: 'مدیریت کالاها',
        description: 'تعریف کالا و aliasهای بازار',
        variant: 'secondary',
        icon: Package,
      },
      {
        key: 'create_channel',
        label: 'ساخت کانال',
        description: 'ایجاد کانال و تنظیم مالک/اعضای اولیه',
        variant: 'secondary',
        icon: PlusCircle,
      },
      {
        key: 'admin_messages',
        label: 'پیام‌های مدیریت',
        description: 'پیام بازار و اعلان همگانی',
        variant: 'secondary',
        icon: Megaphone,
      },
      {
        key: 'settings',
        label: 'تنظیمات سیستم',
        description: 'تنظیمات حساس بازار، دعوت و امنیت',
        variant: 'secondary',
        icon: Settings,
      },
    ]
  }

  return [
    {
      key: 'create_invitation',
      label: 'ارسال لینک دعوت',
      description: 'ساخت لینک دعوت برای کاربران مجاز',
      variant: 'primary',
      icon: PlusCircle,
    },
    {
      key: 'manage_users',
      label: 'مدیریت کاربران',
      description: 'مشاهده و تنظیم کاربران پروژه',
      variant: 'secondary',
      icon: Users,
    },
    {
      key: 'manage_commodities',
      label: 'مدیریت کالاها',
      description: 'تعریف کالا و aliasهای بازار',
      variant: 'secondary',
      icon: Package,
    },
  ]
})
</script>

<template>
  <nav class="admin-panel-container" aria-label="ابزارهای مدیریت">
    <ul class="admin-action-list">
      <li v-for="action in actions" :key="action.key" class="admin-action-list__item">
        <AppListItem
          class="admin-panel-action hub-action"
          :class="action.variant"
          interactive
          :title="action.label"
          :description="action.description"
          @select="$emit('navigate', action.key)"
        >
          <template #leading>
            <component :is="action.icon" :size="20" />
          </template>
          <template #trailing>
            <ChevronLeft :size="18" aria-hidden="true" />
          </template>
        </AppListItem>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.admin-panel-container {
  min-width: 0;
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

.admin-action-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.admin-action-list__item {
  min-width: 0;
}

.admin-panel-action {
  width: 100%;
  min-height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: 0;
  background: var(--ds-bg-card);
  box-shadow: inset 0 -1px 0 var(--ds-native-hairline);
}

@media (min-width: 720px) {
  .admin-action-list {
    grid-template-columns: 1fr;
  }
}
</style>
