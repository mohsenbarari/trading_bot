<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell,
  BriefcaseBusiness,
  ChevronDown,
  ChevronLeft,
  Megaphone,
  Package,
  Settings,
  UserPlus,
  Users,
  WalletCards,
} from 'lucide-vue-next'
import { currentUserSummary, isAdminRole, primeCurrentUserSummary } from '../utils/currentUser'

const router = useRouter()

type OperationsSectionKey = 'relations' | 'management' | 'shortcuts'

interface OperationAction {
  key: string
  title: string
  description: string
  icon: Component
  action: () => void
  hidden?: boolean
}

const openSections = ref<Record<OperationsSectionKey, boolean>>({
  relations: true,
  management: true,
  shortcuts: true,
})

const user = computed(() => currentUserSummary.value)
const userRole = computed(() => user.value?.role || '')
const isAdmin = computed(() => isAdminRole(userRole.value))
const isSuperAdmin = computed(() => userRole.value === 'مدیر ارشد')
const isCustomer = computed(() => user.value?.is_customer === true)
const canUseOwnerRelations = computed(() => !isCustomer.value)

function toggleSection(section: OperationsSectionKey) {
  openSections.value[section] = !openSections.value[section]
}

const ownerActions = computed<OperationAction[]>(() => {
  if (!canUseOwnerRelations.value) return []
  return [
    {
      key: 'customers',
      title: 'مشتریان',
      description: 'دعوت، مدیریت، محدودیت و گزارش مشتریان',
      icon: Users,
      action: () => router.push({ name: 'profile', query: { workspace: 'customers' } }),
    },
    {
      key: 'accountants',
      title: 'حسابداران',
      description: 'دعوت، مدیریت نشست و تنظیمات حسابداران',
      icon: BriefcaseBusiness,
      action: () => router.push({ name: 'profile', query: { workspace: 'accountants' } }),
    },
  ]
})

const adminActions = computed<OperationAction[]>(() => {
  if (!isAdmin.value) return []

  const actions: OperationAction[] = [
    {
      key: 'create_invitation',
      title: 'ارسال دعوت‌نامه',
      description: 'ساخت لینک دعوت برای کاربران مجاز',
      icon: UserPlus,
      action: () => router.push({ name: 'admin', query: { section: 'create_invitation' } }),
    },
    {
      key: 'manage_users',
      title: 'مدیریت کاربران',
      description: 'مشاهده، جستجو و تنظیم کاربران پروژه',
      icon: Users,
      action: () => router.push({ name: 'admin', query: { section: 'manage_users' } }),
    },
  ]

  if (isSuperAdmin.value) {
    actions.push(
      {
        key: 'manage_commodities',
        title: 'مدیریت کالاها',
        description: 'تعریف کالا و aliasهای بازار',
        icon: Package,
        action: () => router.push({ name: 'admin', query: { section: 'manage_commodities' } }),
      },
      {
        key: 'admin_messages',
        title: 'پیام‌های مدیریت',
        description: 'مدیریت پیام‌های سراسری و مدیریتی',
        icon: Megaphone,
        action: () => router.push({ name: 'admin', query: { section: 'admin_messages' } }),
      },
      {
        key: 'settings',
        title: 'تنظیمات سیستم',
        description: 'تنظیمات حساس بازار و سیستم',
        icon: Settings,
        action: () => router.push({ name: 'admin', query: { section: 'settings' } }),
      },
    )
  }

  return actions
})

const utilityActions = computed<OperationAction[]>(() => [
  {
    key: 'notifications',
    title: 'اعلان‌ها',
    description: 'مشاهده اعلان‌های سیستم و معاملات',
    icon: Bell,
    action: () => router.push({ name: 'notifications' }),
  },
  {
    key: 'admin_panel',
    title: 'پنل مدیریت',
    description: 'ورود به منوی کامل مدیریت',
    icon: WalletCards,
    action: () => router.push({ name: 'admin' }),
    hidden: !isAdmin.value,
  },
].filter(action => !action.hidden))

const relationsEmptyState = computed(() => {
  if (isCustomer.value) {
    return {
      title: 'این بخش برای حساب مشتری فعال نیست',
      description: 'مدیریت مشتریان و حسابداران از حساب سرگروه انجام می‌شود. دسترسی‌های معاملاتی شما از همان مسیر کنترل می‌شود.',
    }
  }

  return {
    title: 'رابطه کاری فعالی برای نمایش وجود ندارد',
    description: 'اگر دسترسی شما باید شامل مشتری یا حسابدار باشد، این بخش بعد از همگام‌سازی نقش حساب فعال می‌شود.',
  }
})

const managementEmptyState = computed(() => {
  if (!user.value) {
    return {
      title: 'در حال دریافت نقش کاربر',
      description: 'بعد از شناسایی نقش، ابزارهای مدیریتی مجاز نمایش داده می‌شوند.',
    }
  }

  return {
    title: 'دسترسی مدیریتی فعال نیست',
    description: 'ابزارهای دعوت‌نامه، مدیریت کاربران، کالاها و تنظیمات سیستم فقط برای مدیران مجاز نمایش داده می‌شوند.',
  }
})

