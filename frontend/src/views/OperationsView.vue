<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bell,
  BriefcaseBusiness,
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

const user = computed(() => currentUserSummary.value)
const userRole = computed(() => user.value?.role || '')
const isAdmin = computed(() => isAdminRole(userRole.value))
const isSuperAdmin = computed(() => userRole.value === 'مدیر ارشد')
const isCustomer = computed(() => user.value?.is_customer === true)
const canUseOwnerRelations = computed(() => !isCustomer.value)

const ownerActions = computed(() => {
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

const adminActions = computed(() => {
  if (!isAdmin.value) return []

  const actions = [
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

const utilityActions = computed(() => [
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

      <section v-if="ownerActions.length" class="operations-section">
        <div class="section-heading">
          <h2>روابط کاری</h2>
          <span>مشتریان و حسابداران</span>
        </div>
        <div class="action-grid">
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
      </section>

      <section v-if="adminActions.length" class="operations-section">
        <div class="section-heading">
          <h2>مدیریت</h2>
          <span>ابزارهای نقش مدیریتی</span>
        </div>
        <div class="action-grid">
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
      </section>

      <section v-if="utilityActions.length" class="operations-section">
        <div class="section-heading">
          <h2>میانبرها</h2>
          <span>دسترسی سریع</span>
        </div>
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

.operations-section {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0 0.2rem;
}

.section-heading h2 {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
}

.section-heading span {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  white-space: nowrap;
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
</style>
