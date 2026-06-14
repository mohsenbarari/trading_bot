<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronDown, ChevronLeft, Database, Settings, Smartphone, UserRound } from 'lucide-vue-next'
import { AppActionCard } from '../components/ui'
import { WorkspaceNotice } from '../components/workspace'
import { currentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'

const router = useRouter()
type AccountSectionKey = 'profile' | 'security' | 'notifications'

interface AccountAction {
  key: string
  title: string
  description: string
  icon: Component
  action: () => void
}

const openSections = ref<Record<AccountSectionKey, boolean>>({
  profile: true,
  security: true,
  notifications: true,
})

const user = computed(() => currentUserSummary.value)
const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)

function toggleSection(section: AccountSectionKey) {
  openSections.value[section] = !openSections.value[section]
}

const userDisplayName = computed(() => {
  const fullName = user.value?.full_name?.trim()
  const accountName = user.value?.account_name?.trim()
  return fullName || accountName || 'حساب کاربری'
})

const profileActions = computed<AccountAction[]>(() => [
  {
    key: 'profile',
    title: 'پروفایل من',
    description: 'مشاهده و ویرایش اطلاعات حساب',
    icon: UserRound,
    action: () => router.push({ name: 'profile' }),
  },
  {
    key: 'settings',
    title: 'تنظیمات کاربری',
    description: isAccountant.value ? 'تنظیمات مجاز حساب حسابدار' : 'تنظیمات حساب، نشست‌ها و خروج',
    icon: Settings,
    action: () => router.push({ name: 'account-storage' }),
  },
])

const securityActions = computed<AccountAction[]>(() => {
  const actions: AccountAction[] = []

  if (!isAccountant.value) {
    actions.push({
      key: 'sessions',
      title: 'نشست‌های فعال',
      description: 'بررسی و مدیریت دستگاه‌های فعال',
      icon: Smartphone,
      action: () => router.push({ name: 'account-security' }),
    })
  }

  actions.push({
    key: 'storage',
    title: 'حافظه و داده‌ها',
    description: 'پاک‌سازی فایل‌های دانلود شده پیام‌رسان',
    icon: Database,
    action: () => router.push({ name: 'account-storage' }),
  })

  return actions
})

const notificationActions = computed<AccountAction[]>(() => [
  {
    key: 'notifications',
    title: 'اعلان‌ها',
    description: 'اعلان‌های سیستمی، بازار و معاملات',
    icon: Bell,
    action: () => router.push({ name: 'account-notifications' }),
  },
])

const sessionsRestriction = computed(() => {
  if (!isAccountant.value) return null

  return {
    title: 'مدیریت نشست برای حسابدار فعال نیست',
    description: 'نشست‌های حسابدار توسط سرگروه مدیریت و در صورت نیاز منقضی می‌شود. این محدودیت با تنظیمات امنیتی پروژه هماهنگ است.',
  }
})

onMounted(() => {
  void primeCurrentUserSummary()
})
</script>

<template>
  <div class="ds-page account-hub-page">
    <header class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
        <h2>حساب</h2>
      </div>
      <button class="back-button" type="button" @click="router.back()">
        <ChevronLeft :size="24" />
      </button>
    </header>

    <main class="account-hub-content">
      <section class="account-intro">
        <h1>{{ userDisplayName }}</h1>
        <p>پروفایل، تنظیمات مجاز، اعلان‌ها و داده‌های دستگاه در یک مسیر واحد قرار دارند.</p>
      </section>

      <section class="ds-accordion account-accordion" :class="{ open: openSections.profile }">
        <button
          id="account-profile-header"
          class="ds-accordion-header account-accordion-header"
          type="button"
          :aria-expanded="openSections.profile"
          aria-controls="account-profile-panel"
          @click="toggleSection('profile')"
        >
          <div class="ds-accordion-header-info">
            <UserRound :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>پروفایل و تنظیمات</h2>
              <span>اطلاعات حساب و مسیرهای شخصی</span>
            </div>
          </div>
          <component :is="openSections.profile ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div
          id="account-profile-panel"
          v-show="openSections.profile"
          class="ds-accordion-body account-accordion-body"
          role="region"
          aria-labelledby="account-profile-header"
        >
          <div class="action-grid">
            <AppActionCard
              v-for="action in profileActions"
              :key="action.key"
              class="hub-action"
              :title="action.title"
              :description="action.description"
              @select="action.action"
            >
              <template #icon>
                <component :is="action.icon" :size="20" />
              </template>
            </AppActionCard>
          </div>
        </div>
      </section>

      <section class="ds-accordion account-accordion" :class="{ open: openSections.security }">
        <button
          id="account-security-header"
          class="ds-accordion-header account-accordion-header"
          type="button"
          :aria-expanded="openSections.security"
          aria-controls="account-security-panel"
          @click="toggleSection('security')"
        >
          <div class="ds-accordion-header-info">
            <Smartphone :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>امنیت و داده‌ها</h2>
              <span>نشست‌ها، حافظه و فایل‌های محلی</span>
            </div>
          </div>
          <component :is="openSections.security ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div
          id="account-security-panel"
          v-show="openSections.security"
          class="ds-accordion-body account-accordion-body"
          role="region"
          aria-labelledby="account-security-header"
        >
          <WorkspaceNotice
            v-if="sessionsRestriction"
            class="account-empty-state"
            tone="warning"
            :title="sessionsRestriction.title"
            :message="sessionsRestriction.description"
          />
          <div class="action-grid">
            <AppActionCard
              v-for="action in securityActions"
              :key="action.key"
              class="hub-action"
              :title="action.title"
              :description="action.description"
              @select="action.action"
            >
              <template #icon>
                <component :is="action.icon" :size="20" />
              </template>
            </AppActionCard>
          </div>
        </div>
      </section>

      <section class="ds-accordion account-accordion" :class="{ open: openSections.notifications }">
        <button
          id="account-notifications-header"
          class="ds-accordion-header account-accordion-header"
          type="button"
          :aria-expanded="openSections.notifications"
          aria-controls="account-notifications-panel"
          @click="toggleSection('notifications')"
        >
          <div class="ds-accordion-header-info">
            <Bell :size="18" class="section-icon" />
            <div class="section-title-copy">
              <h2>اعلان‌ها</h2>
              <span>پیام‌های سیستم، بازار و معاملات</span>
            </div>
          </div>
          <component :is="openSections.notifications ? ChevronDown : ChevronLeft" :size="20" class="ds-accordion-icon" />
        </button>
        <div
          id="account-notifications-panel"
          v-show="openSections.notifications"
          class="ds-accordion-body account-accordion-body"
          role="region"
          aria-labelledby="account-notifications-header"
        >
          <div class="action-grid">
            <AppActionCard
              v-for="action in notificationActions"
              :key="action.key"
              class="hub-action"
              :title="action.title"
              :description="action.description"
              @select="action.action"
            >
              <template #icon>
                <component :is="action.icon" :size="20" />
              </template>
            </AppActionCard>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.account-hub-page {
  padding-bottom: 5rem;
}

.account-hub-content {
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  padding: var(--ds-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.account-intro {
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-md);
  padding: 1rem;
}

.account-intro h1 {
  margin: 0 0 0.35rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
}

.account-intro p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.account-accordion {
  margin-bottom: 0;
}

.account-accordion-header {
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
}

.account-empty-state {
  margin-bottom: 0.65rem;
}
</style>
