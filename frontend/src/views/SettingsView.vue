<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ChevronLeft, LogOut, Smartphone, Trash2 } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import OfferOvertimePreferencePanel from '../components/OfferOvertimePreferencePanel.vue'
import {
  clearStorageFileCache,
  getStorageCacheSize,
  reloadAfterStorageCacheClear,
} from '../composables/useStorageCacheMetrics'
import {
  AppButton,
  AppCard,
  AppConfirmDialog,
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
  canEditOfferOvertimePreference,
  currentUserSummary,
  isAuthoritativeCurrentUserSummary,
  loadCurrentUserSummary,
} from '../utils/currentUser'
import { formatIranDateTime } from '../utils/iranTime'
import { storeLocalLogoutReceipt, type LocalLogoutOutcome } from '../utils/localLogoutReceipt'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { routeRequest, routeRequestJson } from '../utils/routeRequest'

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

type SecurityConfirmationKind = 'terminate-session' | 'logout-others' | 'local-logout'

const TERMINATE_SESSION_DETAIL = 'نشست با موفقیت پایان یافت'
const LOGOUT_OTHERS_DETAIL = /^\d+ نشست پایان یافت$/
const STORAGE_CLEAR_SAFE_COPY =
  'پاک‌سازی تأیید نشد. فایل‌های محلی این دستگاه تغییری نکرده است؛ می‌توانید دوباره تلاش کنید.'

