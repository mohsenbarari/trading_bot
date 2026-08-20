<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { openTelegramLink } from '../services/telegramLink'
import {
  AppButton,
  AppErrorState,
  AppFormField,
  AppInput,
  AppLoadingState,
  AppTextarea,
  AuthFlowShell,
} from '../components/ui'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { formatIranDateTime } from '../utils/iranTime'
import { assertSuccessfulNavigation } from '../utils/navigationResult'
import { routeRequestJson } from '../utils/routeRequest'
import {
  captureLegacyRegistrationHandoff,
  clearRegistrationHandoff,
  replaceWithScrubbedRegistrationUrl,
  scrubRegistrationSecretsFromBrowserUrl,
  type RegistrationHandoff,
} from '../utils/registrationHandoff'

const route = useRoute()
const router = useRouter()
const legacyHandoff = captureLegacyRegistrationHandoff(route.query)
let pendingRawHandoff: RegistrationHandoff | null = legacyHandoff.handoff
let pendingExchangeId: string | null = pendingRawHandoff ? createExchangeId() : null
let exchangeWasAttempted = false
let viewActive = true
let queryScrubPromise: Promise<boolean> | null = null
const REGISTRATION_PROGRESS_SESSION_KEY = 'web_registration_progress_v1'

function createExchangeId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function clearRegistrationProgress() {
  try {
    sessionStorage.removeItem(REGISTRATION_PROGRESS_SESSION_KEY)
  } catch {
    // Non-sensitive progress restoration is best-effort only.
  }
}

const browserUrlWasScrubbed = scrubRegistrationSecretsFromBrowserUrl()

if (legacyHandoff.hadSensitiveQuery || browserUrlWasScrubbed) {
  clearRegistrationProgress()
}

if (legacyHandoff.hadSensitiveQuery || browserUrlWasScrubbed) {
  // Remove legacy secrets from the visible address immediately, then synchronize
  // Vue Router while retaining only already-scrubbed query/hash state.
  const scrubbedHash = window.location.hash
  queryScrubPromise = Promise.resolve(
    router.replace({
      name: 'web-register',
      query: legacyHandoff.sanitizedQuery as LocationQueryRaw,
      hash: scrubbedHash,
    }),
  )
    .then((result) => {
      assertSuccessfulNavigation(result)
      return true
    })
    .catch(() => {
      replaceWithScrubbedRegistrationUrl()
      return false
    })
}

const step = ref<1 | 2 | 3 | 4>(1)
const loading = ref(true)
const redirecting = ref(false)
const error = ref('')
type RegistrationOperation = 'load-context' | 'request-otp' | 'verify-otp' | 'complete-registration'
const failedOperation = ref<RegistrationOperation | null>(null)
const retryAvailable = ref(false)
const activeActions = new Set<RegistrationOperation>()

const inviteInfo = ref<RegistrationContext | null>(null)
const contextKind = ref<'invitation' | 'registration' | null>(null)
const otpCode = ref('')
const address = ref('')
const canConnectTelegram = ref(false)
const telegramLinkBusy = ref(false)
const telegramLinkError = ref('')
const otpInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
const addressInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
const completionStatus = ref<HTMLElement | null>(null)
const inviteExpiry = computed(() => {
  const value = inviteInfo.value?.expires_at
  return value ? formatIranDateTime(value) : ''
})
const otpCodeValid = computed(() => /^\d{5}$/.test(otpCode.value))

const showRequiredProgress = computed(() => contextKind.value === 'invitation' && step.value <= 3)
const requiredProgressStep = computed(() => (showRequiredProgress.value ? step.value : undefined))
const authDescription = computed(() => {
  if (step.value === 1) return 'اطلاعات دعوت‌نامه را بررسی کنید و کد تأیید را دریافت کنید.'
  if (step.value === 2) return 'کد پنج‌رقمی ارسال‌شده به موبایل دعوت‌شده را وارد کنید.'
  if (step.value === 3) {
    return contextKind.value === 'registration'
      ? 'برای تکمیل حساب، نشانی دقیق پستی را ثبت کنید.'
      : 'پس از تأیید موبایل، نشانی دقیق پستی را ثبت کنید.'
  }
  return 'اتصال تلگرام اختیاری است و دسترسی وب را محدود نمی‌کند.'
})

