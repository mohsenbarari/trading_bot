<script setup lang="ts">
import { Megaphone, Package, PlusCircle, Settings, Users } from 'lucide-vue-next'
import { computed } from 'vue'
import { isCachedMiddleManager, isCachedSuperAdmin } from '../utils/adminAccess'
import HelpPopover from './HelpPopover.vue'

defineEmits(['navigate']);

const actions = computed(() => {
  if (isCachedMiddleManager()) {
    return [
      { key: 'create_invitation', label: 'ارسال لینک دعوت', variant: 'primary', icon: PlusCircle },
      { key: 'manage_users', label: 'مدیریت کاربران', variant: 'secondary', icon: Users },
    ]
  }

  if (isCachedSuperAdmin()) {
    return [
      { key: 'create_invitation', label: 'ارسال لینک دعوت', variant: 'primary', icon: PlusCircle },
      { key: 'manage_commodities', label: 'مدیریت کالاها', variant: 'secondary', icon: Package },
      { key: 'manage_users', label: 'مدیریت کاربران', variant: 'secondary', icon: Users },
      { key: 'admin_messages', label: 'پیام‌های مدیریت', variant: 'secondary', icon: Megaphone },
      { key: 'settings', label: 'تنظیمات سیستم', variant: 'secondary', icon: Settings },
    ]
  }

  return [
    { key: 'create_invitation', label: 'ارسال لینک دعوت', variant: 'primary', icon: PlusCircle },
    { key: 'manage_commodities', label: 'مدیریت کالاها', variant: 'secondary', icon: Package },
    { key: 'manage_users', label: 'مدیریت کاربران', variant: 'secondary', icon: Users },
  ]
})
</script>

<template>
  <div class="admin-panel-container">
    
    <div class="card management-card">
      <HelpPopover
        floating
        button-test="admin-panel-help"
        note-test="admin-panel-help-note"
        label="راهنمای پنل مدیریت"
        text="از این منو برای ورود به بخش‌های مدیریتی مجاز حساب خود استفاده کن. گزینه‌های حساس مثل تنظیمات سیستم فقط برای مدیر ارشد نمایش داده می‌شوند."
      />
      <h2>پنل مدیریت</h2>
      
      <div class="button-group">
        <button
          v-for="action in actions"
          :key="action.key"
          class="admin-action-btn"
          :class="action.variant"
          @click="$emit('navigate', action.key)"
        >
          <span class="admin-action-icon">
            <component :is="action.icon" :size="18" />
          </span>
          <span>{{ action.label }}</span>
        </button>
      </div>
      <div class="version-tag">UI v1.4</div>
    </div>

  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: var(--ds-page-padding); 
}
.card.management-card {
  position: relative;
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-xl);
  padding: 1.5rem;
  padding-left: 4rem;
  box-shadow: var(--ds-shadow-md);
}
h2 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-weight: 800;
  font-size: var(--ds-font-xl);
  color: var(--ds-text-primary);
}
.button-group {
    display: grid;
    grid-template-columns: 1fr; 
    gap: var(--ds-section-gap);
}
.admin-action-btn {
  width: 100%;
  padding: 1rem 1.25rem;
  font-size: var(--ds-font-md);
  font-weight: 850;
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.96), rgba(255, 255, 255, 0.98));
  color: var(--ds-text-primary);
  border: 1px solid rgba(245, 158, 11, 0.16);
  border-radius: var(--ds-radius-lg);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  direction: rtl; 
  gap: 0.75rem;
  transition: all 0.2s ease-in-out;
  box-shadow: var(--ds-shadow-xs);
  font-family: inherit;
}

.admin-action-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.15rem;
  height: 2.15rem;
  border-radius: 0.85rem;
  background: rgba(245, 158, 11, 0.12);
  color: var(--ds-primary-700);
  flex: 0 0 auto;
}

.admin-action-btn:hover {
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
  background: var(--ds-primary-50);
}
.admin-action-btn:active {
  background: var(--ds-primary-100);
  transform: scale(0.98);
}
.admin-action-btn.primary {
  background: var(--ds-gradient-primary);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
}

.admin-action-btn.primary .admin-action-icon {
  background: rgba(255, 255, 255, 0.22);
  color: white;
}
.admin-action-btn.primary:hover {
  background: linear-gradient(135deg, var(--ds-primary-600), var(--ds-primary-700));
  color: white;
}
.admin-action-btn.primary-alt {
  background: linear-gradient(135deg, #0f766e, var(--ds-success-500));
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.24);
}
.admin-action-btn.primary-alt:hover {
  background: linear-gradient(135deg, #0b5f59, var(--ds-success-600));
  color: white;
}
.admin-action-btn.secondary {
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  border: 1px solid rgba(245, 158, 11, 0.2);
  box-shadow: var(--ds-shadow-sm);
}
.admin-action-btn.secondary:hover {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
}
.version-tag {
    text-align: center;
    font-size: var(--ds-font-xs);
    color: var(--ds-text-placeholder);
    margin-top: 1rem;
    opacity: 0.5;
}
</style>
