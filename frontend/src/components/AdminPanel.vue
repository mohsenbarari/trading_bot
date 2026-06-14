<script setup lang="ts">
import {
  ChevronDown,
  ChevronLeft,
  Megaphone,
  Package,
  PlusCircle,
  Settings,
  Users,
  WalletCards,
} from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import { isCachedMiddleManager, isCachedSuperAdmin } from '../utils/adminAccess'
import HelpPopover from './HelpPopover.vue'

defineEmits(['navigate'])

type AdminSectionKey = 'access' | 'catalog' | 'system'

interface AdminAction {
  key: string
  label: string
  description: string
  variant: 'primary' | 'secondary'
  icon: Component
  section: AdminSectionKey
}

const openSections = ref<Record<AdminSectionKey, boolean>>({
  access: true,
  catalog: true,
  system: true,
})

const sectionCopy: Record<AdminSectionKey, { title: string; description: string; icon: Component }> = {
  access: {
    title: 'دسترسی و کاربران',
    description: 'دعوت‌نامه و تنظیم کاربران',
    icon: Users,
  },
  catalog: {
    title: 'بازار و کالاها',
    description: 'کالاهای قابل معامله و نام‌های بازار',
    icon: Package,
  },
  system: {
    title: 'پیام‌ها و تنظیمات',
    description: 'فقط برای مدیر ارشد',
    icon: Settings,
  },
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
        section: 'access',
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
        description: 'جستجو، مشاهده و ورود به تنظیمات کاربران',
        variant: 'secondary',
        icon: Users,
        section: 'access',
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
        section: 'access',
      },
      {
        key: 'manage_users',
        label: 'مدیریت کاربران',
        description: 'جستجو، مشاهده و تنظیم کاربران',
        variant: 'secondary',
        icon: Users,
        section: 'access',
      },
      {
        key: 'manage_commodities',
        label: 'مدیریت کالاها',
        description: 'تعریف کالا و aliasهای بازار',
        variant: 'secondary',
        icon: Package,
        section: 'catalog',
      },
      {
        key: 'create_channel',
        label: 'ساخت کانال',
        description: 'ایجاد کانال و تنظیم مالک/اعضای اولیه',
        variant: 'secondary',
        icon: PlusCircle,
        section: 'system',
      },
      {
        key: 'admin_messages',
        label: 'پیام‌های مدیریت',
        description: 'پیام بازار و اعلان همگانی',
        variant: 'secondary',
        icon: Megaphone,
        section: 'system',
      },
      {
        key: 'settings',
        label: 'تنظیمات سیستم',
        description: 'تنظیمات حساس بازار، دعوت و امنیت',
        variant: 'secondary',
        icon: Settings,
        section: 'system',
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
      section: 'access',
    },
    {
      key: 'manage_users',
      label: 'مدیریت کاربران',
      description: 'مشاهده و تنظیم کاربران پروژه',
      variant: 'secondary',
      icon: Users,
      section: 'access',
    },
    {
      key: 'manage_commodities',
      label: 'مدیریت کالاها',
      description: 'تعریف کالا و aliasهای بازار',
      variant: 'secondary',
      icon: Package,
      section: 'catalog',
    },
  ]
})

const actionGroups = computed(() => {
  return (Object.keys(sectionCopy) as AdminSectionKey[])
    .map((key) => ({
      key,
      ...sectionCopy[key],
      actions: actions.value.filter((action) => action.section === key),
    }))
    .filter((group) => group.actions.length > 0)
})

const accessNote = computed(() => {
  if (isCachedSuperAdmin()) return 'دسترسی کامل مدیریتی'
  if (isCachedMiddleManager()) return 'دسترسی مدیر میانی'
  return 'دسترسی مدیریتی محدود'
})

function toggleSection(section: AdminSectionKey) {
  openSections.value[section] = !openSections.value[section]
}
</script>

