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
import AppButton from './ui/AppButton.vue'
import AppPageHeader from './ui/AppPageHeader.vue'
import AppSectionCard from './ui/AppSectionCard.vue'
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
      <AppPageHeader
        eyebrow="پنل مدیریت"
        title="پنل مدیریت"
        description="بخش مورد نظر خود را انتخاب کنید و فقط ابزارهای مجاز نقش فعلی را استفاده کنید."
      >
        <template #actions>
          <HelpPopover
            floating
            button-test="admin-panel-help"
            note-test="admin-panel-help-note"
            label="راهنمای پنل مدیریت"
            text="از این منو برای ورود به بخش‌های مدیریتی مجاز حساب خود استفاده کن. گزینه‌های حساس مثل تنظیمات سیستم فقط برای مدیر ارشد نمایش داده می‌شوند."
          />
        </template>
      </AppPageHeader>
      <span class="admin-intro-kicker">{{ accessNote }}</span>
      <div class="admin-intro-badges" aria-label="وضعیت پنل مدیریت">
        <AppStatusBadge tone="primary">
          {{ actionGroups.length }} دسته
        </AppStatusBadge>
        <AppStatusBadge tone="neutral">
          {{ actions.length }} ابزار
        </AppStatusBadge>
      </div>
    </section>

    <AppSectionCard
      v-for="group in actionGroups"
      :key="group.key"
      :title="group.title"
      :description="group.description"
      class="admin-section-card admin-accordion"
      :class="{ open: openSections[group.key] }"
    >
      <template #actions>
        <AppButton
          :id="`admin-${group.key}-header`"
          class="admin-accordion-toggle"
          type="button"
          variant="ghost"
          size="sm"
          :aria-expanded="openSections[group.key]"
          :aria-controls="`admin-${group.key}-panel`"
          @click="toggleSection(group.key)"
        >
          <component :is="group.icon" :size="18" class="section-icon" />
          <AppStatusBadge tone="primary">{{ group.actions.length }} ابزار</AppStatusBadge>
          <component :is="openSections[group.key] ? ChevronDown : ChevronLeft" :size="18" class="admin-section-card-icon" />
        </AppButton>
      </template>
      <div
        :id="`admin-${group.key}-panel`"
        v-show="openSections[group.key]"
        class="admin-accordion-body"
        role="region"
        :aria-labelledby="`admin-${group.key}-header`"
      >
        <div class="action-grid">
          <AppActionCard
            v-for="action in group.actions"
            :key="action.key"
            class="admin-panel-action hub-action"
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
    </AppSectionCard>

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
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding: 1rem;
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
}

.admin-intro-kicker {
  display: inline-flex;
  width: fit-content;
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.admin-intro-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.admin-accordion {
  margin-bottom: 0;
}

.section-icon {
  color: var(--ds-primary-700);
}

.admin-accordion-toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: auto;
  border: 0;
  padding: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  color: var(--ds-text-primary);
  cursor: pointer;
  font-family: inherit;
}

.action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.65rem;
}

.admin-panel-action {
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

</style>
