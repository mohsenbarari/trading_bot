<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronLeft, Database, Settings, Smartphone, UserRound } from 'lucide-vue-next'
import {
  AppActionCard,
  AppIconButton,
  AppPage,
  AppSectionCard,
  AppStatusBadge,
} from '../components/ui'
import { WorkspaceNotice } from '../components/workspace'
import { currentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'
import { openTelegramLink, requestTelegramLink } from '../services/telegramLink'
import TelegramConnectPanel from '../components/account/TelegramConnectPanel.vue'

const router = useRouter()
const telegramLinkBusy = ref(false)
const telegramLinkError = ref<string | null>(null)

interface AccountAction {
  key: string
  title: string
  description: string
  icon: Component
  action: () => void
}

const user = computed(() => currentUserSummary.value)
const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)
const displayName = computed(() => user.value?.full_name?.trim() || user.value?.account_name?.trim() || 'کاربر')
const isInactiveAccount = computed(() => user.value?.account_status === 'inactive')
const accountStatusLabel = computed(() => (isInactiveAccount.value ? 'غیرفعال' : 'فعال'))
const accountStatusTone = computed(() => (isInactiveAccount.value ? 'danger' : 'success'))
const telegramConnected = computed(() => currentUserSummary.value?.telegram_linked === true)
const showTelegramConnectPanel = computed(() => (
  !isAccountant.value
  && (
    currentUserSummary.value?.can_connect_telegram === true
    || telegramConnected.value
  )
))

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
    description: isAccountant.value ? 'دسترسی‌های مجاز حسابدار و حافظه دستگاه' : 'امنیت حساب، حافظه دستگاه و خروج',
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
    description: 'پاک‌سازی فایل‌های دانلود شده و داده‌های محلی',
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

async function connectTelegram() {
  if (telegramLinkBusy.value || telegramConnected.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = null
  try {
    const payload = await requestTelegramLink()
    if (payload.telegram_url) {
      openTelegramLink(payload.telegram_url)
      return
    }
    telegramLinkError.value = payload.detail || 'لینک اتصال تلگرام آماده نشد.'
  } catch (error: any) {
    telegramLinkError.value = error?.message || 'ساخت لینک اتصال تلگرام ناموفق بود.'
  } finally {
    telegramLinkBusy.value = false
  }
}

onMounted(() => {
  void primeCurrentUserSummary(true)
})
</script>

<template>
  <div class="ds-page account-hub-page">
    <AppPage>
      <header class="account-compact-header" aria-label="حساب کاربری">
        <AppIconButton type="button" class="account-return-control" label="بازگشت" size="sm" @click="router.back()">
          <ChevronLeft :size="18" />
        </AppIconButton>
        <div class="account-identity">
          <div class="account-identity-name-row">
            <span class="account-status-dot" :class="`account-status-dot--${accountStatusTone}`" aria-hidden="true"></span>
            <strong>{{ displayName }}</strong>
          </div>
          <AppStatusBadge class="account-status-badge" :tone="accountStatusTone">{{ accountStatusLabel }}</AppStatusBadge>
        </div>
        <div class="account-header-spacer" aria-hidden="true"></div>
      </header>

      <AppSectionCard
        class="account-section-card"
        title="پروفایل و تنظیمات"
        tone="primary"
      >
        <div class="account-action-grid">
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
        <TelegramConnectPanel
          v-if="showTelegramConnectPanel"
          class="account-telegram-panel"
          :connected="telegramConnected"
          :loading="telegramLinkBusy"
          :error="telegramLinkError"
          @connect="connectTelegram"
        />
      </AppSectionCard>

      <AppSectionCard
        class="account-section-card"
        title="امنیت و داده‌ها"
        description="نشست‌ها، حافظه دستگاه و داده‌های محلی را در یک سطح منظم ببینید."
      >
        <WorkspaceNotice
          v-if="sessionsRestriction"
          class="account-empty-state"
          tone="warning"
          :title="sessionsRestriction.title"
          :message="sessionsRestriction.description"
        />
        <div class="account-action-grid">
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
      </AppSectionCard>

      <AppSectionCard
        class="account-section-card"
        title="اعلان‌ها"
        description="اعلان‌های بازار، معامله و سیستم را از مسیر اختصاصی خود ببینید."
      >
        <div class="account-action-grid">
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
      </AppSectionCard>

    </AppPage>
  </div>
</template>

<style scoped>
.account-hub-page {
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.account-compact-header {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) 2.25rem;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.9rem;
  min-height: 2.25rem;
}

.account-return-control {
  white-space: nowrap;
}

.account-identity {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  min-height: 2.25rem;
  text-align: center;
}

.account-identity-name-row {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.42rem;
  max-width: 100%;
}

.account-identity-name-row strong {
  min-width: 0;
  max-width: min(16rem, 56vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-lg);
  line-height: 1.35;
}

.account-status-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 999px;
  flex: 0 0 auto;
  box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.13);
}

.account-status-dot--success {
  background: var(--ds-success-600);
}

.account-status-dot--danger {
  background: var(--ds-danger-600);
  box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.13);
}

.account-status-badge {
  font-size: var(--ds-font-xs);
  line-height: 1.2;
}

.account-header-spacer {
  width: 2.25rem;
  height: 2.25rem;
}

.account-section-card + .account-section-card {
  margin-top: 0.75rem;
}

.account-action-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.account-telegram-panel {
  margin-top: 0.75rem;
}

@media (max-width: 700px) {
  .account-action-grid {
    grid-template-columns: 1fr;
  }
}
</style>
