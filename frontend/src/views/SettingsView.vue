<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ChevronLeft, LogOut, Smartphone, Trash2 } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import {
  clearStorageFileCache,
  getStorageCacheSize,
  reloadAfterStorageCacheClear,
} from '../composables/useStorageCacheMetrics'
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
import { forceLogout } from '../utils/auth'
import {
  currentUserSummary,
  isAuthoritativeCurrentUserSummary,
  loadCurrentUserSummary,
} from '../utils/currentUser'
import { formatIranDateTime } from '../utils/iranTime'
import { storeLocalLogoutReceipt, type LocalLogoutOutcome } from '../utils/localLogoutReceipt'
import { routeRequestJson } from '../utils/routeRequest'

interface AccountSession {
  id: string
  deviceName: string
  platform: string
  isPrimary: boolean
  isCurrent: boolean
  lastActiveAt: string | null
}

interface ActionFeedback {
  tone: 'success' | 'warning' | 'danger'
  title: string
  message: string
}

interface SessionActionFeedback extends ActionFeedback {
  sessionId: string
}

const router = useRouter()
const route = useRoute()
const settingsPageRoot = ref<HTMLElement | null>(null)
const STORAGE_CLEAR_RECEIPT_KEY = 'stage4_storage_cache_cleared'

const identityState = ref<'loading' | 'ready' | 'stale' | 'error'>(
  isAuthoritativeCurrentUserSummary(currentUserSummary.value) ? 'stale' : 'loading',
)
const identityBusy = ref(false)

const sessions = ref<AccountSession[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref<string | null>(null)
const sessionBusyIds = ref<string[]>([])
const terminateConfirmationId = ref<string | null>(null)
const sessionActionFeedback = ref<SessionActionFeedback | null>(null)

const logoutOthersConfirming = ref(false)
const logoutOthersBusy = ref(false)
const logoutOthersFeedback = ref<ActionFeedback | null>(null)
const localLogoutConfirming = ref(false)
const localLogoutBusy = ref(false)
const localLogoutFeedback = ref<ActionFeedback | null>(null)

const cacheSize = ref<string | null>(null)
const cacheSizeLoading = ref(false)
const cacheSizeError = ref<string | null>(null)
const cacheClearConfirming = ref(false)
const cacheClearBusy = ref(false)
const cacheClearFeedback = ref<ActionFeedback | null>(consumeStorageClearReceipt())

function consumeStorageClearReceipt(): ActionFeedback | null {
  try {
    if (sessionStorage.getItem(STORAGE_CLEAR_RECEIPT_KEY) !== '1') return null
    sessionStorage.removeItem(STORAGE_CLEAR_RECEIPT_KEY)
    return {
      tone: 'success',
      title: 'فایل‌های محلی پاک شدند',
      message: 'فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف شدند.',
    }
  } catch {
    return null
  }
}

const hasIdentity = computed(() => isAuthoritativeCurrentUserSummary(currentUserSummary.value))
const isAccountant = computed(
  () => hasIdentity.value && currentUserSummary.value?.is_accountant === true,
)
const canManageSessions = computed(() => hasIdentity.value && !isAccountant.value)
const isSecurityRoute = computed(() => route.name === 'account-security')
const isStorageRoute = computed(() => !isSecurityRoute.value)

const pageTitle = computed(() => (isSecurityRoute.value ? 'امنیت حساب' : 'حافظه و داده‌ها'))
const pageDescription = computed(() =>
  isSecurityRoute.value
    ? 'نشست‌های گزارش‌شده توسط همین سرور و اختیار دستگاه فعلی را مدیریت کنید.'
    : 'فایل‌های محلی پیام‌رسان روی همین دستگاه را بررسی و پاک‌سازی کنید.',
)

const currentSession = computed(() => sessions.value.find((session) => session.isCurrent) ?? null)
const currentSessionIsPrimary = computed(() => currentSession.value?.isPrimary === true)
const otherSessions = computed(() => sessions.value.filter((session) => !session.isCurrent))
const canLogoutOtherSessions = computed(
  () => currentSessionIsPrimary.value && otherSessions.value.length > 0,
)

const sessionAuthority = computed<ActionFeedback>(() => {
  if (!currentSession.value) {
    return {
      tone: 'warning',
      title: 'اختیار این دستگاه مشخص نیست',
      message:
        'تا زمانی که سرور نشست فعلی را مشخص نکند، اقدام مدیریت نشست‌های دیگر نمایش داده نمی‌شود.',
    }
  }
  if (currentSessionIsPrimary.value) {
    return {
      tone: 'success',
      title: 'این دستگاه، نشست اصلی است',
      message: 'این دستگاه طبق پاسخ سرور اجازه پایان دادن به نشست‌های دیگر را دارد.',
    }
  }
  return {
    tone: 'warning',
    title: 'این دستگاه، نشست فرعی است',
    message: 'مدیریت نشست‌های دیگر فقط از دستگاهی که سرور آن را اصلی اعلام کرده در دسترس است.',
  }
})

const cacheSizeLabel = computed(() => {
  if (cacheSizeLoading.value && cacheSize.value === null) return 'در حال محاسبه'
  if (cacheSizeError.value) return 'نامشخص'
  return cacheSize.value ?? 'نامشخص'
})

function normalizeSession(raw: unknown): AccountSession | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  if (typeof value.id !== 'string' && typeof value.id !== 'number') return null
  return {
    id: String(value.id),
    deviceName:
      typeof value.device_name === 'string' && value.device_name.trim()
        ? value.device_name.trim()
        : 'دستگاه بدون نام',
    platform:
      typeof value.platform === 'string' && value.platform.trim()
        ? value.platform.trim()
        : 'نوع دستگاه ثبت نشده',
    isPrimary: value.is_primary === true,
    isCurrent: value.is_current === true,
    lastActiveAt:
      typeof value.last_active_at === 'string' && value.last_active_at.trim()
        ? value.last_active_at
        : null,
  }
}