class TerminalRegistrationError extends Error {}

type RegistrationContextProgress = 'context_ready' | 'otp_requested' | 'otp_verified'

interface RegistrationContext extends Record<string, unknown> {
  account_name: string
  mobile_number: string
  role: string
  expires_at?: string | null
  kind: 'invitation' | 'registration'
  progress: RegistrationContextProgress
  requires_otp: boolean
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasRegistrationContext(value: unknown): value is RegistrationContext {
  return (
    isObject(value) &&
    typeof value.account_name === 'string' &&
    Boolean(value.account_name.trim()) &&
    typeof value.mobile_number === 'string' &&
    Boolean(value.mobile_number.trim()) &&
    typeof value.role === 'string' &&
    Boolean(value.role.trim()) &&
    (value.kind === 'invitation' || value.kind === 'registration') &&
    (value.progress === 'context_ready' ||
      value.progress === 'otp_requested' ||
      value.progress === 'otp_verified') &&
    typeof value.requires_otp === 'boolean'
  )
}

function stepForContext(context: RegistrationContext): 1 | 2 | 3 {
  if (context.kind === 'registration' || context.progress === 'otp_verified') return 3
  if (context.progress === 'otp_requested') return 2
  return 1
}

function hasDetailReceipt(value: unknown) {
  return isObject(value) && typeof value.detail === 'string' && Boolean(value.detail.trim())
}

function isRegistrationCompleteOutcome(
  value: unknown,
): value is { status: 'registration_complete' } {
  return isObject(value) && value.status === 'registration_complete'
}

async function finishCompletedRegistrationRecovery() {
  redirecting.value = true
  if (await redirectAuthenticatedContextMiss()) {
    await clearServerRegistrationContext()
    return
  }
  try {
    assertSuccessfulNavigation(
      await router.replace({ name: 'login', query: { registration: 'complete' } }),
    )
  } catch (cause: unknown) {
    redirecting.value = false
    throw cause
  }
  pendingRawHandoff = null
  pendingExchangeId = null
  clearRegistrationProgress()
  clearRegistrationHandoff()
  await clearServerRegistrationContext()
}

async function clearServerRegistrationContext() {
  try {
    await routeRequestJson<unknown>('/api/auth/registration-context/clear', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
    })
  } catch {
    // The server-side context and completion receipt remain bounded to 10 min.
  }
}

function hasTokenReceipt(value: unknown): value is { access_token: string; refresh_token: string } {
  return (
    isObject(value) &&
    typeof value.access_token === 'string' &&
    Boolean(value.access_token.trim()) &&
    typeof value.refresh_token === 'string' &&
    Boolean(value.refresh_token.trim())
  )
}

function hasTelegramLinkReceipt(value: unknown): value is Record<string, unknown> & {
  telegram_linked: boolean
  can_connect_telegram: boolean
} {
  return (
    isObject(value) &&
    typeof value.telegram_linked === 'boolean' &&
    typeof value.can_connect_telegram === 'boolean'
  )
}

function beginAction(key: RegistrationOperation) {
  if (activeActions.has(key)) return false
  activeActions.add(key)
  loading.value = true
  return true
}

function finishAction(key: RegistrationOperation) {
  activeActions.delete(key)
  loading.value = activeActions.size > 0
}

function clearFailure() {
  error.value = ''
  failedOperation.value = null
  retryAvailable.value = false
}

function isRetryableFailure(cause: unknown) {
  if (!isAppHttpError(cause)) return true
  return (
    cause.status === null ||
    cause.status === 408 ||
    cause.status === 425 ||
    cause.status === 429 ||
    cause.status >= 500
  )
}

function failureMessage(cause: unknown, fallback: string) {
  if (isAppHttpError(cause)) {
    if (cause.status !== null && cause.status >= 500) return cause.presentation.message
    return cause.detail || fallback
  }
  if (cause instanceof SyntaxError) return fallback
  if (cause instanceof Error && /^[\u0600-\u06ff]/u.test(cause.message)) return cause.message
  return fallback
}

