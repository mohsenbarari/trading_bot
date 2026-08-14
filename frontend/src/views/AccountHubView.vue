<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronLeft, Database, Settings, Smartphone, UserRound } from 'lucide-vue-next'
import {
  AppActionCard,
  AppButton,
  AppErrorState,
  AppIconButton,
  AppLoadingState,
  AppPage,
  AppSectionCard,
  AppStatusBadge,
} from '../components/ui'
import { WorkspaceNotice } from '../components/workspace'
import {
  canEditOfferOvertimePreference,
  currentUserSummary,
  isAuthoritativeCurrentUserSummary,
  loadCurrentUserSummary,
} from '../utils/currentUser'
import {
  openTelegramAccountLink,
  requestTelegramLink,
  TELEGRAM_LINK_REQUEST_FAILED_MESSAGE,
  TELEGRAM_LINK_UNAVAILABLE_MESSAGE,
} from '../services/telegramLink'
import TelegramConnectPanel from '../components/account/TelegramConnectPanel.vue'

const router = useRouter()
const telegramLinkBusy = ref(false)
const telegramLinkError = ref<string | null>(null)
const cachedAccountIdentity = currentUserSummary.value
const hasCachedAccountIdentity = Boolean(
  isAuthoritativeCurrentUserSummary(cachedAccountIdentity) &&
    (cachedAccountIdentity.customer_management_name?.trim() ||
      cachedAccountIdentity.full_name?.trim() ||
      cachedAccountIdentity.account_name?.trim()),
)
const identityState = ref<'loading' | 'ready' | 'stale' | 'error'>(
  hasCachedAccountIdentity ? 'stale' : 'loading',
)
const identityBusy = ref(false)

interface AccountAction {
  key: string
  title: string
  description: string
  icon: Component
  action: () => void
}

const user = computed(() => currentUserSummary.value)
const displayName = computed(
  () =>
    user.value?.customer_management_name?.trim() ||
    user.value?.full_name?.trim() ||
    user.value?.account_name?.trim() ||
    '',
)
const hasIdentity = computed(
  () => isAuthoritativeCurrentUserSummary(user.value) && Boolean(displayName.value),
)
const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)
const isInactiveAccount = computed(() => user.value?.account_status === 'inactive')
const accountRestriction = computed<{ label: string; tone: 'danger' | 'warning' } | null>(() => {
  if (isInactiveAccount.value) return { label: 'حساب غیرفعال', tone: 'danger' }
  if (user.value?.global_web_locked_at) return { label: 'دسترسی محدود', tone: 'warning' }
  return null
})
const telegramConnected = computed(() => currentUserSummary.value?.telegram_linked === true)
const showTelegramConnectPanel = computed(
  () =>
    !isAccountant.value &&
    (currentUserSummary.value?.can_connect_telegram === true || telegramConnected.value),
)

const profileActions = computed<AccountAction[]>(() => {
  if (!hasIdentity.value) return []
  const actions: AccountAction[] = [
    {
      key: 'profile',
      title: 'پروفایل من',
      description: 'مشاهده و ویرایش اطلاعات حساب',
      icon: UserRound,
      action: () => router.push({ name: 'profile' }),
    },
  ]
  if (canEditOfferOvertimePreference(user.value)) {
    actions.push({
      key: 'settings',
      title: 'تنظیمات کاربری',
      description: 'مدیریت وقت اضافه پیشنهادهای تازه',
      icon: Settings,
      action: () => router.push({ name: 'settings' }),
    })
  }
  return actions
})

