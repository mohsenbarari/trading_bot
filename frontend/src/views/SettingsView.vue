<script setup lang="ts">
import { computed, onMounted, watch, ref } from 'vue'
import {
  Smartphone,
  Trash2,
  LogOut,
  ChevronLeft,
} from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { forceLogout } from '../utils/auth'
import { openTelegramLink, requestTelegramLink } from '../services/telegramLink'
import TelegramConnectPanel from '../components/account/TelegramConnectPanel.vue'
import { useChatFileHandler } from '../composables/chat/useChatFileHandler'
import { currentUserSummary, loadCurrentUserSummary } from '../utils/currentUser'
import { routeRequestJson } from '../utils/routeRequest'
import {
  AppButton,
  AppCard,
  AppEmptyState,
  AppErrorState,
  AppLoadingState,
  AppPage,
  AppPageHeader,
  AppSectionCard,
  AppStatusBadge,
} from '../components/ui'
import { WorkspaceNotice } from '../components/workspace'

const router = useRouter()
const route = useRoute()
const { getCacheSize, clearFileCache } = useChatFileHandler()

const cacheSize = ref('نامشخص')
const cacheBusy = ref(false)
const cacheFeedback = ref<string | null>(null)
const cacheSizeError = ref<string | null>(null)
const sessions = ref<any[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref<string | null>(null)
const sessionActionError = ref<string | null>(null)
const sessionActionReceipt = ref<string | null>(null)
const sessionBusyIds = ref<string[]>([])
const logoutAllBusy = ref(false)
const telegramLinkBusy = ref(false)
const telegramLinkError = ref<string | null>(null)
const identityState = ref<'loading' | 'ready' | 'stale' | 'error'>('loading')
const identityBusy = ref(false)

const hasIdentity = computed(() => currentUserSummary.value !== null)
const isAccountant = computed(() => hasIdentity.value && currentUserSummary.value?.is_accountant === true)
const canManageSessions = computed(() => hasIdentity.value && !isAccountant.value)
const telegramConnected = computed(() => currentUserSummary.value?.telegram_linked === true)
const showTelegramConnectSection = computed(() => (
  !isAccountant.value
  && (
    currentUserSummary.value?.can_connect_telegram === true
    || telegramConnected.value
  )
))
const routeSection = computed<'sessions' | 'storage' | null>(() => {
  if (route.name === 'account-storage') return 'storage'
  if (route.name === 'account-security') return 'sessions'
  const section = route.query.section
  return section === 'sessions' || section === 'storage' ? section : null
})

const pageTitle = computed(() => {
  if (routeSection.value === 'sessions') return 'امنیت حساب'
  if (routeSection.value === 'storage') return 'حافظه و داده‌ها'
  return 'تنظیمات حساب'
})

const settingsDescription = computed(() => {
  if (routeSection.value === 'sessions') return 'نشست‌های فعال و دسترسی‌های ورود از این بخش مدیریت می‌شوند.'
  if (routeSection.value === 'storage') return 'فایل‌های محلی و داده‌های دانلود شده از این بخش مدیریت می‌شوند.'
  return 'امنیت حساب، حافظه دستگاه و خروج از حساب را از یک مرکز تنظیمات روشن مدیریت کنید.'
})

async function refreshCacheSize() {
  cacheSizeError.value = null
  try {
    cacheSize.value = await getCacheSize()
  } catch {
    cacheSize.value = 'نامشخص'
    cacheSizeError.value = 'محاسبه فضای اشغال‌شده ممکن نشد.'
  }
}

async function clearCache() {
  if (cacheBusy.value) return
  cacheBusy.value = true
  cacheFeedback.value = null
  try {
    await clearFileCache()
    cacheSize.value = '0.00 MB'
    cacheFeedback.value = 'حافظه با موفقیت پاک شد.'
  } catch (err) {
    console.error(err)
    cacheFeedback.value = 'پاک‌سازی حافظه ناموفق بود.'
  } finally {
    cacheBusy.value = false
    setTimeout(() => { cacheFeedback.value = null }, 3500)
  }
}

async function fetchSessions() {
  if (isAccountant.value) {
    sessions.value = []
    sessionsError.value = null
    return
  }
  if (sessionsLoading.value) return
  sessionsLoading.value = true
  sessionsError.value = null
  try {
    const payload = await routeRequestJson<unknown>('/api/sessions/active', {
      errorContext: {
        surface: 'settings',
        scope: 'list',
        operation: sessions.value.length > 0 ? 'background-refresh' : 'load-list',
        preserveExistingData: sessions.value.length > 0,
        fallbackMessage: 'دریافت نشست‌های فعال ناموفق بود.',
      },
    })
    if (!Array.isArray(payload)) {
      throw new Error('invalid_sessions_payload')
    }
    sessions.value = payload
  } catch (e) {
    console.error(e)
    sessionsError.value = 'دریافت نشست‌های فعال ناموفق بود.'
  } finally {
    sessionsLoading.value = false
  }
}

async function terminateSession(sessionId: string) {
  if (sessionBusyIds.value.includes(sessionId) || logoutAllBusy.value) return
  sessionBusyIds.value = [...sessionBusyIds.value, sessionId]
  sessionActionError.value = null
  sessionActionReceipt.value = null
  try {
    const receipt = await routeRequestJson<{ detail?: unknown }>(`/api/sessions/${sessionId}`, {
      method: 'DELETE',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'پایان دادن نشست ممکن نشد.',
      },
    })
    const detail = typeof receipt?.detail === 'string' ? receipt.detail.trim() : ''
    if (!detail) {
      throw new Error('invalid_terminate_session_receipt')
    }
    sessions.value = sessions.value.filter((session) => session.id !== sessionId)
    sessionActionReceipt.value = detail
  } catch {
    sessionActionError.value = 'پایان دادن نشست ممکن نشد. فهرست فعلی تغییر نکرد.'
  } finally {
    sessionBusyIds.value = sessionBusyIds.value.filter((id) => id !== sessionId)
  }
}