function lastActivityLabel(session: AccountSession) {
  return formatIranDateTime(session.lastActiveAt) || 'زمان ثبت نشده'
}

function isSessionBusy(sessionId: string) {
  return sessionBusyIds.value.includes(sessionId)
}

function canTerminateSession(session: AccountSession) {
  return currentSessionIsPrimary.value && !session.isCurrent && !session.isPrimary
}

function returnToAccount() {
  void router.push({ name: 'account' })
}

function focusAfterRender(selector: string) {
  void nextTick(() => settingsPageRoot.value?.querySelector<HTMLElement>(selector)?.focus())
}

function focusSessionTrigger(sessionId: string) {
  void nextTick(() => {
    const trigger = Array.from(
      settingsPageRoot.value?.querySelectorAll<HTMLElement>('.session-delete-btn') ?? [],
    ).find((element) => element.dataset.sessionId === sessionId)
    trigger?.focus()
  })
}

async function refreshCacheSize() {
  if (!isStorageRoute.value || cacheSizeLoading.value) return
  cacheSizeLoading.value = true
  cacheSizeError.value = null
  try {
    cacheSize.value = await getStorageCacheSize()
  } catch {
    cacheSize.value = null
    cacheSizeError.value =
      'اندازه فایل‌های محلی این دستگاه محاسبه نشد. این وضعیت به معنی صفر بودن فضا نیست.'
  } finally {
    cacheSizeLoading.value = false
  }
}

function requestCacheClear() {
  if (cacheClearBusy.value) return
  cacheClearFeedback.value = null
  cacheClearConfirming.value = true
  focusAfterRender('.storage-clear-confirm')
}

function cancelCacheClear() {
  if (cacheClearBusy.value) return
  cacheClearConfirming.value = false
  focusAfterRender('.storage-clear-btn')
}

async function confirmCacheClear() {
  if (cacheClearBusy.value) return
  cacheClearBusy.value = true
  cacheClearFeedback.value = null
  try {
    await clearStorageFileCache()
    cacheSize.value = '0.00 MB'
    cacheSizeError.value = null
    cacheClearConfirming.value = false
    cacheClearFeedback.value = {
      tone: 'success',
      title: 'فایل‌های محلی پاک شدند',
      message: 'فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف شدند.',
    }
    try {
      sessionStorage.setItem(STORAGE_CLEAR_RECEIPT_KEY, '1')
      reloadAfterStorageCacheClear()
    } catch {
      // The persistent cache is already cleared and inline feedback remains.
    }
  } catch {
    cacheClearFeedback.value = {
      tone: 'danger',
      title: 'پاک‌سازی انجام نشد',
      message: 'فایل‌های محلی این دستگاه تغییر نکردند. می‌توانید دوباره تلاش کنید.',
    }
  } finally {
    cacheClearBusy.value = false
  }
}