const securityActions = computed<AccountAction[]>(() => {
  if (!hasIdentity.value) return []
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

const securitySectionDescription = computed(() =>
  isAccountant.value
    ? 'حافظه دستگاه و داده‌های محلی را از مسیر مشخص خود مدیریت کنید.'
    : 'نشست‌ها، حافظه دستگاه و داده‌های محلی را از مسیر مشخص خود مدیریت کنید.',
)

const notificationActions = computed<AccountAction[]>(() =>
  hasIdentity.value
    ? [
        {
          key: 'notifications',
          title: 'اعلان‌ها',
          description: 'اعلان‌های سیستمی، بازار و معاملات',
          icon: Bell,
          action: () => router.push({ name: 'account-notifications' }),
        },
      ]
    : [],
)

async function connectTelegram() {
  if (telegramLinkBusy.value || telegramConnected.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = null
  try {
    const payload = await requestTelegramLink()
    if (openTelegramAccountLink(payload)) return
    telegramLinkError.value = TELEGRAM_LINK_UNAVAILABLE_MESSAGE
  } catch {
    telegramLinkError.value = TELEGRAM_LINK_REQUEST_FAILED_MESSAGE
  } finally {
    telegramLinkBusy.value = false
  }
}

async function refreshIdentity() {
  if (identityBusy.value) return
  identityBusy.value = true
  if (!hasIdentity.value) identityState.value = 'loading'
  try {
    const result = await loadCurrentUserSummary({ force: true })
    if (
      !isAuthoritativeCurrentUserSummary(result.user) ||
      !(
        result.user.customer_management_name?.trim() ||
        result.user.full_name?.trim() ||
        result.user.account_name?.trim()
      )
    ) {
      identityState.value = 'error'
      return
    }
    identityState.value = result.state === 'stale' ? 'stale' : 'ready'
  } catch {
    identityState.value = hasIdentity.value ? 'stale' : 'error'
  } finally {
    identityBusy.value = false
  }
}

onMounted(refreshIdentity)
</script>

<template>
  <div class="ds-page account-hub-page ui-v2-daily-page ui-v2-account-page">
    <AppPage>
      <h1 class="sr-only account-page-title">حساب</h1>
      <AppLoadingState
        v-if="identityState === 'loading' && !hasIdentity"
        class="account-identity-loading"
        label="در حال دریافت اطلاعات حساب"
      />
      <AppErrorState
        v-else-if="identityState === 'error' && !hasIdentity"
        class="account-identity-error"
        title="حساب بارگذاری نشد"
        message="وضعیت و دسترسی‌های حساب تا پاسخ معتبر دریافت نشود نمایش داده نمی‌شود."
      >
        <template #actions>
          <AppButton
            type="button"
            class="account-identity-retry"
            :loading="identityBusy"
            @click="refreshIdentity"
            >تلاش دوباره</AppButton
          >
        </template>
      </AppErrorState>
      <template v-else-if="hasIdentity && user">
        <WorkspaceNotice
          v-if="identityState === 'stale'"
          class="account-identity-stale"
          tone="warning"
          title="اطلاعات حساب به‌روز نشد"
          message="نسخه ذخیره‌شده قبلی نمایش داده شده است."
        >
          <AppButton
            type="button"
            size="sm"
            variant="secondary"
            :loading="identityBusy"
            @click="refreshIdentity"
            >به‌روزرسانی</AppButton
          >
        </WorkspaceNotice>
        <header class="account-compact-header ui-v2-account-header" aria-label="حساب کاربری">
          <AppIconButton
            type="button"
            class="account-return-control"
            label="بازگشت"
            size="sm"
            @click="router.back()"
          >
            <ChevronLeft :size="18" />
          </AppIconButton>
          <div class="account-identity ui-v2-account-header__identity">
            <div class="account-identity-name-row">
              <span
                v-if="accountRestriction"
                class="account-status-dot"
                :class="`account-status-dot--${accountRestriction.tone}`"
                aria-hidden="true"
              ></span>
              <strong>{{ displayName }}</strong>
            </div>
            <AppStatusBadge
              v-if="accountRestriction"
              class="account-status-badge"
              :tone="accountRestriction.tone"
            >
              {{ accountRestriction.label }}
            </AppStatusBadge>
          </div>
          <div class="account-header-spacer" aria-hidden="true"></div>
        </header>

        <AppSectionCard class="account-section-card" title="پروفایل" tone="primary">
          <div
            class="account-action-grid"
            :class="{ 'account-action-grid--single': profileActions.length === 1 }"
          >
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
          :description="securitySectionDescription"
        >
          <div
            class="account-action-grid"
            :class="{ 'account-action-grid--single': securityActions.length === 1 }"
          >
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
          <div
            class="account-action-grid"
            :class="{ 'account-action-grid--single': notificationActions.length === 1 }"
          >
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
      </template>
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

.account-status-dot--warning {
  background: var(--ds-warning-600);
  box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.14);
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

.account-action-grid--single {
  grid-template-columns: minmax(0, 1fr);
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