async function logoutAll() {
  if (logoutAllBusy.value || sessionBusyIds.value.length > 0) return
  logoutAllBusy.value = true
  sessionActionError.value = null
  sessionActionReceipt.value = null
  try {
    const receipt = await routeRequestJson<{ detail?: unknown }>('/api/sessions/logout-all', {
      method: 'POST',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'خروج از همه نشست‌ها ممکن نشد.',
      },
    })
    const detail = typeof receipt?.detail === 'string' ? receipt.detail.trim() : ''
    if (!detail) {
      throw new Error('invalid_logout_all_receipt')
    }
    sessionActionReceipt.value = detail
    await fetchSessions()
  } catch {
    sessionActionError.value = 'خروج از همه نشست‌ها ممکن نشد. فهرست فعلی حفظ شده است.'
  } finally {
    logoutAllBusy.value = false
  }
}

function isSessionBusy(sessionId: string) {
  return sessionBusyIds.value.includes(sessionId)
}

async function loadIdentityAndSessions() {
  if (identityBusy.value) return
  identityBusy.value = true
  if (!currentUserSummary.value) identityState.value = 'loading'
  try {
    const result = await loadCurrentUserSummary({ force: true })
    if (!result.user) {
      identityState.value = 'error'
      return
    }
    identityState.value = result.state === 'stale' ? 'stale' : 'ready'
    if (result.user.is_accountant !== true) {
      await fetchSessions()
    }
  } catch {
    identityState.value = currentUserSummary.value ? 'stale' : 'error'
  } finally {
    identityBusy.value = false
  }
}

async function logout() {
  const currentSession = sessions.value.find((session) => session.is_current)
  if (currentSession) {
    try {
      await routeRequestJson<{ detail?: unknown }>(`/api/sessions/${currentSession.id}`, {
        method: 'DELETE',
        errorContext: {
          surface: 'settings',
          scope: 'action',
          operation: 'delete',
          userInitiated: true,
          fallbackMessage: 'پایان نشست فعلی ممکن نشد.',
        },
      })
    } catch (e) {
      console.error(e)
    }
  }
  forceLogout()
}

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
  void loadIdentityAndSessions()
  void refreshCacheSize()
})