function recordFailure(operation: RegistrationOperation, cause: unknown, fallback: string) {
  error.value = failureMessage(cause, fallback)
  failedOperation.value = operation
  retryAvailable.value = isRetryableFailure(cause)
}

function recordOperationFailure(
  operation: RegistrationOperation,
  cause: unknown,
  fallback: string,
) {
  if (isAppHttpError(cause) && [401, 403, 404, 410].includes(cause.status ?? 0)) {
    inviteInfo.value = null
    contextKind.value = null
    error.value = 'جلسه ثبت‌نام نامعتبر یا منقضی شده است.'
    failedOperation.value = null
    retryAvailable.value = false
    clearRegistrationHandoff()
    return
  }
  recordFailure(operation, cause, fallback)
}

function isTerminalContextFailure(cause: unknown) {
  return (
    cause instanceof TerminalRegistrationError ||
    (isAppHttpError(cause) &&
      cause.status !== null &&
      [400, 401, 403, 404, 410, 422].includes(cause.status))
  )
}

async function redirectAuthenticatedContextMiss(): Promise<boolean> {
  const accessToken = localStorage.getItem('auth_token')?.trim()
  if (!accessToken) return false
  let currentUser: unknown
  try {
    currentUser = await routeRequestJson<unknown>('/api/auth/me', {
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'initial-load',
      },
    })
  } catch (cause: unknown) {
    if (isAppHttpError(cause) && [401, 403].includes(cause.status ?? 0)) return false
    throw cause
  }
  if (!isObject(currentUser)) return false

  redirecting.value = true
  try {
    assertSuccessfulNavigation(await router.replace('/'))
  } catch (cause: unknown) {
    redirecting.value = false
    throw cause
  }
  pendingRawHandoff = null
  pendingExchangeId = null
  clearRegistrationProgress()
  clearRegistrationHandoff()
  return true
}

async function loadRegistrationContext() {
  if (!beginAction('load-context')) return
  clearFailure()
  redirecting.value = false

  try {
    const handoff = pendingRawHandoff
    const exchangeId = pendingExchangeId
    let data: unknown
    if (handoff && exchangeId) {
      if (exchangeWasAttempted) {
        try {
          data = await routeRequestJson<unknown>('/api/auth/registration-context', {
            mode: 'public',
            method: 'POST',
            credentials: 'same-origin',
            errorContext: { surface: 'auth', scope: 'page', operation: 'initial-load' },
          })
        } catch (cause: unknown) {
          if (!isAppHttpError(cause) || ![400, 410].includes(cause.status ?? 0)) throw cause
        }
      }
      if (!data) {
        exchangeWasAttempted = true
        data = await routeRequestJson<unknown>('/api/auth/registration-context/exchange', {
          mode: 'public',
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: handoff.kind,
            token: handoff.token,
            exchange_id: exchangeId,
          }),
          errorContext: { surface: 'auth', scope: 'page', operation: 'initial-load' },
        })
      }
    } else {
      data = await routeRequestJson<unknown>('/api/auth/registration-context', {
        mode: 'public',
        method: 'POST',
        credentials: 'same-origin',
        errorContext: { surface: 'auth', scope: 'page', operation: 'initial-load' },
      })
    }
    if (isRegistrationCompleteOutcome(data)) {
      await finishCompletedRegistrationRecovery()
      return
    }
    if (!hasRegistrationContext(data)) throw new Error('پاسخ بررسی دعوت‌نامه کامل نیست.')
    pendingRawHandoff = null
    pendingExchangeId = null
    clearRegistrationHandoff()
    inviteInfo.value = data
    contextKind.value = data.kind
    step.value = stepForContext(data)
  } catch (cause: unknown) {
    redirecting.value = false
    if (isTerminalContextFailure(cause)) {
      try {
        if (await redirectAuthenticatedContextMiss()) return
      } catch (authCause: unknown) {
        recordFailure('load-context', authCause, 'بررسی نشست اکنون ممکن نشد.')
        return
      }
      pendingRawHandoff = null
      pendingExchangeId = null
      clearRegistrationHandoff()
      error.value =
        cause instanceof TerminalRegistrationError
          ? cause.message
          : 'جلسه ثبت‌نام نامعتبر یا منقضی شده است.'
      failedOperation.value = null
      retryAvailable.value = false
    } else {
      recordFailure('load-context', cause, 'بررسی اطلاعات ثبت‌نام اکنون ممکن نشد.')
    }
  } finally {
    finishAction('load-context')
  }
}