const securitySafeCopy: Record<SecurityConfirmationKind, string> = {
  'terminate-session':
    'پایان نشست تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  'logout-others':
    'خروج از نشست‌های دیگر تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.',
  'local-logout':
    'تأیید سرور دریافت نشد. اطلاعات ورود این دستگاه به‌صورت محلی پاک می‌شود.',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isExactTerminateReceipt(receipt: unknown): boolean {
  return isRecord(receipt) && receipt.detail === TERMINATE_SESSION_DETAIL
}

function isExactLogoutOthersReceipt(receipt: unknown): boolean {
  return isRecord(receipt)
    && typeof receipt.detail === 'string'
    && LOGOUT_OTHERS_DETAIL.test(receipt.detail)
}

async function parseJsonReceipt(response: Response) {
  try {
    return await response.json()
  } catch {
    throw new Error('invalid_json_receipt')
  }
}

function getSafeSessionError(kind: SecurityConfirmationKind, error: unknown): string {
  const status = isAppHttpError(error) ? error.status : null
  if (status === 403) {
    return 'اجازه این اقدام را ندارید. اطلاعات نمایش‌داده‌شده تغییری نکرده است.'
  }
  if (status === 404) {
    return 'این نشست دیگر در دسترس نیست. اطلاعات نمایش‌داده‌شده تغییری نکرده است.'
  }
  return securitySafeCopy[kind]
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
const pendingSecurityConfirmation = ref<SecurityConfirmationKind | null>(null)
const securityConfirmationError = ref('')
const sessionActionFeedback = ref<SessionActionFeedback | null>(null)

const logoutOthersBusy = ref(false)
const logoutOthersFeedback = ref<ActionFeedback | null>(null)
const localLogoutBusy = ref(false)
const localLogoutFeedback = ref<ActionFeedback | null>(null)

const cacheSize = ref<string | null>(null)
const cacheSizeLoading = ref(false)
const cacheSizeError = ref<string | null>(null)
const cacheClearConfirming = ref(false)
const cacheClearBusy = ref(false)
const storageConfirmationError = ref('')
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
const isGeneralRoute = computed(() => route.name === 'settings')
const isSecurityRoute = computed(() => route.name === 'account-security')
const isStorageRoute = computed(() => route.name === 'account-storage')
const showOvertimePreference = computed(
  () => isGeneralRoute.value && canEditOfferOvertimePreference(currentUserSummary.value),
)

const pageTitle = computed(() => {
  if (isGeneralRoute.value) return 'تنظیمات حساب'
  if (isSecurityRoute.value) return 'امنیت حساب'
  return 'حافظه و داده‌ها'
})
const pageDescription = computed(() => {
  if (isGeneralRoute.value) return 'وقت اضافه پیشنهادهای تازه را از مسیر مشخص حساب مدیریت کنید.'
  if (isSecurityRoute.value) {
    return 'نشست‌های گزارش‌شده توسط همین سرور و اختیار دستگاه فعلی را مدیریت کنید.'
  }
  return 'فایل‌های محلی پیام‌رسان روی همین دستگاه را بررسی و پاک‌سازی کنید.'
})

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

const securityDialogOpen = computed(() => pendingSecurityConfirmation.value !== null)
const securityDialogBusy = computed(() => {
  if (pendingSecurityConfirmation.value === 'terminate-session' && terminateConfirmationId.value) {
    return isSessionBusy(terminateConfirmationId.value)
  }
  if (pendingSecurityConfirmation.value === 'logout-others') return logoutOthersBusy.value
  if (pendingSecurityConfirmation.value === 'local-logout') return localLogoutBusy.value
  return false
})
const securityDialogTitle = computed(() => {
  if (pendingSecurityConfirmation.value === 'terminate-session') return 'پایان این نشست'
  if (pendingSecurityConfirmation.value === 'logout-others') return 'خروج از نشست‌های دیگر'
  if (pendingSecurityConfirmation.value === 'local-logout') return 'خروج از این دستگاه'
  return ''
})
const securityDialogMessage = computed(() => {
  if (pendingSecurityConfirmation.value === 'terminate-session') {
    return 'این نشست در همین سرور پایان یابد؟ لغو یا Escape هیچ تغییری ایجاد نمی‌کند.'
  }
  if (pendingSecurityConfirmation.value === 'logout-others') {
    return 'همه نشست‌های دیگر این سرور پایان می‌یابند؛ نشست فعلی این دستگاه باز می‌ماند. لغو یا Escape هیچ تغییری ایجاد نمی‌کند.'
  }
  if (pendingSecurityConfirmation.value === 'local-logout') {
    return 'از حساب روی همین دستگاه خارج می‌شوید. نشست‌های دیگر تغییر نمی‌کنند. لغو یا Escape هیچ تغییری ایجاد نمی‌کند.'
  }
  return ''
})
const securityDialogConfirmLabel = computed(() => {
  if (pendingSecurityConfirmation.value === 'terminate-session') return 'تأیید پایان نشست'
  if (pendingSecurityConfirmation.value === 'logout-others') return 'تأیید خروج دیگران'
  if (pendingSecurityConfirmation.value === 'local-logout') return 'تأیید خروج'
  return 'تأیید'
})
const storageDialogOpen = computed(() => cacheClearConfirming.value && isStorageRoute.value)

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
  if (cacheClearBusy.value || cacheClearConfirming.value) return
  cacheClearFeedback.value = null
  storageConfirmationError.value = ''
  cacheClearConfirming.value = true
}

function cancelCacheClear() {
  if (cacheClearBusy.value) return
  cacheClearConfirming.value = false
  storageConfirmationError.value = ''
  focusAfterRender('.storage-clear-btn')
}

async function confirmCacheClear() {
  if (cacheClearBusy.value) return
  cacheClearBusy.value = true
  cacheClearFeedback.value = null
  storageConfirmationError.value = ''
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
    storageConfirmationError.value = STORAGE_CLEAR_SAFE_COPY
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

function closeSecurityConfirmation() {
  if (securityDialogBusy.value) return
  pendingSecurityConfirmation.value = null
  securityConfirmationError.value = ''
  terminateConfirmationId.value = null
}

function requestTerminateSession(session: AccountSession) {
  if (!canTerminateSession(session) || isSessionBusy(session.id) || logoutOthersBusy.value) return
  terminateConfirmationId.value = session.id
  pendingSecurityConfirmation.value = 'terminate-session'
  securityConfirmationError.value = ''
  sessionActionFeedback.value = null
  logoutOthersFeedback.value = null
}

async function confirmTerminateSession(session: AccountSession) {
  if (
    pendingSecurityConfirmation.value !== 'terminate-session'
    || terminateConfirmationId.value !== session.id
    || isSessionBusy(session.id)
    || logoutOthersBusy.value
  ) {
    return
  }
  sessionBusyIds.value = [...sessionBusyIds.value, session.id]
  sessionActionFeedback.value = null
  securityConfirmationError.value = ''
  try {
    const response = await routeRequest(`/api/sessions/${session.id}`, {
      method: 'DELETE',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'پایان دادن به این نشست انجام نشد.',
      },
    })
    if (response.status !== 200) throw new Error('invalid_terminate_session_receipt')
    const receipt = await parseJsonReceipt(response)
    if (!isExactTerminateReceipt(receipt)) throw new Error('invalid_terminate_session_receipt')
    sessions.value = sessions.value.filter((item) => item.id !== session.id)
    pendingSecurityConfirmation.value = null
    securityConfirmationError.value = ''
    terminateConfirmationId.value = null
    sessionActionFeedback.value = {
      sessionId: session.id,
      tone: 'success',
      title: 'نشست پایان یافت',
      message: 'نشست انتخاب‌شده در همین سرور پایان یافت.',
    }
    focusAfterRender('.session-mutation-feedback')
  } catch (error) {
    securityConfirmationError.value = getSafeSessionError('terminate-session', error)
  } finally {
    sessionBusyIds.value = sessionBusyIds.value.filter((id) => id !== session.id)
  }
}

function requestLogoutOthers() {
  if (!canLogoutOtherSessions.value || logoutOthersBusy.value || sessionBusyIds.value.length > 0)
    return
  logoutOthersFeedback.value = null
  securityConfirmationError.value = ''
  terminateConfirmationId.value = null
  pendingSecurityConfirmation.value = 'logout-others'
}

async function confirmLogoutOthers() {
  if (
    pendingSecurityConfirmation.value !== 'logout-others'
    || !canLogoutOtherSessions.value
    || logoutOthersBusy.value
  ) {
    return
  }
  logoutOthersBusy.value = true
  logoutOthersFeedback.value = null
  securityConfirmationError.value = ''
  try {
    const response = await routeRequest('/api/sessions/logout-all', {
      method: 'POST',
      errorContext: {
        surface: 'settings',
        scope: 'action',
        operation: 'delete',
        userInitiated: true,
        fallbackMessage: 'خروج از نشست‌های دیگر انجام نشد.',
      },
    })
    if (response.status !== 200) throw new Error('invalid_logout_others_receipt')
    const receipt = await parseJsonReceipt(response)
    if (!isExactLogoutOthersReceipt(receipt)) throw new Error('invalid_logout_others_receipt')
    sessions.value = sessions.value.filter((session) => session.isCurrent)
    pendingSecurityConfirmation.value = null
    securityConfirmationError.value = ''
    logoutOthersFeedback.value = {
      tone: 'success',
      title: 'نشست‌های دیگر پایان یافتند',
      message: 'نشست‌های دیگر این سرور پایان یافتند. نشست فعلی این دستگاه حفظ شد.',
    }
    await fetchSessions()
    focusAfterRender('.logout-others-feedback')
  } catch (error) {
    securityConfirmationError.value = getSafeSessionError('logout-others', error)
  } finally {
    logoutOthersBusy.value = false
  }
}

function requestLocalLogout() {
  if (localLogoutBusy.value) return
  localLogoutFeedback.value = null
  securityConfirmationError.value = ''
  pendingSecurityConfirmation.value = 'local-logout'
}

async function confirmLocalLogout() {
  if (pendingSecurityConfirmation.value !== 'local-logout' || localLogoutBusy.value) return
  localLogoutBusy.value = true
  localLogoutFeedback.value = null
  let logoutOutcome: LocalLogoutOutcome = 'local-only'
  const session = currentSession.value
  if (session) {
    try {
      const response = await routeRequest(`/api/sessions/${session.id}`, {
        method: 'DELETE',
        errorContext: {
          surface: 'settings',
          scope: 'action',
          operation: 'delete',
          userInitiated: true,
          fallbackMessage: 'پایان نشست فعلی روی سرور تأیید نشد.',
        },
      })
      if (response.status !== 200) throw new Error('invalid_local_logout_receipt')
      const receipt = await parseJsonReceipt(response)
      if (!isExactTerminateReceipt(receipt)) throw new Error('invalid_local_logout_receipt')
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
  pendingSecurityConfirmation.value = null
  securityConfirmationError.value = ''
  localLogoutBusy.value = false
  storeLocalLogoutReceipt(logoutOutcome)
  forceLogout()
}

async function confirmPendingSecurityAction() {
  if (pendingSecurityConfirmation.value === 'terminate-session') {
    const session = sessions.value.find((item) => item.id === terminateConfirmationId.value)
    if (!session) {
      closeSecurityConfirmation()
      return
    }
    await confirmTerminateSession(session)
    return
  }
  if (pendingSecurityConfirmation.value === 'logout-others') {
    await confirmLogoutOthers()
    return
  }
  if (pendingSecurityConfirmation.value === 'local-logout') {
    await confirmLocalLogout()
  }
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
  pendingSecurityConfirmation.value = null
  securityConfirmationError.value = ''
  terminateConfirmationId.value = null
  sessionActionFeedback.value = null
  logoutOthersFeedback.value = null
  localLogoutFeedback.value = null
  cacheClearConfirming.value = false
  storageConfirmationError.value = ''
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

        <AppSectionCard
          v-if="showOvertimePreference"
          class="settings-section-card settings-overtime-card"
          title="وقت اضافه پیشنهادها"
          description="مدت اعتبار افزوده برای پیشنهادهایی که از این پس ایجاد می‌کنید."
          tone="primary"
        >
          <OfferOvertimePreferencePanel class="settings-overtime-panel" />
        </AppSectionCard>

        <WorkspaceNotice
          v-else-if="isGeneralRoute"
          class="settings-role-notice"
          tone="info"
          title="تنظیمی برای این نوع حساب فعال نیست"
          message="امنیت، حافظه و اعلان‌ها از صفحه حساب و مسیرهای اختصاصی خود در دسترس‌اند."
        />

        <WorkspaceNotice
          v-else-if="isSecurityRoute && isAccountant"
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
              role="status"
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
                    v-if="canTerminateSession(session)"
                    class="session-delete-btn"
                    :data-session-id="session.id"
                    variant="ghost"
                    size="sm"
                    :disabled="logoutOthersBusy || securityDialogBusy"
                    @click="requestTerminateSession(session)"
                  >
                    <template #icon>
                      <Trash2 :size="16" />
                    </template>
                    پایان نشست
                  </AppButton>
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
              v-if="canLogoutOtherSessions"
              class="logout-all-btn"
              type="button"
              variant="danger"
              block
              :disabled="sessionBusyIds.length > 0 || securityDialogBusy"
              @click="requestLogoutOthers"
            >
              خروج از نشست‌های دیگر
            </AppButton>
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
              variant="danger"
              block
              class="logout-btn"
              :disabled="localLogoutBusy || securityDialogBusy"
              @click="requestLocalLogout"
            >
              <template #icon>
                <LogOut :size="16" />
              </template>
              خروج از این دستگاه
            </AppButton>
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
              type="button"
              class="storage-clear-btn"
              variant="danger"
              block
              :disabled="cacheClearBusy || cacheClearConfirming"
              @click="requestCacheClear"
            >
              <template #icon>
                <Trash2 :size="16" />
              </template>
              پاک‌کردن فایل‌های محلی
            </AppButton>
          </AppCard>
        </AppSectionCard>
      </template>
    </AppPage>
    <AppConfirmDialog
      v-if="securityDialogOpen"
      :open="securityDialogOpen"
      :title="securityDialogTitle"
      :message="securityDialogMessage"
      :confirm-label="securityDialogConfirmLabel"
      cancel-label="انصراف"
      tone="danger"
      :busy="securityDialogBusy"
      :error="securityConfirmationError || undefined"
      :confirm-disabled="securityDialogBusy"
      @cancel="closeSecurityConfirmation"
      @confirm="confirmPendingSecurityAction"
    />
    <AppConfirmDialog
      v-if="storageDialogOpen"
      :open="storageDialogOpen"
      title="پاک‌کردن فایل‌های محلی"
      message="فقط فایل‌های ذخیره‌شده پیام‌رسان روی همین دستگاه حذف می‌شوند. پیام‌ها، تنظیمات حساب و فایل‌های روی سرور تغییر نمی‌کنند. لغو یا Escape هیچ تغییری ایجاد نمی‌کند."
      confirm-label="تأیید پاک‌سازی"
      cancel-label="انصراف"
      tone="danger"
      :busy="cacheClearBusy"
      :error="storageConfirmationError || undefined"
      :confirm-disabled="cacheClearBusy"
      @cancel="cancelCacheClear"
      @confirm="confirmCacheClear"
    />
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
.settings-overtime-card,
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
.storage-clear-btn {
  margin-top: 0.75rem;
}

.sessions-list {
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
  overflow-wrap: anywhere;
}

.session-meta,
.storage-copy,
.storage-label {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.session-meta span + span::before {
  content: '·';
  margin-left: 0.45rem;
}

.storage-copy {
  margin: 0;
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