async function fetchSessions() {
  if (!isSecurityRoute.value || !canManageSessions.value || sessionsLoading.value) return
  sessionsLoading.value = true
  sessionsError.value = null
  try {
    const payload = await routeRequestJson<unknown>('/api/sessions/active', {
      errorContext: {
        surface: 'settings',
        scope: 'list',
        operation: sessions.value.length > 0 ? 'background-refresh' : 'load-list',
        preserveExistingData: sessions.value.length > 0,
        fallbackMessage: 'دریافت نشست‌های این سرور انجام نشد.',
      },
    })
    if (!Array.isArray(payload)) throw new Error('invalid_sessions_payload')
    const normalized = payload.map(normalizeSession)
    if (normalized.some((session) => session === null)) throw new Error('invalid_session_record')
    sessions.value = normalized as AccountSession[]
  } catch {
    sessionsError.value = 'دریافت نشست‌های این سرور انجام نشد.'
  } finally {
    sessionsLoading.value = false
  }
}

function requestTerminateSession(session: AccountSession) {
  if (!canTerminateSession(session) || isSessionBusy(session.id) || logoutOthersBusy.value) return
  terminateConfirmationId.value = session.id
  sessionActionFeedback.value = null
  logoutOthersConfirming.value = false
  focusAfterRender('.session-terminate-confirm')
}

function cancelTerminateSession(sessionId: string) {
  if (isSessionBusy(sessionId)) return
  if (terminateConfirmationId.value === sessionId) {
    terminateConfirmationId.value = null
    focusSessionTrigger(sessionId)
  }
}

async function confirmTerminateSession(session: AccountSession) {
  if (
    terminateConfirmationId.value !== session.id ||
    isSessionBusy(session.id) ||
    logoutOthersBusy.value
  )
    return
  sessionBusyIds.value = [...sessionBusyIds.value, session.id]
  sessionActionFeedback.value = null
  try {
    const receipt = await routeRequestJson<{ detail?: unknown }>(`/api/sessions/${session.id}`, {
      method: 'DELETE',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'پایان دادن به این نشست انجام نشد.',
      },
    })
    const detail = typeof receipt?.detail === 'string' ? receipt.detail.trim() : ''
    if (!detail) throw new Error('invalid_terminate_session_receipt')
    sessions.value = sessions.value.filter((item) => item.id !== session.id)
    terminateConfirmationId.value = null
    sessionActionFeedback.value = {
      sessionId: session.id,
      tone: 'success',
      title: 'نشست پایان یافت',
      message: 'نشست انتخاب‌شده در همین سرور پایان یافت.',
    }
    focusAfterRender('.session-mutation-feedback')
  } catch {
    sessionActionFeedback.value = {
      sessionId: session.id,
      tone: 'danger',
      title: 'پایان نشست انجام نشد',
      message: 'این نشست در فهرست باقی ماند. می‌توانید دوباره تلاش کنید.',
    }
  } finally {
    sessionBusyIds.value = sessionBusyIds.value.filter((id) => id !== session.id)
  }
}

function requestLogoutOthers() {
  if (!canLogoutOtherSessions.value || logoutOthersBusy.value || sessionBusyIds.value.length > 0)
    return
  logoutOthersFeedback.value = null
  logoutOthersConfirming.value = true
  terminateConfirmationId.value = null
  focusAfterRender('.logout-others-confirm')
}

function cancelLogoutOthers() {
  if (logoutOthersBusy.value) return
  logoutOthersConfirming.value = false
  focusAfterRender('.logout-all-btn')
}