onMounted(async () => {
  if (queryScrubPromise && !(await queryScrubPromise)) return
  if (viewActive) await loadRegistrationContext()
})

onBeforeUnmount(() => {
  viewActive = false
})

watch(step, async (nextStep) => {
  await nextTick()
  if (nextStep === 2) otpInput.value?.focus()
  if (nextStep === 3) addressInput.value?.focus()
  if (nextStep === 4) completionStatus.value?.focus()
})

async function requestOtp() {
  if (!beginAction('request-otp')) return
  clearFailure()
  try {
    const data = await routeRequestJson<unknown>('/api/auth/registration-context/otp/request', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
      errorContext: { surface: 'auth', scope: 'form', operation: 'submit' },
    })
    if (isRegistrationCompleteOutcome(data)) {
      await finishCompletedRegistrationRecovery()
      return
    }
    if (!hasDetailReceipt(data)) throw new Error('پاسخ ارسال کد تایید کامل نیست.')
    step.value = 2
  } catch (cause: unknown) {
    recordOperationFailure('request-otp', cause, 'ارسال کد تایید اکنون ممکن نشد.')
  } finally {
    finishAction('request-otp')
  }
}

async function verifyOtp() {
  if (!otpCodeValid.value) {
    error.value = 'کد تأیید باید دقیقاً پنج رقم باشد.'
    failedOperation.value = 'verify-otp'
    retryAvailable.value = false
    await nextTick()
    otpInput.value?.focus()
    return
  }
  if (!beginAction('verify-otp')) return
  clearFailure()
  try {
    const data = await routeRequestJson<unknown>('/api/auth/registration-context/otp/verify', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: otpCode.value }),
      errorContext: { surface: 'auth', scope: 'field', operation: 'submit' },
    })
    if (isRegistrationCompleteOutcome(data)) {
      await finishCompletedRegistrationRecovery()
      return
    }
    if (!hasDetailReceipt(data)) throw new Error('پاسخ تایید کد کامل نیست.')
    step.value = 3
  } catch (cause: unknown) {
    recordOperationFailure('verify-otp', cause, 'کد نادرست است')
    await nextTick()
    otpInput.value?.focus()
  } finally {
    finishAction('verify-otp')
  }
}

async function submitRegistration() {
  if (address.value.length < 10) {
    error.value = 'آدرس باید حداقل ۱۰ کاراکتر باشد.'
    failedOperation.value = 'complete-registration'
    retryAvailable.value = false
    await nextTick()
    addressInput.value?.focus()
    return
  }
  if (!beginAction('complete-registration')) return
  clearFailure()
  try {
    const data = await routeRequestJson<unknown>('/api/auth/registration-context/complete', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: address.value }),
      errorContext: { surface: 'auth', scope: 'form', operation: 'submit' },
    })
    if (isRegistrationCompleteOutcome(data)) {
      await finishCompletedRegistrationRecovery()
      return
    }
    if (!hasTokenReceipt(data)) throw new Error('پاسخ تکمیل ثبت‌نام کامل نیست.')
    localStorage.setItem('auth_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    const me = await routeRequestJson<Record<string, unknown>>('/api/auth/me', {
      errorContext: { surface: 'auth', scope: 'action', operation: 'initial-load' },
    }).catch(() => null)
    canConnectTelegram.value = me?.can_connect_telegram === true && me?.telegram_linked !== true
    if (canConnectTelegram.value) {
      step.value = 4
      await nextTick()
      clearRegistrationProgress()
      clearRegistrationHandoff()
      await clearServerRegistrationContext()
      return
    }
    redirecting.value = true
    try {
      assertSuccessfulNavigation(await router.replace('/'))
    } catch (cause: unknown) {
      redirecting.value = false
      throw cause
    }
    clearRegistrationProgress()
    clearRegistrationHandoff()
    await clearServerRegistrationContext()
  } catch (cause: unknown) {
    recordOperationFailure('complete-registration', cause, 'تکمیل ثبت‌نام اکنون ممکن نشد.')
    await nextTick()
    addressInput.value?.focus()
  } finally {
    finishAction('complete-registration')
  }
}

