<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronLeft, Database, Settings, ShieldCheck, Smartphone, UserRound } from 'lucide-vue-next'
import {
  AppActionCard,
  AppIconButton,
  AppPage,
  AppPageHeader,
  AppSectionCard,
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
  void primeCurrentUserSummary()
})
</script>

<template>
  <div class="ds-page account-hub-page">
    <AppPage>
      <AppPageHeader
        eyebrow="حساب"
        title="مرکز حساب کاربری"
      >
        <template #actions>
          <AppIconButton type="button" class="account-return-control" label="بازگشت" size="sm" @click="router.back()">
            <ChevronLeft :size="18" />
          </AppIconButton>
        </template>
      </AppPageHeader>

      <AppSectionCard
        class="account-section-card"
        title="پروفایل و تنظیمات"
        description="اطلاعات حساب و مسیرهای شخصی را از یک نقطه مدیریت کنید."
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

      <AppSectionCard
        class="account-section-card"
        title="راهنمای دسترسی"
        description="این خلاصه کمک می‌کند قبل از ورود به تنظیمات بدانید کدام مسیرها برای نقش فعلی شما فعال هستند."
      >
        <div class="account-guidance-list">
          <div class="account-guidance-item">
            <ShieldCheck :size="18" />
            <p>پروفایل و اعلان‌ها برای همه نقش‌ها در دسترس هستند.</p>
          </div>
          <div class="account-guidance-item">
            <Smartphone :size="18" />
            <p>{{ isAccountant ? 'نشست‌های حسابدار توسط سرگروه مدیریت می‌شوند.' : 'نشست‌های فعال و خروج از حساب از بخش امنیت حساب در دسترس است.' }}</p>
          </div>
        </div>
      </AppSectionCard>
    </AppPage>
  </div>
</template>

<style scoped>
.account-hub-page {
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.account-return-control {
  white-space: nowrap;
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

.account-guidance-list {
  display: grid;
  gap: 0.75rem;
}

.account-guidance-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: start;
  gap: 0.6rem;
  color: var(--ds-text-secondary);
}

.account-guidance-item p {
  margin: 0;
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

@media (max-width: 700px) {
  .account-action-grid {
    grid-template-columns: 1fr;
  }
}
</style>