const managementNote = computed(() => {
  if (!isAdmin.value) return ''
  if (isSuperAdmin.value) return 'دسترسی کامل مدیریتی'
  return 'دسترسی مدیر میانی؛ تنظیمات سیستم و پیام‌های مدیریت فقط برای مدیر ارشد است.'
})

onMounted(() => {
  void primeCurrentUserSummary()
})
</script>

<template>
  <div class="ds-page operations-page">
    <header class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
        <h2>عملیات</h2>
      </div>
      <button class="back-button" type="button" @click="router.back()">
        <ChevronLeft :size="24" />
      </button>
    </header>

    <main class="operations-content">
      <section class="operations-intro">
        <h1>کارهای اجرایی</h1>
        <p>دسترسی‌های عملیاتی حساب شما در یک مسیر واحد قرار گرفته‌اند.</p>
      </section>

      <section class="ds-accordion operations-accordion" :class="{ open: openSections.relations }">
        <button class="ds-accordion-header operations-accordion-header" type="button" @click="toggleSection('relations')">
          <div class="ds-accordion-header-info">
            <Users :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>روابط کاری</h2>
              <span>مشتریان و حسابداران</span>
            </div>
          </div>
          <component :is="openSections.relations ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div v-show="openSections.relations" class="ds-accordion-body operations-accordion-body">
          <div v-if="ownerActions.length" class="action-grid">
            <button
              v-for="action in ownerActions"
              :key="action.key"
              type="button"
              class="hub-action"
              @click="action.action"
            >
              <span class="action-icon"><component :is="action.icon" :size="20" /></span>
              <span class="action-copy">
                <strong>{{ action.title }}</strong>
                <small>{{ action.description }}</small>
              </span>
              <ChevronLeft :size="18" class="action-chevron" />
            </button>
          </div>
          <div v-else class="operations-empty-state">
            <strong>{{ relationsEmptyState.title }}</strong>
            <p>{{ relationsEmptyState.description }}</p>
          </div>
        </div>
      </section>

      <section class="ds-accordion operations-accordion" :class="{ open: openSections.management }">
        <button class="ds-accordion-header operations-accordion-header" type="button" @click="toggleSection('management')">
          <div class="ds-accordion-header-info">
            <WalletCards :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>مدیریت</h2>
              <span>{{ managementNote || 'ابزارهای نقش مدیریتی' }}</span>
            </div>
          </div>
          <component :is="openSections.management ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div v-show="openSections.management" class="ds-accordion-body operations-accordion-body">
          <div v-if="adminActions.length" class="action-grid">
            <button
              v-for="action in adminActions"
              :key="action.key"
              type="button"
              class="hub-action"
              @click="action.action"
            >
              <span class="action-icon"><component :is="action.icon" :size="20" /></span>
              <span class="action-copy">
                <strong>{{ action.title }}</strong>
                <small>{{ action.description }}</small>
              </span>
              <ChevronLeft :size="18" class="action-chevron" />
            </button>
          </div>
          <div v-else class="operations-empty-state">
            <strong>{{ managementEmptyState.title }}</strong>
            <p>{{ managementEmptyState.description }}</p>
          </div>
        </div>
      </section>

      <section class="ds-accordion operations-accordion" :class="{ open: openSections.shortcuts }">
        <button class="ds-accordion-header operations-accordion-header" type="button" @click="toggleSection('shortcuts')">
          <div class="ds-accordion-header-info">
            <Bell :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>میانبرها</h2>
              <span>دسترسی سریع</span>
            </div>
          </div>
          <component :is="openSections.shortcuts ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div v-show="openSections.shortcuts" class="ds-accordion-body operations-accordion-body">
          <div class="action-grid">
            <button
              v-for="action in utilityActions"
              :key="action.key"
              type="button"
              class="hub-action"
              @click="action.action"
            >
              <span class="action-icon"><component :is="action.icon" :size="20" /></span>
              <span class="action-copy">
                <strong>{{ action.title }}</strong>
                <small>{{ action.description }}</small>
              </span>
              <ChevronLeft :size="18" class="action-chevron" />
            </button>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.operations-page {
  padding-bottom: 5rem;
}

.operations-content {
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  padding: var(--ds-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.operations-intro {
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-md);
  padding: 1rem;
}

.operations-intro h1 {
  margin: 0 0 0.35rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
}

.operations-intro p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.operations-accordion {
  margin-bottom: 0;
}

.operations-accordion-header {
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

.hub-action {
  width: 100%;
  min-height: 72px;
  display: grid;
  grid-template-columns: 44px 1fr 24px;
  align-items: center;
  gap: 0.75rem;
  direction: rtl;
  text-align: right;
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  padding: 0.85rem;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.18s ease;
}

.hub-action:active {
  transform: scale(0.985);
  background: var(--ds-primary-50);
}

.action-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--ds-radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
}

.action-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.action-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.35;
}

.action-copy small {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.6;
}

.action-chevron {
  color: var(--ds-text-placeholder);
}

.operations-empty-state {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.85rem;
  border-radius: var(--ds-radius-md);
  border: 1px dashed var(--ds-border-medium);
  background: var(--ds-bg-inset);
}

.operations-empty-state strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.operations-empty-state p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}
</style>