async function retryFailedOperation() {
  const operation = failedOperation.value
  if (!operation || !retryAvailable.value) return
  if (operation === 'load-context') return loadRegistrationContext()
  if (operation === 'request-otp') return requestOtp()
  if (operation === 'verify-otp') return verifyOtp()
  return submitRegistration()
}

async function returnToForm() {
  const operation = failedOperation.value
  clearFailure()
  await nextTick()
  if (operation === 'verify-otp') otpInput.value?.focus()
  if (operation === 'complete-registration') addressInput.value?.focus()
}

function goBackOneStep() {
  clearFailure()
  if (step.value === 2) step.value = 1
  else if (step.value === 3 && contextKind.value === 'invitation') step.value = 2
}

async function returnToLogin() {
  try {
    assertSuccessfulNavigation(await router.replace({ name: 'login' }))
  } catch {
    error.value = 'بازگشت به ورود اکنون ممکن نشد. دوباره تلاش کنید.'
    return
  }
  clearRegistrationProgress()
  clearRegistrationHandoff()
  await clearServerRegistrationContext()
}

async function connectTelegram() {
  if (telegramLinkBusy.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = ''
  try {
    const payload = await routeRequestJson<unknown>('/api/auth/telegram-link-token', {
      method: 'POST',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'submit',
        fallbackMessage: 'ساخت لینک اتصال تلگرام ناموفق بود.',
      },
    })
    if (!hasTelegramLinkReceipt(payload)) throw new Error('پاسخ اتصال تلگرام کامل نیست.')
    if (typeof payload.telegram_url === 'string' && payload.telegram_url.trim()) {
      openTelegramLink(payload.telegram_url)
      return
    }
    telegramLinkError.value =
      typeof payload.detail === 'string' && payload.detail.trim()
        ? payload.detail
        : 'لینک اتصال تلگرام آماده نشد.'
  } catch (cause: unknown) {
    telegramLinkError.value = failureMessage(cause, 'ساخت لینک اتصال تلگرام ناموفق بود.')
  } finally {
    telegramLinkBusy.value = false
  }
}