async function confirmLogoutOthers() {
  if (!logoutOthersConfirming.value || !canLogoutOtherSessions.value || logoutOthersBusy.value)
    return
  logoutOthersBusy.value = true
  logoutOthersFeedback.value = null
  try {
    const receipt = await routeRequestJson<{ detail?: unknown }>('/api/sessions/logout-all', {
      method: 'POST',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'خروج از نشست‌های دیگر انجام نشد.',
      },
    })
    const detail = typeof receipt?.detail === 'string' ? receipt.detail.trim() : ''
    if (!detail) throw new Error('invalid_logout_others_receipt')
    sessions.value = sessions.value.filter((session) => session.isCurrent)
    logoutOthersConfirming.value = false
    logoutOthersFeedback.value = {
      tone: 'success',
      title: 'نشست‌های دیگر پایان یافتند',
      message: 'نشست‌های دیگر این سرور پایان یافتند. نشست فعلی این دستگاه حفظ شد.',
    }
    await fetchSessions()
    focusAfterRender('.logout-others-feedback')
  } catch {
    logoutOthersFeedback.value = {
      tone: 'danger',
      title: 'خروج از نشست‌های دیگر انجام نشد',
      message: 'فهرست فعلی حفظ شده است. می‌توانید دوباره تلاش کنید.',
    }
  } finally {
    logoutOthersBusy.value = false
  }
}

function requestLocalLogout() {
  if (localLogoutBusy.value) return
  localLogoutFeedback.value = null
  localLogoutConfirming.value = true
  focusAfterRender('.local-logout-confirm')
}

function cancelLocalLogout() {
  if (localLogoutBusy.value) return
  localLogoutConfirming.value = false
  focusAfterRender('.logout-btn')
}

async function confirmLocalLogout() {
  if (!localLogoutConfirming.value || localLogoutBusy.value) return
  localLogoutBusy.value = true
  localLogoutFeedback.value = null
  let logoutOutcome: LocalLogoutOutcome = 'local-only'
  const session = currentSession.value
  if (session) {
    try {
      const receipt = await routeRequestJson<{ detail?: unknown }>(`/api/sessions/${session.id}`, {
        method: 'DELETE',
        errorContext: {
          surface: 'settings',
          scope: 'action',
          operation: 'delete',
          userInitiated: true,
          fallbackMessage: 'پایان نشست فعلی روی سرور تأیید نشد.',
        },
      })
      const detail = typeof receipt?.detail === 'string' ? receipt.detail.trim() : ''
      if (!detail) throw new Error('invalid_local_logout_receipt')
      logoutOutcome = 'server-confirmed'
      localLogoutFeedback.value = {
        tone: 'success',
        title: 'خروج این دستگاه ثبت شد',
        message: 'اطلاعات ورود این دستگاه در حال پاک‌شدن است.',
      }
    } catch {
      localLogoutFeedback.value = {
        tone: 'warning',
        title: 'تأیید سرور دریافت نشد',
        message: 'اطلاعات ورود این دستگاه به‌صورت محلی پاک می‌شود.',
      }
    }
  }
  localLogoutConfirming.value = false
  localLogoutBusy.value = false
  storeLocalLogoutReceipt(logoutOutcome)
  forceLogout()
}

async function loadIdentityAndSection() {
  if (identityBusy.value) return
  identityBusy.value = true
  if (!hasIdentity.value) identityState.value = 'loading'
  try {
    const result = await loadCurrentUserSummary({ force: true })
    if (!isAuthoritativeCurrentUserSummary(result.user)) {
      identityState.value = 'error'
      return
    }
    identityState.value = result.state === 'stale' ? 'stale' : 'ready'
    if (isSecurityRoute.value && result.user.is_accountant !== true) {
      await fetchSessions()
    } else if (isStorageRoute.value) {
      await refreshCacheSize()
    }
  } catch {
    identityState.value = hasIdentity.value ? 'stale' : 'error'
  } finally {
    identityBusy.value = false
  }
}

function resetSectionActions() {
  terminateConfirmationId.value = null
  sessionActionFeedback.value = null
  logoutOthersConfirming.value = false
  logoutOthersFeedback.value = null
  localLogoutConfirming.value = false
  localLogoutFeedback.value = null
  cacheClearConfirming.value = false
  cacheClearFeedback.value = null
}

onMounted(() => {
  void loadIdentityAndSection()
})

watch(
  () => route.name,
  () => {
    resetSectionActions()
    if (!hasIdentity.value) return
    if (isSecurityRoute.value && canManageSessions.value) {
      void fetchSessions()
    } else if (isStorageRoute.value) {
      void refreshCacheSize()
    }
  },
)
</script>

