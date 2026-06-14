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
import AppActionCard from './ui/AppActionCard.vue'
import AppMetricCard from './ui/AppMetricCard.vue'
import AppStatusBadge from './ui/AppStatusBadge.vue'
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

const adminMetrics = computed(() => [
  {
    label: 'سطح دسترسی',
    value: accessNote.value,
    hint: isCachedSuperAdmin() ? 'همه ابزارها فعال است' : 'ابزارها براساس نقش محدود شده‌اند',
    tone: isCachedSuperAdmin() ? 'success' : 'info',
  },
  {
    label: 'دسته‌های فعال',
    value: actionGroups.value.length,
    hint: 'پنل‌های قابل استفاده',
    tone: 'primary',
  },
  {
    label: 'ابزارهای مجاز',
    value: actions.value.length,
    hint: 'عملیات در دسترس',
    tone: 'neutral',
  },
] as const)

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

    <section class="admin-metrics" aria-label="وضعیت پنل مدیریت">
      <AppMetricCard
        v-for="metric in adminMetrics"
        :key="metric.label"
        :label="metric.label"
        :value="metric.value"
        :hint="metric.hint"
        :tone="metric.tone"
      />
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
            <div class="section-title-row">
              <h2>{{ group.title }}</h2>
              <AppStatusBadge tone="primary">{{ group.actions.length }} ابزار</AppStatusBadge>
            </div>
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
          <AppActionCard
            v-for="action in group.actions"
            :key="action.key"
            class="admin-action-btn hub-action"
            :class="action.variant"
            :title="action.label"
            :description="action.description"
            :tone="action.variant === 'primary' ? 'primary' : 'neutral'"
            @select="$emit('navigate', action.key)"
          >
            <template #icon>
              <component :is="action.icon" :size="20" />
            </template>
          </AppActionCard>
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

.admin-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
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

.section-title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
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

@media (max-width: 640px) {
  .admin-metrics {
    grid-template-columns: 1fr;
  }

  .section-title-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 0.25rem;
  }
}
</style>