<template>
  <div class="admin-panel-container">
    <section class="admin-intro">
      <HelpPopover
        floating
        button-test="admin-panel-help"
        note-test="admin-panel-help-note"
        label="راهنمای پنل مدیریت"
        text="از این منو برای ورود به بخش‌های مدیریتی مجاز حساب خود استفاده کن. گزینه‌های حساس مثل تنظیمات سیستم فقط برای مدیر ارشد نمایش داده می‌شوند."
      />
      <span class="admin-intro-kicker">{{ accessNote }}</span>
      <h1>پنل مدیریت</h1>
      <p>لطفاً بخش مورد نظر خود را انتخاب کنید:</p>
    </section>

    <section
      v-for="group in actionGroups"
      :key="group.key"
      class="ds-accordion admin-accordion"
      :class="{ open: openSections[group.key] }"
    >
      <button
        :id="`admin-${group.key}-header`"
        class="ds-accordion-header admin-accordion-header"
        type="button"
        :aria-expanded="openSections[group.key]"
        :aria-controls="`admin-${group.key}-panel`"
        @click="toggleSection(group.key)"
      >
        <div class="ds-accordion-header-info">
          <component :is="group.icon" :size="18" class="section-icon" />
          <div class="section-title-copy">
            <h2>{{ group.title }}</h2>
            <span>{{ group.description }}</span>
          </div>
        </div>
        <component :is="openSections[group.key] ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
      </button>
      <div
        :id="`admin-${group.key}-panel`"
        v-show="openSections[group.key]"
        class="ds-accordion-body admin-accordion-body"
        role="region"
        :aria-labelledby="`admin-${group.key}-header`"
      >
        <div class="action-grid">
          <button
            v-for="action in group.actions"
            :key="action.key"
            class="admin-action-btn hub-action"
            :class="action.variant"
            type="button"
            @click="$emit('navigate', action.key)"
          >
            <span class="admin-action-icon action-icon">
              <component :is="action.icon" :size="20" />
            </span>
            <span class="action-copy">
              <strong>{{ action.label }}</strong>
              <small>{{ action.description }}</small>
            </span>
            <ChevronLeft :size="18" class="action-chevron" />
          </button>
        </div>
      </div>
    </section>

    <section v-if="actionGroups.length === 0" class="admin-empty-state">
      <WalletCards :size="20" />
      <span>
        <strong>ابزار مدیریتی فعالی برای این نقش وجود ندارد</strong>
        <p>اگر این دسترسی باید فعال باشد، نقش کاربر را از تنظیمات مدیریتی بررسی کنید.</p>
      </span>
    </section>
  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.admin-intro {
  position: relative;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-md);
  padding: 1rem;
  padding-left: 3.75rem;
}

.admin-intro-kicker {
  display: inline-flex;
  width: fit-content;
  margin-bottom: 0.45rem;
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.admin-intro h1 {
  margin: 0 0 0.35rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
}

.admin-intro p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.admin-accordion {
  margin-bottom: 0;
}

.admin-accordion-header {
  width: 100%;
  border: 0;
  font-family: inherit;
  text-align: right;
}

.section-icon {
  color: var(--ds-primary-700);
}

.section-title-copy {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.section-title-copy h2 {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.45;
}

.section-title-copy span {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.5;
}

.action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.65rem;
}

.admin-action-btn {
  width: 100%;
  min-height: 72px;
  display: grid;
  grid-template-columns: 44px 1fr 24px;
  align-items: center;
  gap: 0.75rem;
  direction: rtl;
  text-align: right;
  padding: 0.85rem;
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.18s ease;
}

.admin-action-btn:hover {
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
  background: var(--ds-primary-50);
}

.admin-action-btn:active {
  transform: scale(0.985);
  background: var(--ds-primary-50);
}

.admin-action-btn.primary {
  background: var(--ds-gradient-primary);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
}

.admin-action-btn.primary:hover {
  background: linear-gradient(135deg, var(--ds-primary-600), var(--ds-primary-700));
  color: white;
}

.admin-action-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--ds-radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  flex: 0 0 auto;
}

.admin-action-btn.primary .admin-action-icon {
  background: rgba(255, 255, 255, 0.22);
  color: white;
}

.action-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.action-copy strong {
  color: inherit;
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.35;
}

.action-copy small {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.6;
}

.admin-action-btn.primary .action-copy small {
  color: rgba(255, 255, 255, 0.82);
}

.action-chevron {
  color: var(--ds-text-placeholder);
}

.admin-action-btn.primary .action-chevron {
  color: rgba(255, 255, 255, 0.78);
}

.admin-empty-state {
  display: grid;
  grid-template-columns: 32px 1fr;
  gap: 0.65rem;
  align-items: start;
  padding: 0.85rem;
  border-radius: var(--ds-radius-md);
  border: 1px dashed var(--ds-border-medium);
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
}

.admin-empty-state strong {
  display: block;
  margin-bottom: 0.25rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.admin-empty-state p {
  margin: 0;
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}
</style>