<template>
  <div ref="settingsPageRoot" class="ds-page settings-page ui-v2-daily-page ui-v2-settings-page">
    <AppPage narrow>
      <AppPageHeader eyebrow="حساب" :title="pageTitle" :description="pageDescription">
        <template #actions>
          <AppButton
            class="settings-return-control"
            variant="ghost"
            size="sm"
            @click="returnToAccount"
          >
            <template #icon>
              <ChevronLeft :size="18" />
            </template>
            حساب
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
        message="تا دریافت پاسخ معتبر، اقدام‌های این صفحه نمایش داده نمی‌شوند."
      >
        <template #actions>
          <AppButton
            type="button"
            class="settings-identity-retry"
            :loading="identityBusy"
            @click="loadIdentityAndSection"
          >
            تلاش دوباره
          </AppButton>
        </template>
      </AppErrorState>

      <template v-else-if="hasIdentity">
        <WorkspaceNotice
          v-if="identityState === 'stale'"
          class="settings-identity-stale"
          tone="warning"
          title="اطلاعات حساب به‌روز نشد"
          message="دسترسی‌های ذخیره‌شده قبلی حفظ شده‌اند."
        >
          <AppButton
            type="button"
            size="sm"
            variant="secondary"
            :loading="identityBusy"
            @click="loadIdentityAndSection"
          >
            به‌روزرسانی
          </AppButton>
        </WorkspaceNotice>

        <WorkspaceNotice
          v-if="isSecurityRoute && isAccountant"
          class="settings-role-notice"
          tone="warning"
          title="مدیریت نشست برای حسابدار در دسترس نیست"
          message="پروفایل، حافظه و اعلان‌ها از حساب در دسترس‌اند؛ نشست و خروج توسط سرگروه مدیریت می‌شود."
        />

        <template v-else-if="isSecurityRoute && canManageSessions">
          <AppSectionCard
            class="settings-section-card settings-security-card"
            title="نشست‌های این سرور"
            description="فقط نشست‌هایی نمایش داده می‌شوند که همین سرور گزارش کرده است؛ درباره سرور دیگر نتیجه‌ای فرض نمی‌شود."
            tone="primary"
          >
            <WorkspaceNotice
              class="session-authority-notice"
              :tone="sessionAuthority.tone"
              :title="sessionAuthority.title"
              :message="sessionAuthority.message"
            />

            <WorkspaceNotice
              v-if="
                sessionActionFeedback &&
                !sessions.some((session) => session.id === sessionActionFeedback?.sessionId)
              "
              class="session-mutation-feedback"
              tabindex="-1"
              :tone="sessionActionFeedback.tone"
              :title="sessionActionFeedback.title"
              :message="sessionActionFeedback.message"
            />

            <AppLoadingState
              v-if="sessionsLoading && sessions.length === 0"
              label="در حال دریافت نشست‌های این سرور"
            />

            <AppErrorState
              v-if="sessionsError && sessions.length === 0"
              class="sessions-load-error"
              title="نشست‌های این سرور دریافت نشد"
              :message="sessionsError"
            >
              <template #actions>
                <AppButton
                  type="button"
                  size="sm"
                  variant="secondary"
                  class="sessions-retry"
                  :loading="sessionsLoading"
                  @click="fetchSessions"
                >
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
              <AppButton
                type="button"
                size="sm"
                variant="secondary"
                class="sessions-retry"
                :loading="sessionsLoading"
                @click="fetchSessions"
              >
                تلاش دوباره
              </AppButton>
            </WorkspaceNotice>

            <AppEmptyState
              v-if="!sessionsLoading && !sessionsError && sessions.length === 0"
              title="نشستی در این سرور گزارش نشد"
              message="این نتیجه فقط به همین سرور مربوط است."
              tone="info"
            />

            <div v-if="sessions.length > 0" class="sessions-list">
              <AppCard v-for="session in sessions" :key="session.id" class="session-card">
                <div class="session-card__main">
                  <div class="session-card__identity">
                    <div
                      class="session-icon"
                      :class="{ 'session-icon-primary': session.isPrimary }"
                    >
                      <Smartphone :size="18" />
                    </div>
                    <div class="session-details">
                      <div class="session-name-row">
                        <strong class="session-name">{{ session.deviceName }}</strong>
                        <AppStatusBadge v-if="session.isPrimary" tone="primary"
                          >اصلی</AppStatusBadge
                        >
                        <AppStatusBadge v-if="session.isCurrent" tone="success"
                          >این دستگاه</AppStatusBadge
                        >
                      </div>
                      <div class="session-meta">
                        <span>{{ session.platform }}</span>
                        <span>آخرین فعالیت: {{ lastActivityLabel(session) }}</span>
                      </div>
                    </div>
                  </div>

                  <AppButton
                    v-if="canTerminateSession(session) && terminateConfirmationId !== session.id"
                    class="session-delete-btn"
                    :data-session-id="session.id"
                    variant="ghost"
                    size="sm"
                    :disabled="logoutOthersBusy"
                    @click="requestTerminateSession(session)"
                  >
                    <template #icon>
                      <Trash2 :size="16" />
                    </template>
                    پایان نشست
                  </AppButton>
                </div>

                <div
                  v-if="terminateConfirmationId === session.id"
                  class="session-inline-confirm"
                  role="group"
                  aria-label="تأیید پایان نشست"
                >
                  <p>نشست «{{ session.deviceName }}» در همین سرور پایان یابد؟</p>
                  <div class="settings-inline-actions settings-inline-actions--compact">
                    <AppButton
                      type="button"
                      size="sm"
                      variant="ghost"
                      :disabled="isSessionBusy(session.id)"
                      @click="cancelTerminateSession(session.id)"
                    >
                      انصراف
                    </AppButton>
                    <AppButton
                      type="button"
                      size="sm"
                      variant="danger"
                      class="session-terminate-confirm"
                      :loading="isSessionBusy(session.id)"
                      @click="confirmTerminateSession(session)"
                    >
                      تأیید پایان نشست
                    </AppButton>
                  </div>
                </div>

                <WorkspaceNotice
                  v-if="sessionActionFeedback?.sessionId === session.id"
                  class="session-mutation-feedback"
                  :tone="sessionActionFeedback.tone"
                  :title="sessionActionFeedback.title"
                  :message="sessionActionFeedback.message"
                />
              </AppCard>
            </div>
          </AppSectionCard>

          <AppSectionCard
            v-if="canLogoutOtherSessions || logoutOthersFeedback"
            class="settings-section-card settings-other-sessions-card"
            title="نشست‌های دیگر"
            description="این اقدام نشست‌های دیگر را می‌بندد و نشست فعلی این دستگاه را حفظ می‌کند."
          >
            <WorkspaceNotice
              v-if="logoutOthersFeedback"
              class="logout-others-feedback"
              tabindex="-1"
              :tone="logoutOthersFeedback.tone"
              :title="logoutOthersFeedback.title"
              :message="logoutOthersFeedback.message"
            />
            <AppButton
              v-if="canLogoutOtherSessions && !logoutOthersConfirming"
              class="logout-all-btn"
              type="button"
              variant="danger"
              block
              :disabled="sessionBusyIds.length > 0"
              @click="requestLogoutOthers"
            >
              خروج از نشست‌های دیگر
            </AppButton>
            <div
              v-if="logoutOthersConfirming"
              class="settings-inline-confirm"
              role="group"
              aria-label="تأیید خروج از نشست‌های دیگر"
            >
              <p>همه نشست‌های دیگر این سرور پایان می‌یابند؛ نشست فعلی این دستگاه باز می‌ماند.</p>
              <div class="settings-inline-actions settings-inline-actions--compact">
                <AppButton
                  type="button"
                  size="sm"
                  variant="ghost"
                  :disabled="logoutOthersBusy"
                  @click="cancelLogoutOthers"
                >
                  انصراف
                </AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="danger"
                  class="logout-others-confirm"
                  :loading="logoutOthersBusy"
                  @click="confirmLogoutOthers"
                >
                  تأیید خروج دیگران
                </AppButton>
              </div>
            </div>
          </AppSectionCard>

          <AppSectionCard
            class="settings-section-card settings-current-logout-card"
            title="خروج از این دستگاه"
            description="اطلاعات ورود فقط از همین دستگاه پاک می‌شود."
            tone="danger"
          >
            <WorkspaceNotice
              v-if="localLogoutFeedback"
              class="local-logout-feedback"
              :tone="localLogoutFeedback.tone"
              :title="localLogoutFeedback.title"
              :message="localLogoutFeedback.message"
            />
            <AppButton
              v-if="!localLogoutConfirming"
              variant="danger"
              block
              class="logout-btn"
              @click="requestLocalLogout"
            >
              <template #icon>
                <LogOut :size="16" />
              </template>
              خروج از این دستگاه
            </AppButton>
            <div
              v-else
              class="settings-inline-confirm"
              role="group"
              aria-label="تأیید خروج از این دستگاه"
            >
              <p>از حساب روی همین دستگاه خارج می‌شوید. نشست‌های دیگر تغییر نمی‌کنند.</p>
              <div class="settings-inline-actions settings-inline-actions--compact">
                <AppButton
                  type="button"
                  size="sm"
                  variant="ghost"
                  :disabled="localLogoutBusy"
                  @click="cancelLocalLogout"
                >
                  انصراف
                </AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="danger"
                  class="local-logout-confirm"
                  :loading="localLogoutBusy"
                  @click="confirmLocalLogout"
                >
                  تأیید خروج
                </AppButton>
              </div>
            </div>
          </AppSectionCard>
        </template>

        <AppSectionCard
          v-else-if="isStorageRoute"
          class="settings-section-card settings-storage-card"
          title="فایل‌های پیام‌رسان این دستگاه"
          description="فقط نسخه‌های محلی فایل‌های دریافت‌شده حذف می‌شوند؛ پیام‌ها، تنظیمات حساب و فایل‌های روی سرور تغییر نمی‌کنند."
          tone="primary"
        >
          <AppCard class="storage-card">
            <div class="storage-info">
              <div>
                <span class="storage-label">فضای فایل‌های محلی پیام‌رسان</span>
                <p class="storage-copy">این مقدار فقط به حافظه همین دستگاه مربوط است.</p>
              </div>
              <strong class="storage-value" dir="ltr">{{ cacheSizeLabel }}</strong>
            </div>

            <WorkspaceNotice
              v-if="cacheSizeError"
              class="storage-size-error"
              tone="warning"
              role="alert"
              title="اندازه حافظه مشخص نشد"
              :message="cacheSizeError"
            >
              <AppButton
                type="button"
                size="sm"
                variant="secondary"
                :loading="cacheSizeLoading"
                @click="refreshCacheSize"
              >
                محاسبه دوباره
              </AppButton>
            </WorkspaceNotice>

            <WorkspaceNotice
              v-if="cacheClearFeedback"
              class="storage-feedback"
              :tone="cacheClearFeedback.tone"
              :title="cacheClearFeedback.title"
              :message="cacheClearFeedback.message"
            />

            <AppButton
              v-if="!cacheClearConfirming"
              type="button"
              class="storage-clear-btn"
              variant="danger"
              block
              :disabled="cacheClearBusy"
              @click="requestCacheClear"
            >
              <template #icon>
                <Trash2 :size="16" />
              </template>
              پاک‌کردن فایل‌های محلی
            </AppButton>

            <div
              v-else
              class="settings-inline-confirm storage-inline-confirm"
              role="group"
              aria-label="تأیید پاک‌سازی فایل‌های محلی"
            >
              <p>فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف می‌شوند.</p>
              <div class="settings-inline-actions settings-inline-actions--compact">
                <AppButton
                  type="button"
                  size="sm"
                  variant="ghost"
                  :disabled="cacheClearBusy"
                  @click="cancelCacheClear"
                >
                  انصراف
                </AppButton>
                <AppButton
                  type="button"
                  size="sm"
                  variant="danger"
                  class="storage-clear-confirm"
                  :loading="cacheClearBusy"
                  @click="confirmCacheClear"
                >
                  تأیید پاک‌سازی
                </AppButton>
              </div>
            </div>
          </AppCard>
        </AppSectionCard>
      </template>
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
.settings-section-card + .settings-section-card,
.settings-identity-stale + .settings-section-card,
.session-authority-notice + .sessions-list,
.session-authority-notice + .sessions-load-error,
.session-authority-notice + .sessions-refresh-error,
.session-authority-notice + .ui-empty-state,
.session-mutation-feedback,
.logout-others-feedback,
.local-logout-feedback,
.storage-size-error,
.storage-feedback,
.storage-clear-btn,
.settings-inline-confirm {
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

.session-name-row,
.session-meta {
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
.settings-inline-confirm p {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.session-meta span + span::before {
  content: '·';
  margin-left: 0.45rem;
}

.settings-inline-confirm {
  padding: 0.75rem;
  border: 1px solid var(--ds-border-subtle);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-subtle);
}

.settings-inline-confirm p,
.storage-copy {
  margin: 0;
}

.settings-inline-actions--compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 0.65rem;
}

.storage-copy {
  margin-top: 0.25rem;
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