async function skipTelegramConnect() {
  if (telegramLinkBusy.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = ''
  try {
    assertSuccessfulNavigation(await router.replace('/'))
  } catch {
    telegramLinkError.value = 'بازگشت به خانه اکنون ممکن نشد. دوباره تلاش کنید.'
  } finally {
    telegramLinkBusy.value = false
  }
}
</script>

<template>
  <AuthFlowShell
    fill-viewport
    title="تکمیل ثبت‌نام"
    :description="authDescription"
    :current-step="requiredProgressStep"
    :total-steps="showRequiredProgress ? 3 : undefined"
  >
    <AppLoadingState
      v-if="redirecting || (loading && !inviteInfo)"
      :label="redirecting ? 'در حال انتقال به ورود' : 'در حال بررسی دعوت‌نامه'"
    />

    <AppErrorState
      v-else-if="error && !inviteInfo"
      title="ثبت‌نام ادامه پیدا نکرد"
      :message="error"
    >
      <template #actions>
        <AppButton
          v-if="retryAvailable"
          variant="secondary"
          block
          :loading="loading"
          @click="retryFailedOperation"
        >
          تلاش مجدد
        </AppButton>
        <AppButton v-else variant="secondary" block @click="returnToLogin">
          بازگشت به ورود
        </AppButton>
      </template>
    </AppErrorState>

    <section v-else class="ui-v2-auth-register-step" aria-live="polite">
      <div v-if="inviteInfo" class="ui-v2-auth-register-context">
        <p>
          <span>نام حساب</span><strong>{{ inviteInfo.account_name }}</strong>
        </p>
        <p>
          <span>موبایل</span><strong dir="ltr">{{ inviteInfo.mobile_number }}</strong>
        </p>
        <p>
          <span>نقش</span><strong>{{ inviteInfo.role }}</strong>
        </p>
        <p v-if="inviteExpiry">
          <span>مهلت ثبت‌نام</span><strong>{{ inviteExpiry }}</strong>
        </p>
      </div>

      <div v-if="error" class="ui-v2-auth-error" role="alert">
        <span>{{ error }}</span>
        <AppButton
          v-if="retryAvailable"
          variant="secondary"
          size="sm"
          :loading="loading"
          @click="retryFailedOperation"
        >
          تلاش مجدد
        </AppButton>
        <button v-else type="button" class="ui-v2-auth-register-link" @click="returnToForm">
          بازگشت به فرم
        </button>
      </div>

      <template v-if="step === 1">
        <p class="ui-v2-auth-register-guidance">
          کد تأیید برای موبایل ثبت‌شده در دعوت‌نامه ارسال می‌شود.
        </p>
        <AppButton block :loading="loading" @click="requestOtp">دریافت کد تأیید</AppButton>
      </template>

      <template v-else-if="step === 2">
        <AppFormField label="کد تأیید پنج‌رقمی">
          <template #default="{ id, describedby }">
            <AppInput
              :id="id"
              ref="otpInput"
              v-model="otpCode"
              class="ui-v2-auth-register-code"
              :aria-describedby="describedby"
              type="text"
              inputmode="numeric"
              pattern="[0-9]*"
              maxlength="5"
              dir="ltr"
              autocomplete="one-time-code"
              placeholder="_____"
              autofocus
            />
          </template>
        </AppFormField>
        <AppButton block :disabled="!otpCodeValid" :loading="loading" @click="verifyOtp">
          تأیید و ادامه
        </AppButton>
        <button type="button" class="ui-v2-auth-register-link" @click="goBackOneStep">
          بازگشت به بررسی دعوت‌نامه
        </button>
      </template>

      <template v-else-if="step === 3">
        <AppFormField
          label="نشانی دقیق پستی"
          hint="استان، شهر، خیابان، پلاک و توضیح لازم را کامل وارد کنید."
        >
          <template #default="{ id, describedby }">
            <AppTextarea
              :id="id"
              ref="addressInput"
              v-model="address"
              class="ui-v2-auth-register-address"
              :aria-describedby="describedby"
              rows="4"
              autocomplete="street-address"
              placeholder="استان، شهر، خیابان، پلاک…"
              autofocus
            />
          </template>
        </AppFormField>
        <p class="ui-v2-auth-register-privacy">نشانی در پروفایل عمومی نمایش داده نمی‌شود.</p>
        <AppButton
          block
          :disabled="address.length < 10"
          :loading="loading"
          @click="submitRegistration"
        >
          تکمیل ثبت‌نام
        </AppButton>
        <button
          v-if="contextKind === 'invitation'"
          type="button"
          class="ui-v2-auth-register-link"
          @click="goBackOneStep"
        >
          بازگشت به کد تأیید
        </button>
      </template>

      <template v-else>
        <div
          ref="completionStatus"
          class="ui-v2-auth-register-guidance"
          role="status"
          tabindex="-1"
          aria-labelledby="registration-complete-title"
        >
          <strong id="registration-complete-title">اتصال تلگرام</strong>
          <p>این اتصال اختیاری است و دسترسی وب شما را محدود نمی‌کند.</p>
        </div>
        <div v-if="telegramLinkError" class="ui-v2-auth-error" role="alert">
          {{ telegramLinkError }}
        </div>
        <AppButton block :loading="telegramLinkBusy" @click="connectTelegram">
          اتصال به ربات تلگرام
        </AppButton>
        <AppButton
          block
          variant="secondary"
          :disabled="telegramLinkBusy"
          @click="skipTelegramConnect"
          >فعلاً رد می‌کنم</AppButton
        >
      </template>
    </section>
  </AuthFlowShell>
</template>