watch(
  () => [route.name, route.query.section],
  () => {
    if (canManageSessions.value && routeSection.value === 'sessions' && sessions.value.length === 0 && !sessionsLoading.value) {
      void fetchSessions()
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="ds-page settings-page">
    <AppPage narrow>
      <AppPageHeader eyebrow="حساب" :title="pageTitle" :description="settingsDescription">
        <template #actions>
          <AppButton class="settings-return-control" variant="ghost" size="sm" @click="router.back()">
            <template #icon>
              <ChevronLeft :size="18" />
            </template>
            بازگشت
          </AppButton>
        </template>
      </AppPageHeader>

      <AppLoadingState
        v-if="identityState === 'loading' && !hasIdentity"
        class="settings-identity-loading"
        label="در حال بررسی دسترسی‌های حساب"
      />
      <AppErrorState
        v-else-if="identityState === 'error' && !hasIdentity"
        class="settings-identity-error"
        title="دسترسی‌های حساب مشخص نشد"
        message="تا دریافت پاسخ معتبر، نشست‌ها و اقدام‌های حساب نمایش داده نمی‌شوند."
      >
        <template #actions>
          <AppButton type="button" class="settings-identity-retry" :loading="identityBusy" @click="loadIdentityAndSessions">تلاش دوباره</AppButton>
        </template>
      </AppErrorState>
      <WorkspaceNotice
        v-if="identityState === 'stale' && hasIdentity"
        class="settings-identity-stale"
        tone="warning"
        title="اطلاعات حساب به‌روز نشد"
        message="دسترسی‌های ذخیره‌شده قبلی حفظ شده‌اند."
      >
        <AppButton type="button" size="sm" variant="secondary" :loading="identityBusy" @click="loadIdentityAndSessions">به‌روزرسانی</AppButton>
      </WorkspaceNotice>

      <WorkspaceNotice
        v-if="hasIdentity && isAccountant"
        class="settings-role-notice"
        tone="warning"
        title="نشست و خروج برای حسابدار محدود است"
        message="نشست‌های حسابدار و خروج از حساب توسط سرگروه مدیریت می‌شود. در این صفحه فقط حافظه و داده‌های دستگاه در دسترس است."
      />

      <AppSectionCard
        v-if="showTelegramConnectSection"
        class="settings-section-card"
        title="اتصال تلگرام"
        description="دسترسی سریع به امکانات اپ در بستر تلگرام"
        tone="primary"
      >
        <TelegramConnectPanel
          :connected="telegramConnected"
          :loading="telegramLinkBusy"
          :error="telegramLinkError"
          @connect="connectTelegram"
        />
      </AppSectionCard>

      <AppSectionCard
        v-if="canManageSessions"
        class="settings-section-card"
        title="نشست‌های فعال"
        description="دستگاه‌های فعال، نشست جاری و پایان دادن به نشست‌های دیگر را از این بخش مدیریت کنید."
        :tone="routeSection === 'sessions' ? 'primary' : 'neutral'"
      >
        <WorkspaceNotice
          v-if="sessionActionError"
          class="session-action-feedback"
          tone="danger"
          role="alert"
          title="اقدام انجام نشد"
          :message="sessionActionError"
        />
        <WorkspaceNotice
          v-else-if="sessionActionReceipt"
          class="session-action-feedback"
          tone="success"
          title="اقدام انجام شد"
          :message="sessionActionReceipt"
        />

        <AppLoadingState v-if="sessionsLoading && sessions.length === 0" label="در حال دریافت نشست‌ها" />

        <AppErrorState
          v-if="sessionsError && sessions.length === 0"
          class="sessions-load-error"
          title="خطا در دریافت نشست‌ها"
          :message="sessionsError"
        >
          <template #actions>
            <AppButton type="button" size="sm" variant="secondary" class="sessions-retry" :loading="sessionsLoading" @click="fetchSessions">
              تلاش دوباره
            </AppButton>
          </template>
        </AppErrorState>
        <WorkspaceNotice
          v-else-if="sessionsError"
          class="sessions-refresh-error"
          tone="warning"
          role="alert"
          title="به‌روزرسانی نشست‌ها انجام نشد"
          message="فهرست قبلی حفظ شده است."
        >
          <AppButton type="button" size="sm" variant="secondary" class="sessions-retry" :loading="sessionsLoading" @click="fetchSessions">
            تلاش دوباره
          </AppButton>
        </WorkspaceNotice>

        <AppEmptyState
          v-if="!sessionsLoading && !sessionsError && sessions.length === 0"
          title="نشست فعالی یافت نشد"
          message="در حال حاضر دستگاه دیگری برای مدیریت نمایش داده نمی‌شود."
          tone="info"
        />

        <div v-if="sessions.length > 0" class="sessions-list">
          <AppCard v-for="session in sessions" :key="session.id" class="session-card">
            <div class="session-card__main">
              <div class="session-card__identity">
                <div class="session-icon" :class="{ 'session-icon-primary': session.is_primary }">
                  <Smartphone :size="18" />
                </div>
                <div class="session-details">
                  <div class="session-name-row">
                    <strong class="session-name">{{ session.device_name }}</strong>
                    <AppStatusBadge v-if="session.is_primary" tone="primary">اصلی</AppStatusBadge>
                    <AppStatusBadge v-if="session.is_current" tone="success">این دستگاه</AppStatusBadge>
                  </div>
                  <div class="session-meta">
                    {{ session.platform }} · {{ session.device_ip || '—' }}
                  </div>
                </div>
              </div>

              <AppButton
                v-if="!session.is_current && !session.is_primary && sessions.some(s => s.is_current && s.is_primary)"
                class="session-delete-btn"
                variant="ghost"
                size="sm"
                :loading="isSessionBusy(session.id)"
                :disabled="logoutAllBusy"
                @click="terminateSession(session.id)"
              >
                <template #icon>
                  <Trash2 :size="16" />
                </template>
                پایان نشست
              </AppButton>
            </div>
          </AppCard>

          <div class="settings-inline-actions">
            <AppButton
              v-if="sessions.length > 1 && sessions.some(s => s.is_current && s.is_primary)"
              class="logout-all-btn"
              type="button"
              variant="danger"
              block
              :loading="logoutAllBusy"
              :disabled="sessionBusyIds.length > 0"
              @click="logoutAll"
            >
              خروج از همه نشست‌ها
            </AppButton>
          </div>
        </div>
      </AppSectionCard>

      <AppSectionCard
        class="settings-section-card"
        title="حافظه و داده‌ها"
        description="فایل‌های دانلود شده و داده‌های محلی دستگاه را بدون خروج از حساب مدیریت کنید."
        :tone="routeSection === 'storage' ? 'primary' : 'neutral'"
      >
        <AppCard class="storage-card">
          <div class="storage-info">
            <div>
              <span class="storage-label">فضای اشغال‌شده توسط فایل‌های دانلود شده</span>
              <p class="storage-copy">فایل‌های پیام‌رسان و داده‌های محلی قابل حذف از این بخش هستند.</p>
            </div>
            <strong class="storage-value" dir="ltr">{{ cacheSize }}</strong>
          </div>

          <WorkspaceNotice
            v-if="cacheSizeError"
            class="storage-size-error"
            tone="danger"
            role="alert"
            title="حجم حافظه نامشخص است"
            :message="cacheSizeError"
          />

          <AppButton
            type="button"
            class="storage-clear-btn"
            variant="danger"
            block
            :disabled="cacheBusy"
            :loading="cacheBusy"
            @click="clearCache"
          >
            <template #icon>
              <Trash2 :size="16" />
            </template>
            حذف فایل‌های دانلود شده
          </AppButton>

          <p v-if="cacheFeedback" class="storage-feedback">{{ cacheFeedback }}</p>
        </AppCard>
      </AppSectionCard>

      <AppSectionCard
        v-if="canManageSessions"
        class="settings-section-card"
        title="خروج از حساب"
        description="نشست فعلی را ببندید و از حساب کاربری خارج شوید."
        tone="danger"
      >
        <WorkspaceNotice
          tone="warning"
          title="خروج روی همین دستگاه اعمال می‌شود"
          message="برای بستن همه دستگاه‌ها از بخش نشست‌های فعال استفاده کنید."
        />
        <div class="settings-inline-actions">
          <AppButton variant="danger" block class="logout-btn" @click="logout">
            <template #icon>
              <LogOut :size="16" />
            </template>
            خروج از حساب کاربری
          </AppButton>
        </div>
      </AppSectionCard>
    </AppPage>
  </div>
</template>

<style scoped>
.settings-page {
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.settings-return-control {
  white-space: nowrap;
}

.settings-role-notice,
.settings-section-card + .settings-section-card {
  margin-top: 0.75rem;
}

.sessions-list,
.settings-inline-actions {
  display: grid;
  gap: 0.75rem;
}

.session-card__main,
.session-card__identity,
.storage-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.session-card__identity,
.storage-info {
  min-width: 0;
}

.session-icon {
  width: 38px;
  height: 38px;
  border-radius: var(--ds-radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--ds-bg-hover);
  color: var(--ds-text-muted);
}

.session-icon-primary {
  background: var(--ds-primary-100);
  color: var(--ds-primary-600);
}

.session-details {
  min-width: 0;
}

.session-name-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem;
}

.session-name {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.session-meta,
.storage-copy,
.storage-label,
.storage-feedback {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.storage-copy,
.storage-feedback {
  margin: 0.25rem 0 0;
}

.storage-label {
  display: block;
}

.storage-value {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-lg);
  font-weight: 900;
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .session-card__main,
  .storage-info {
    flex-direction: column;
    align-items: stretch;
  }

  .session-card__identity {
    align-items: flex-start;
  }

  .storage-value {
    text-align: right;
  }
}
</style>
