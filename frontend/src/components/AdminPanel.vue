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
  variant: 'primary' | 'secondary'
  icon: Component
}

const actions = computed<AdminAction[]>(() => {
  if (isCachedMiddleManager()) {
    return [
      {
        key: 'create_invitation',
        label: 'ارسال لینک دعوت',
        variant: 'primary',
        icon: PlusCircle,
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
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
        variant: 'primary',
        icon: PlusCircle,
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
        variant: 'secondary',
        icon: Users,
      },
      {
        key: 'manage_commodities',
        label: 'مدیریت کالاها',
        variant: 'secondary',
        icon: Package,
      },
      {
        key: 'create_channel',
        label: 'ساخت کانال',
        variant: 'secondary',
        icon: PlusCircle,
      },
      {
        key: 'admin_messages',
        label: 'پیام‌های مدیریت',
        variant: 'secondary',
        icon: Megaphone,
      },
      {
        key: 'settings',
        label: 'تنظیمات سیستم',
        variant: 'secondary',
        icon: Settings,
      },
    ]
  }

  return [
    {
      key: 'create_invitation',
      label: 'ارسال لینک دعوت',
      variant: 'primary',
      icon: PlusCircle,
    },
    {
      key: 'manage_users',
      label: 'مدیریت کاربران',
      variant: 'secondary',
      icon: Users,
    },
    {
      key: 'manage_commodities',
      label: 'مدیریت کالاها',
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
  overflow: hidden;
  border-radius: 12px;
  background: var(--ds-bg-card);
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

.admin-action-list__item:last-child .admin-panel-action {
  box-shadow: none;
}

@media (min-width: 720px) {
  .admin-action-list {
    grid-template-columns: 1fr;
  }
}
</style>
