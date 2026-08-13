<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Clock, FileCheck2, Loader2 } from 'lucide-vue-next'
import { setupExpiryTimer } from '../utils/auth'
import { clearIntendedRoute, readIntendedRoute } from '../utils/authNavigation'
import { clearCurrentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { assertSuccessfulNavigation } from '../utils/navigationResult'
import { consumeLocalLogoutReceipt } from '../utils/localLogoutReceipt'
import { routeRequestJson } from '../utils/routeRequest'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import { AppButton, AppFormField, AppInput, AppTextarea, AuthFlowShell } from '../components/ui'

const router = useRouter()
const route = useRoute()
const completedRegistrationNotice = computed(() => route.query.registration === 'complete')
const localLogoutNotice = ref(consumeLocalLogoutReceipt())
type LoginStep =
  | 'mobile'
  | 'otp'
  | 'waiting_approval'
  | 'recovery_waiting'
  | 'recovery_identity'
  | 'recovery_submitted'
  | 'recovery_approved'
  | 'recovery_rejected'
  | 'recovery_expired'

const step = ref<LoginStep>('mobile')
const loading = ref(false)
const error = ref('')
const mobileInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
const otpInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
const statusStepContainer = ref<HTMLElement | null>(null)

// OTP Timer State
const countdown = ref(0)
let timerInterval: ReturnType<typeof window.setInterval> | null = null
let countdownDeadlineMs: number | null = null
const otpRequestId = ref<string | null>(null)
const otpExpiresAt = ref<string | null>(null)
const smsFallbackAt = ref<string | null>(null)
const legacySmsResendAt = ref<string | null>(null)
const legacyManualSmsResend = ref(false)
const OTP_ATTEMPT_SESSION_KEY = 'login_otp_attempt_v1'

const form = reactive({
  mobile: '',
  code: '',
})
const maskedMobileForDisplay = computed(() => {
  const digits = form.mobile.replace(/\D/gu, '')
  return /^09\d{9}$/u.test(digits) ? `${digits.slice(0, 4)}****${digits.slice(-3)}` : ''
})

// Session approval state
const loginRequestId = ref<string | null>(null)
const approvalExpiresAt = ref<string | null>(null)
const approvalCountdown = ref(0)
let approvalTimerInterval: ReturnType<typeof window.setInterval> | null = null
let approvalPollInterval: ReturnType<typeof window.setInterval> | null = null

// Recovery flow state
const recoveryStatus = ref<string | null>(null)
const recoveryCountdown = ref(0)
const recoveryFile = ref<File | null>(null)
const recoveryCaption = ref('')
const recoveryApprovedTokens = ref<{ access_token: string; refresh_token?: string | null } | null>(
  null,
)
const recoveryFileInput = ref<HTMLInputElement | null>(null)
const recoveryCameraInput = ref<HTMLInputElement | null>(null)
const recoveryDocumentInput = ref<HTMLInputElement | null>(null)
let recoveryTimerInterval: ReturnType<typeof window.setInterval> | null = null
let recoveryPollInterval: ReturnType<typeof window.setInterval> | null = null
type LoginActionKey =
  | 'request-otp'
  | 'resend-otp'
  | 'verify-otp'
  | 'start-recovery'
  | 'cancel-recovery'
  | 'submit-recovery-identity'
  | 'enter-approved-recovery'
  | 'complete-login-transition'
  | 'dev-login'
const activeActions = new Set<LoginActionKey>()
let approvalPollPending = false
let recoveryPollPending = false
let loginContextEpoch = 0

type JsonObject = Record<string, unknown>
type LoginTokenReceipt = JsonObject & { access_token: string; refresh_token?: string | null }
type VerifyOtpReceipt =
  | LoginTokenReceipt
  | (JsonObject & {
      status: 'approval_required' | 'registration_required' | 'registration_complete'
      login_request_id?: string
      expires_at?: string
    })
type RecoveryReceipt = JsonObject & { status: string }
const pendingAuthenticatedLogin = ref<LoginTokenReceipt | null>(null)

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function readString(record: JsonObject, key: string): string | null {
  const value = record[key]
  return typeof value === 'string' ? value : null
}

function beginAction(key: LoginActionKey) {
  if (activeActions.has(key)) return false
  activeActions.add(key)
  loading.value = true
  return true
}

function finishAction(key: LoginActionKey) {
  activeActions.delete(key)
  loading.value = activeActions.size > 0
}

function requestErrorMessage(cause: unknown, fallback: string) {
  if (isAppHttpError(cause)) {
    if (cause.status !== null && cause.status >= 500) return cause.presentation.message
    return parseResponseError(cause.payload, cause.detail || fallback)
  }
  if (cause instanceof SyntaxError) return fallback
  if (cause instanceof Error && /^[\u0600-\u06ff]/u.test(cause.message)) return cause.message
  return fallback
}

interface LoginContextSnapshot {
  epoch: number
  step: LoginStep
  mobile: string
  code?: string
  otpRequestId?: string | null
}

function invalidateLoginContext() {
  loginContextEpoch += 1
}

function captureLoginContext(
  options: { includeCode?: boolean; includeOtpRequestId?: boolean } = {},
): LoginContextSnapshot {
  return {
    epoch: loginContextEpoch,
    step: step.value,
    mobile: form.mobile,
    ...(options.includeCode ? { code: form.code } : {}),
    ...(options.includeOtpRequestId ? { otpRequestId: otpRequestId.value } : {}),
  }
}

function isLoginContextCurrent(snapshot: LoginContextSnapshot) {
  return (
    snapshot.epoch === loginContextEpoch &&
    snapshot.step === step.value &&
    snapshot.mobile === form.mobile &&
    (snapshot.code === undefined || snapshot.code === form.code) &&
    (snapshot.otpRequestId === undefined || snapshot.otpRequestId === otpRequestId.value)
  )
}

function hasLoginTokens(value: unknown): value is LoginTokenReceipt {
  return (
    isObject(value) &&
    typeof value.access_token === 'string' &&
    Boolean(value.access_token.trim()) &&
    (value.refresh_token === undefined ||
      value.refresh_token === null ||
      typeof value.refresh_token === 'string')
  )
}

function isOtpDeliveryReceipt(value: unknown): value is JsonObject {
  if (!isObject(value)) return false
  if (value.method === 'log') {
    const hasDetail = typeof value.detail === 'string' && Boolean(value.detail.trim())
    const hasExpiry =
      (typeof value.expires_in === 'number' &&
        Number.isFinite(value.expires_in) &&
        value.expires_in > 0) ||
      (typeof value.expires_at === 'string' && Boolean(value.expires_at.trim()))
    return hasDetail && hasExpiry
  }
  return (
    value.method === 'telegram' ||
    value.method === 'sms' ||
    (typeof value.otp_request_id === 'string' && Boolean(value.otp_request_id.trim()))
  )
}

function isVerifyOtpReceipt(value: unknown): value is VerifyOtpReceipt {
  if (hasLoginTokens(value)) return true
  if (!isObject(value) || typeof value.status !== 'string') return false
  if (value.status === 'approval_required') {
    return typeof value.login_request_id === 'string' && Boolean(value.login_request_id.trim())
  }
  if (value.status === 'registration_required') {
    return true
  }
  if (value.status === 'registration_complete') return true
  return false
}

function isRecoveredDirectRegistrationContext(value: unknown): value is JsonObject {
  return isObject(value) && value.kind === 'registration' && value.progress === 'otp_verified'
}

const knownRecoveryStatuses = new Set([
  'not_started',
  'pending_admin_review',
  'identity_verification_requested',
  'identity_submitted',
  'approved',
  'rejected',
  'expired',
  'cancelled',
])

function isRecoveryReceipt(value: unknown): value is RecoveryReceipt {
  return (
    isObject(value) && typeof value.status === 'string' && knownRecoveryStatuses.has(value.status)
  )
}

async function completeAuthenticatedLogin(data: {
  access_token: string
  refresh_token?: string | null
}): Promise<boolean> {
  localStorage.setItem('auth_token', data.access_token)
  if (data.refresh_token) {
    localStorage.setItem('refresh_token', data.refresh_token)
  }
  pendingAuthenticatedLogin.value = {
    access_token: data.access_token,
    refresh_token: data.refresh_token,
  }

  try {
    await primeCurrentUserSummary(true)
  } catch {
    // Do not block the login transition on a best-effort current-user prefetch.
  }

  const intendedRoute = readIntendedRoute()
  try {
    assertSuccessfulNavigation(await router.push(intendedRoute || '/'))
  } catch {
    error.value = 'ورود انجام شد، اما انتقال به صفحه بعد ممکن نشد. دوباره تلاش کنید.'
    return false
  }

  pendingAuthenticatedLogin.value = null
  localStorage.removeItem('suspended_refresh_token')
  setupExpiryTimer()
  clearBackStack()
  clearIntendedRoute()
  return true
}

async function retryAuthenticatedLoginTransition() {
  const pendingTokens = pendingAuthenticatedLogin.value
  if (!pendingTokens || !beginAction('complete-login-transition')) return
  error.value = ''
  try {
    const completed = await completeAuthenticatedLogin(pendingTokens)
    if (completed) {
      clearOtpAttempt()
      form.code = ''
    }
  } finally {
    finishAction('complete-login-transition')
  }
}

function syncCountdown() {
  if (countdownDeadlineMs === null) {
    countdown.value = 0
    return
  }
  countdown.value = Math.max(0, Math.ceil((countdownDeadlineMs - Date.now()) / 1000))
  if (countdown.value === 0 && timerInterval) {
    clearInterval(timerInterval)
    timerInterval = null
  }
}

function startTimerUntil(deadline: string | number) {
  if (timerInterval) clearInterval(timerInterval)
  const deadlineMs = typeof deadline === 'number' ? deadline : new Date(deadline).getTime()
  countdownDeadlineMs = Number.isFinite(deadlineMs) ? deadlineMs : null
  syncCountdown()
  if (countdown.value > 0) timerInterval = setInterval(syncCountdown, 1000)
}

function persistOtpAttempt() {
  if (!otpRequestId.value || !otpExpiresAt.value) return
  try {
    sessionStorage.setItem(
      OTP_ATTEMPT_SESSION_KEY,
      JSON.stringify({
        requestId: otpRequestId.value,
        method: lastMethod.value,
        expiresAt: otpExpiresAt.value,
        smsFallbackAt: smsFallbackAt.value,
      }),
    )
  } catch {
    // Browser storage is best-effort; backend timing remains authoritative.
  }
}

function clearOtpAttempt() {
  otpRequestId.value = null
  otpExpiresAt.value = null
  smsFallbackAt.value = null
  legacySmsResendAt.value = null
  legacyManualSmsResend.value = false
  countdownDeadlineMs = null
  if (timerInterval) clearInterval(timerInterval)
  timerInterval = null
  countdown.value = 0
  try {
    sessionStorage.removeItem(OTP_ATTEMPT_SESSION_KEY)
  } catch {
    // Ignore unavailable browser storage.
  }
}

function applyOtpTiming(data: JsonObject) {
  const nextOtpRequestId = readString(data, 'otp_request_id')
  const method = readString(data, 'method')
  const expiresAt = readString(data, 'expires_at')
  const nextSmsFallbackAt = readString(data, 'sms_fallback_at')
  const nextLegacySmsResendAt = readString(data, 'legacy_sms_resend_at')
  otpRequestId.value = nextOtpRequestId
  lastMethod.value = method === 'telegram' || method === 'sms' ? method : null
  otpExpiresAt.value =
    expiresAt ||
    new Date(Date.now() + Math.max(0, Number(data.expires_in) || 0) * 1000).toISOString()
  smsFallbackAt.value = nextSmsFallbackAt
  legacyManualSmsResend.value =
    data.manual_sms_resend === true || (lastMethod.value === 'telegram' && !smsFallbackAt.value)
  legacySmsResendAt.value = legacyManualSmsResend.value
    ? nextLegacySmsResendAt || new Date(Date.now() + 30_000).toISOString()
    : null
  const displayDeadline =
    lastMethod.value === 'telegram' && smsFallbackAt.value
      ? smsFallbackAt.value
      : legacySmsResendAt.value || otpExpiresAt.value
  startTimerUntil(displayDeadline)
  persistOtpAttempt()
}

function restoreOtpAttempt() {
  try {
    const raw = sessionStorage.getItem(OTP_ATTEMPT_SESSION_KEY)
    if (!raw) return
    const saved = JSON.parse(raw)
    const expiresAtMs = new Date(saved?.expiresAt).getTime()
    if (!saved?.requestId || !Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) {
      clearOtpAttempt()
      return
    }
    otpRequestId.value = saved.requestId
    otpExpiresAt.value = saved.expiresAt
    smsFallbackAt.value = typeof saved.smsFallbackAt === 'string' ? saved.smsFallbackAt : null
    legacySmsResendAt.value = null
    legacyManualSmsResend.value = false
    lastMethod.value = saved.method === 'telegram' || saved.method === 'sms' ? saved.method : null
    step.value = 'otp'
    const displayDeadline =
      lastMethod.value === 'telegram' && smsFallbackAt.value
        ? smsFallbackAt.value
        : otpExpiresAt.value
    if (!displayDeadline) {
      clearOtpAttempt()
      return
    }
    startTimerUntil(displayDeadline)
  } catch {
    clearOtpAttempt()
  }
}

const formattedTimer = computed(() => {
  const m = Math.floor(countdown.value / 60)
    .toString()
    .padStart(2, '0')
  const s = (countdown.value % 60).toString().padStart(2, '0')
  return `${m}:${s}`
})

function formatCountdown(seconds: number) {
  const safeSeconds = Math.max(0, seconds)
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const remainingSeconds = safeSeconds % 60
  if (hours > 0) {
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`
  }
  return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`
}

const formattedApprovalTimer = computed(() => formatCountdown(approvalCountdown.value))
const formattedRecoveryTimer = computed(() => formatCountdown(recoveryCountdown.value))
const selectedRecoveryFileName = computed(() => recoveryFile.value?.name || '')
const canOfferAppRecovery = computed(() => {
  const message = (error.value || '').toLowerCase()
  if (!message) return false
  return (
    message.includes('failed to fetch') ||
    message.includes('networkerror') ||
    message.includes('load failed') ||
    message.includes('connection') ||
    message.includes('fetch dynamically imported module') ||
    message.includes('خطا در ارتباط با سرور')
  )
})
const mobileFieldError = computed(() =>
  step.value === 'mobile' && error.value === 'شماره موبایل معتبر نیست' ? error.value : '',
)
const otpFieldError = computed(() =>
  step.value === 'otp' &&
  error.value &&
  !canOfferAppRecovery.value &&
  !pendingAuthenticatedLogin.value
    ? error.value
    : '',
)
const processError = computed(() =>
  error.value && !mobileFieldError.value && !otpFieldError.value ? error.value : '',
)

const lastMethod = ref<'telegram' | 'sms' | null>(null)
const automaticSmsFallback = computed(
  () => lastMethod.value === 'telegram' && Boolean(smsFallbackAt.value),
)
const otpDeliveryStatus = computed(() => {
  if (!automaticSmsFallback.value) {
    return countdown.value > 0 ? `${formattedTimer.value} تا ارسال مجدد` : ''
  }
  if (countdown.value > 0) {
    return `کد ابتدا در تلگرام ارسال شد؛ ${formattedTimer.value} تا ارسال خودکار پیامک`
  }
  return 'ارسال خودکار همان کد از طریق پیامک فعال شد.'
})

function startAppRecovery() {
  const nextUrl = new URL(window.location.href)
  nextUrl.search = ''
  nextUrl.hash = ''
  nextUrl.searchParams.set('app_recovery', Date.now().toString())
  window.location.replace(`${nextUrl.pathname}${nextUrl.search}`)
}

function goToOtpStep() {
  if (step.value === 'otp') return
  step.value = 'otp'
  pushBackState(() => {
    if (pendingAuthenticatedLogin.value) return
    invalidateLoginContext()
    clearOtpAttempt()
    form.code = ''
    step.value = 'mobile'
    error.value = ''
  })
}

async function requestOtp() {
  if (pendingAuthenticatedLogin.value) return
  if (!form.mobile || form.mobile.length < 10) {
    error.value = 'شماره موبایل معتبر نیست'
    mobileInput.value?.focus()
    return
  }

  if (countdown.value > 0) {
    goToOtpStep()
    return
  }

  if (!beginAction('request-otp')) return
  const requestContext = captureLoginContext()
  error.value = ''

  try {
    const data = await routeRequestJson<unknown>('/api/auth/request-otp', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mobile_number: requestContext.mobile }),
      errorContext: {
        surface: 'auth',
        scope: 'form',
        operation: 'submit',
        fallbackMessage: 'خطا در ارسال کد',
      },
    })
    if (!isLoginContextCurrent(requestContext)) return
    if (!isOtpDeliveryReceipt(data)) throw new Error('پاسخ ارسال کد کامل نیست.')
    applyOtpTiming(data)
    goToOtpStep()
  } catch (cause: unknown) {
    if (!isLoginContextCurrent(requestContext)) return
    if (isAppHttpError(cause) && cause.status === 429) {
      const payload = cause.payload
      if (payload?.code === 'otp_active' && typeof payload.expires_at === 'string') {
        applyOtpTiming(payload)
        goToOtpStep()
        return
      }
      if (!payload?.code) {
        applyOtpTiming({
          delivery_contract: 'legacy',
          manual_sms_resend: true,
          legacy_sms_resend_at: new Date(Date.now() + 30_000).toISOString(),
        })
        goToOtpStep()
        return
      }
    }
    error.value = requestErrorMessage(cause, 'خطا در ارسال کد')
  } finally {
    finishAction('request-otp')
  }
}

async function resendOtpSms() {
  if (!beginAction('resend-otp')) return
  const requestContext = captureLoginContext({ includeOtpRequestId: true })
  error.value = ''

  try {
    const data = await routeRequestJson<unknown>('/api/auth/resend-otp-sms', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mobile_number: requestContext.mobile }),
      errorContext: {
        surface: 'auth',
        scope: 'form',
        operation: 'submit',
        fallbackMessage: 'خطا در ارسال پیامک',
      },
    })
    if (!isLoginContextCurrent(requestContext)) return
    if (!isOtpDeliveryReceipt(data)) throw new Error('پاسخ ارسال پیامک کامل نیست.')

    // SMS Sent successfully
    applyOtpTiming({
      ...data,
      method: 'sms',
      otp_request_id: data.otp_request_id || otpRequestId.value,
    })
  } catch (cause: unknown) {
    if (!isLoginContextCurrent(requestContext)) return
    error.value = requestErrorMessage(cause, 'خطا در ارسال پیامک')
  } finally {
    finishAction('resend-otp')
  }
}

function handleResend() {
  if (pendingAuthenticatedLogin.value) return
  if (automaticSmsFallback.value) return
  if (legacyManualSmsResend.value || lastMethod.value === 'telegram') {
    resendOtpSms()
    return
  }
  requestOtp()
}

async function routeToRegistrationContext(): Promise<boolean> {
  try {
    assertSuccessfulNavigation(await router.push({ name: 'web-register' }))
  } catch {
    error.value = 'ادامه ثبت‌نام اکنون ممکن نشد. دوباره تلاش کنید.'
    return false
  }
  clearOtpAttempt()
  form.code = ''
  localStorage.removeItem('suspended_refresh_token')
  clearBackStack()
  try {
    sessionStorage.removeItem('web_registration_progress_v1')
  } catch {
    // Non-sensitive flow progress is best-effort only.
  }
  return true
}

async function routeToRecoveredRegistrationCompletion(): Promise<boolean> {
  try {
    assertSuccessfulNavigation(
      await router.push({ name: 'login', query: { registration: 'complete' } }),
    )
  } catch {
    error.value = 'ادامه ثبت‌نام اکنون ممکن نشد. دوباره تلاش کنید.'
    return false
  }
  clearOtpAttempt()
  form.code = ''
  localStorage.removeItem('suspended_refresh_token')
  clearBackStack()
  try {
    await routeRequestJson<unknown>('/api/auth/registration-context/clear', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'background-refresh',
      },
    })
  } catch {
    // Completion state remains bounded server-side if acknowledgement is lost.
  }
  return true
}

async function recoverRegistrationContextAfterVerifyFailure(
  requestContext: LoginContextSnapshot,
): Promise<boolean> {
  try {
    const context = await routeRequestJson<unknown>('/api/auth/registration-context', {
      mode: 'public',
      method: 'POST',
      credentials: 'same-origin',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'background-refresh',
      },
    })
    if (!isLoginContextCurrent(requestContext)) return false
    if (isObject(context) && context.status === 'registration_complete') {
      return routeToRecoveredRegistrationCompletion()
    }
    if (isRecoveredDirectRegistrationContext(context)) {
      return routeToRegistrationContext()
    }
  } catch {
    // No authoritative cookie context means the original OTP error remains.
  }
  return false
}

async function verifyOtp() {
  if (pendingAuthenticatedLogin.value) {
    await retryAuthenticatedLoginTransition()
    return
  }
  if (!form.code || form.code.length < 4) {
    error.value = 'کد احراز هویت نامعتبر است'
    otpInput.value?.focus()
    return
  }

  if (!beginAction('verify-otp')) return
  const requestContext = captureLoginContext({ includeCode: true, includeOtpRequestId: true })
  error.value = ''

  try {
    const data = await routeRequestJson<unknown>('/api/auth/verify-otp', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mobile_number: requestContext.mobile || undefined,
        otp_request_id: requestContext.otpRequestId || undefined,
        code: requestContext.code,
        suspended_refresh_token: localStorage.getItem('suspended_refresh_token') || undefined,
      }),
      errorContext: {
        surface: 'auth',
        scope: 'field',
        operation: 'submit',
        fallbackMessage: 'کد نادرست است',
      },
    })
    if (!isLoginContextCurrent(requestContext)) return
    if (!isVerifyOtpReceipt(data)) throw new Error('پاسخ تأیید ورود کامل نیست.')
    const receiptStatus = readString(data, 'status')
    // Session management: check if approval is required
    if (receiptStatus === 'approval_required') {
      clearOtpAttempt()
      form.code = ''
      localStorage.removeItem('suspended_refresh_token')
      loginRequestId.value = readString(data, 'login_request_id')
      approvalExpiresAt.value = readString(data, 'expires_at')
      step.value = 'waiting_approval'
      startApprovalPolling()
      return
    }

    if (receiptStatus === 'registration_required') {
      await routeToRegistrationContext()
      return
    }

    if (receiptStatus === 'registration_complete') {
      await routeToRecoveredRegistrationCompletion()
      return
    }

    if (!hasLoginTokens(data)) throw new Error('پاسخ تأیید ورود کامل نیست.')
    if (await completeAuthenticatedLogin(data)) {
      clearOtpAttempt()
      form.code = ''
    }
  } catch (cause: unknown) {
    if (!isLoginContextCurrent(requestContext)) return
    if (
      (!isAppHttpError(cause) || cause.status === null || cause.status >= 500) &&
      (await recoverRegistrationContextAfterVerifyFailure(requestContext))
    ) {
      return
    }
    if (!isLoginContextCurrent(requestContext)) return
    error.value = requestErrorMessage(cause, 'کد نادرست است')
  } finally {
    finishAction('verify-otp')
  }
}

function startApprovalPolling() {
  const expiresAtMs = approvalExpiresAt.value
    ? new Date(approvalExpiresAt.value).getTime()
    : Date.now() + 120 * 1000
  approvalCountdown.value = Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000))
  if (approvalTimerInterval) clearInterval(approvalTimerInterval)
  approvalTimerInterval = setInterval(() => {
    approvalCountdown.value--
    if (approvalCountdown.value <= 0) {
      const interval = approvalTimerInterval
      if (interval) clearInterval(interval)
      stopApprovalPolling()
      error.value = 'زمان انتظار تأیید به پایان رسید. لطفاً دوباره تلاش کنید.'
      step.value = 'otp'
    }
  }, 1000)

  // Poll every 2 seconds
  if (approvalPollInterval) clearInterval(approvalPollInterval)
  approvalPollInterval = setInterval(async () => {
    if (!loginRequestId.value || approvalPollPending) return
    const requestId = loginRequestId.value
    approvalPollPending = true
    try {
      const data = await routeRequestJson<unknown>(
        `/api/sessions/login-requests/${encodeURIComponent(requestId)}/status`,
        {
          mode: 'public',
          errorContext: { surface: 'auth', scope: 'action', operation: 'background-refresh' },
        },
      )
      if (loginRequestId.value !== requestId) return
      if (!isObject(data) || typeof data.status !== 'string') return
      localStorage.removeItem('suspended_refresh_token')

      if (data.status === 'approved' && hasLoginTokens(data)) {
        stopApprovalPolling()
        await completeAuthenticatedLogin(data)
      } else if (data.status === 'rejected') {
        stopApprovalPolling()
        error.value = 'درخواست ورود شما رد شد.'
        step.value = 'otp'
      } else if (data.status === 'expired') {
        stopApprovalPolling()
        error.value = 'زمان انتظار تأیید به پایان رسید.'
        step.value = 'otp'
      }
    } catch {
      // Ignore polling errors
    } finally {
      approvalPollPending = false
    }
  }, 2000)
}

function stopApprovalPolling(preserveRequestId = false) {
  if (approvalTimerInterval) {
    clearInterval(approvalTimerInterval)
    approvalTimerInterval = null
  }
  if (approvalPollInterval) {
    clearInterval(approvalPollInterval)
    approvalPollInterval = null
  }
  if (!preserveRequestId) {
    loginRequestId.value = null
  }
}

function returnToOtpFromApproval() {
  if (pendingAuthenticatedLogin.value) return
  stopApprovalPolling()
  step.value = 'otp'
  error.value = ''
}

function stopRecoveryPolling(preserveRequestId = false) {
  if (recoveryTimerInterval) {
    clearInterval(recoveryTimerInterval)
    recoveryTimerInterval = null
  }
  if (recoveryPollInterval) {
    clearInterval(recoveryPollInterval)
    recoveryPollInterval = null
  }
  recoveryCountdown.value = 0
  if (!preserveRequestId) {
    loginRequestId.value = null
  }
}

function clearRecoveryDraft() {
  recoveryStatus.value = null
  recoveryFile.value = null
  recoveryCaption.value = ''
  recoveryApprovedTokens.value = null
  if (recoveryFileInput.value) recoveryFileInput.value.value = ''
  if (recoveryCameraInput.value) recoveryCameraInput.value.value = ''
  if (recoveryDocumentInput.value) recoveryDocumentInput.value.value = ''
}

function startRecoveryCountdown(expiresAt?: string | null) {
  if (recoveryTimerInterval) clearInterval(recoveryTimerInterval)

  const fallbackSeconds = 2 * 60 * 60
  if (expiresAt) {
    recoveryCountdown.value = Math.max(
      0,
      Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000),
    )
  } else {
    recoveryCountdown.value = fallbackSeconds
  }

  if (recoveryCountdown.value <= 0) {
    stopRecoveryPolling(true)
    step.value = 'recovery_expired'
    return
  }

  recoveryTimerInterval = setInterval(() => {
    recoveryCountdown.value--
    if (recoveryCountdown.value <= 0) {
      const interval = recoveryTimerInterval
      if (interval) clearInterval(interval)
      recoveryTimerInterval = null
      stopRecoveryPolling(true)
      step.value = 'recovery_expired'
    }
  }, 1000)
}

function parseResponseError(data: unknown, fallback: string) {
  if (isObject(data)) {
    const detail = readString(data, 'detail')
    if (detail?.trim()) return detail
  }
  return fallback
}

function applyRecoveryStatus(data: JsonObject) {
  const nextStatus = readString(data, 'status')
  recoveryStatus.value = nextStatus

  const expiresAt =
    readString(data, 'chat_action_expires_at') || readString(data, 'inline_action_expires_at')

  if (nextStatus === 'pending_admin_review') {
    step.value = 'recovery_waiting'
    startRecoveryCountdown(expiresAt)
    return
  }

  if (nextStatus === 'identity_verification_requested') {
    step.value = 'recovery_identity'
    startRecoveryCountdown(expiresAt)
    return
  }

  if (nextStatus === 'identity_submitted') {
    step.value = 'recovery_submitted'
    startRecoveryCountdown(expiresAt)
    return
  }

  if (nextStatus === 'approved') {
    stopRecoveryPolling(true)
    const accessToken = readString(data, 'access_token')
    recoveryApprovedTokens.value = accessToken
      ? {
          access_token: accessToken,
          refresh_token: readString(data, 'refresh_token'),
        }
      : null
    step.value = 'recovery_approved'
    return
  }

  if (nextStatus === 'rejected') {
    stopRecoveryPolling(true)
    step.value = 'recovery_rejected'
    return
  }

  if (nextStatus === 'expired') {
    stopRecoveryPolling(true)
    step.value = 'recovery_expired'
    return
  }

  if (nextStatus === 'cancelled') {
    invalidateLoginContext()
    stopRecoveryPolling()
    clearRecoveryDraft()
    form.code = ''
    step.value = 'mobile'
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تأیید دریافت کنید.'
  }
}

async function pollRecoveryStatusOnce() {
  if (!loginRequestId.value || recoveryPollPending) return
  const requestId = loginRequestId.value
  recoveryPollPending = true

  try {
    const data = await routeRequestJson<unknown>(
      `/api/sessions/login-requests/${encodeURIComponent(requestId)}/recovery/status`,
      {
        mode: 'public',
        errorContext: { surface: 'auth', scope: 'action', operation: 'background-refresh' },
      },
    )
    if (loginRequestId.value !== requestId) return
    if (!isRecoveryReceipt(data)) return
    applyRecoveryStatus(data)
  } catch {
    // Ignore polling errors.
  } finally {
    recoveryPollPending = false
  }
}

function startRecoveryPolling() {
  void pollRecoveryStatusOnce()
  if (recoveryPollInterval) clearInterval(recoveryPollInterval)
  recoveryPollInterval = setInterval(() => {
    void pollRecoveryStatusOnce()
  }, 2000)
}

async function startRecoveryFlow() {
  if (!loginRequestId.value || !beginAction('start-recovery')) return

  error.value = ''
  try {
    const data = await routeRequestJson<unknown>(
      `/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery`,
      {
        mode: 'public',
        method: 'POST',
        errorContext: {
          surface: 'auth',
          scope: 'action',
          operation: 'submit',
          fallbackMessage: 'شروع مسیر بازیابی ممکن نشد',
        },
      },
    )
    if (!isRecoveryReceipt(data) || data.status === 'not_started')
      throw new Error('پاسخ شروع مسیر بازیابی کامل نیست.')

    stopApprovalPolling(true)
    applyRecoveryStatus(data)
    startRecoveryPolling()
  } catch (cause: unknown) {
    error.value = requestErrorMessage(cause, 'شروع مسیر بازیابی ممکن نشد')
  } finally {
    finishAction('start-recovery')
  }
}

async function cancelRecoveryFlow() {
  if (!loginRequestId.value || !beginAction('cancel-recovery')) return

  error.value = ''
  try {
    const data = await routeRequestJson<unknown>(
      `/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery/cancel`,
      {
        mode: 'public',
        method: 'POST',
        errorContext: {
          surface: 'auth',
          scope: 'action',
          operation: 'submit',
          fallbackMessage: 'لغو درخواست بازیابی ممکن نشد',
        },
      },
    )
    const recovery = isObject(data) && isRecoveryReceipt(data.recovery) ? data.recovery : data
    if (!isRecoveryReceipt(recovery) || recovery.status !== 'cancelled') {
      throw new Error('پاسخ لغو درخواست بازیابی کامل نیست.')
    }

    stopApprovalPolling()
    stopRecoveryPolling()
    clearRecoveryDraft()
    form.code = ''
    step.value = 'mobile'
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تأیید دریافت کنید.'
  } catch (cause: unknown) {
    error.value = requestErrorMessage(cause, 'لغو درخواست بازیابی ممکن نشد')
  } finally {
    finishAction('cancel-recovery')
  }
}

function setRecoveryFile(file: File | null) {
  recoveryFile.value = file
  if (file) {
    error.value = ''
  }
}

function handleRecoveryFileInput(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0] || null
  setRecoveryFile(file)
}

function openRecoveryPicker(kind: 'gallery' | 'camera' | 'file') {
  if (kind === 'gallery') {
    recoveryFileInput.value?.click()
    return
  }
  if (kind === 'camera') {
    recoveryCameraInput.value?.click()
    return
  }
  recoveryDocumentInput.value?.click()
}

async function submitRecoveryIdentity() {
  if (!loginRequestId.value) return
  if (!recoveryFile.value) {
    error.value = 'ابتدا تصویر یا فایل مدرک را انتخاب کنید.'
    return
  }

  if (!beginAction('submit-recovery-identity')) return
  error.value = ''
  try {
    const formData = new FormData()
    formData.set('file', recoveryFile.value)
    const trimmedCaption = recoveryCaption.value.trim()
    if (trimmedCaption) {
      formData.set('caption', trimmedCaption)
    }

    const data = await routeRequestJson<unknown>(
      `/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery/identity`,
      {
        mode: 'public',
        method: 'POST',
        body: formData,
        errorContext: {
          surface: 'auth',
          scope: 'form',
          operation: 'upload',
          fallbackMessage: 'ارسال مدرک ممکن نشد',
        },
      },
    )
    const recovery = isObject(data) && isRecoveryReceipt(data.recovery) ? data.recovery : data
    if (!isRecoveryReceipt(recovery) || recovery.status !== 'identity_submitted') {
      throw new Error('پاسخ ارسال مدرک کامل نیست.')
    }

    setRecoveryFile(null)
    recoveryCaption.value = ''
    applyRecoveryStatus(recovery)
    startRecoveryPolling()
  } catch (cause: unknown) {
    error.value = requestErrorMessage(cause, 'ارسال مدرک ممکن نشد')
  } finally {
    finishAction('submit-recovery-identity')
  }
}

async function enterWithApprovedRecovery() {
  if (!beginAction('enter-approved-recovery')) return
  error.value = ''
  try {
    let tokens = recoveryApprovedTokens.value
    if (!tokens?.access_token) {
      const requestId = loginRequestId.value
      if (!requestId) throw new Error('دسترسی ورود آماده نیست. مسیر ورود را دوباره آغاز کنید.')

      const data = await routeRequestJson<unknown>(
        `/api/sessions/login-requests/${encodeURIComponent(requestId)}/recovery/status`,
        {
          mode: 'public',
          errorContext: {
            surface: 'auth',
            scope: 'action',
            operation: 'submit',
            fallbackMessage: 'دریافت دسترسی ورود ممکن نشد.',
          },
        },
      )
      if (loginRequestId.value !== requestId) return
      if (!isRecoveryReceipt(data) || data.status !== 'approved' || !hasLoginTokens(data)) {
        throw new Error('دسترسی ورود هنوز آماده نیست. دوباره تلاش کنید.')
      }
      applyRecoveryStatus(data)
      tokens = recoveryApprovedTokens.value
    }

    if (!tokens?.access_token) throw new Error('دسترسی ورود هنوز آماده نیست. دوباره تلاش کنید.')
    await completeAuthenticatedLogin(tokens)
  } catch (cause: unknown) {
    error.value = requestErrorMessage(cause, 'دریافت دسترسی ورود ممکن نشد.')
  } finally {
    finishAction('enter-approved-recovery')
  }
}

function restartLoginFlow() {
  if (pendingAuthenticatedLogin.value) {
    cancelPendingAuthenticatedLogin()
    return
  }
  invalidateLoginContext()
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  clearOtpAttempt()
  form.code = ''
  error.value = ''
  step.value = 'mobile'
}

function cancelPendingAuthenticatedLogin() {
  pendingAuthenticatedLogin.value = null
  localStorage.removeItem('auth_token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('suspended_refresh_token')
  clearCurrentUserSummary()
  clearIntendedRoute()
  invalidateLoginContext()
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  clearOtpAttempt()
  clearBackStack()
  form.mobile = ''
  form.code = ''
  error.value = ''
  step.value = 'mobile'
  void nextTick(() => mobileInput.value?.focus())
}

const authFlowTitle = computed(() => {
  if (step.value === 'mobile') return 'ورود به سامانه'
  if (step.value === 'otp') return 'کد تأیید را وارد کنید'
  if (step.value === 'waiting_approval') return 'در انتظار تأیید'
  if (step.value === 'recovery_waiting') return 'در حال بررسی مدیریت'
  if (step.value === 'recovery_identity') return 'مدرک احراز هویت'
  if (step.value === 'recovery_submitted') return 'مدرک ارسال شد'
  if (step.value === 'recovery_approved') return 'درخواست شما تأیید شد'
  if (step.value === 'recovery_rejected') return 'درخواست شما رد شد'
  return 'مهلت درخواست به پایان رسید'
})

const authFlowDescription = computed(() => {
  if (step.value === 'mobile') {
    return 'شماره موبایل ثبت‌شده را وارد کنید تا کد تأیید برای شما ارسال شود.'
  }
  if (step.value === 'otp') return 'کد ارسال‌شده را وارد کنید.'
  if (step.value === 'waiting_approval') {
    return 'درخواست ورود به دستگاه اصلی شما ارسال شد. نتیجه بدون نیاز به به‌روزرسانی دستی در همین صفحه نمایش داده می‌شود.'
  }
  if (step.value === 'recovery_waiting') {
    return 'نتیجه به‌صورت خودکار در همین صفحه اعلام می‌شود. اگر مدرکی لازم باشد، درخواست آن را همین‌جا می‌بینید.'
  }
  if (step.value === 'recovery_identity') {
    return 'تصویر کارت شناسایی یا فایل مدرک را ارسال کنید.'
  }
  if (step.value === 'recovery_submitted') {
    return 'مدرک برای بررسی ارسال شد و نتیجه به‌صورت خودکار در همین صفحه اعلام می‌شود.'
  }
  if (step.value === 'recovery_approved') {
    return 'نشست قدیمی منقضی شد و اکنون می‌توانید وارد سامانه شوید.'
  }
  if (step.value === 'recovery_rejected') {
    return 'در صورت نیاز می‌توانید دوباره درخواست بازیابی را ثبت کنید.'
  }
  return 'در مهلت تعیین‌شده پاسخی ثبت نشد. در صورت نیاز درخواست جدید ثبت کنید.'
})

const authFlowStep = computed(() => {
  if (step.value === 'mobile') return 1
  if (step.value === 'otp') return 2
  return undefined
})

const stagingDevLoginFlag = String(import.meta.env.VITE_STAGING_DEV_LOGIN ?? '')
  .trim()
  .toLowerCase()
const isDevMode =
  stagingDevLoginFlag === 'true' ||
  stagingDevLoginFlag === '1' ||
  window.location.hostname === 'localhost' ||
  window.location.hostname === '127.0.0.1' ||
  window.location.hostname.startsWith('192.168.') ||
  window.location.hostname.startsWith('172.') ||
  window.location.hostname.startsWith('10.')

async function startDevLogin() {
  if (!beginAction('dev-login')) return
  error.value = ''
  try {
    const data = await routeRequestJson<unknown>('/api/auth/dev-login', {
      mode: 'public',
      method: 'POST',
      credentials: 'include',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'submit',
        fallbackMessage: 'دسترسی مجاز نیست',
      },
    })
    if (!hasLoginTokens(data)) throw new Error('پاسخ ورود سریع کامل نیست.')
    await completeAuthenticatedLogin(data)
  } catch (cause: unknown) {
    error.value = requestErrorMessage(cause, 'دسترسی مجاز نیست')
  } finally {
    finishAction('dev-login')
  }
}

let otpVisibilityHandler: (() => void) | null = null

onMounted(() => {
  restoreOtpAttempt()
  otpVisibilityHandler = () => syncCountdown()
  document.addEventListener('visibilitychange', otpVisibilityHandler)
  mobileInput.value?.focus()
})

watch(
  () => form.code,
  (newVal, previousValue) => {
    if (newVal !== previousValue) invalidateLoginContext()
    if (newVal !== previousValue && step.value === 'otp' && error.value) error.value = ''
    if (newVal && newVal.length === 5) {
      verifyOtp()
    }
  },
  { flush: 'sync' },
)

watch(
  () => form.mobile,
  (newValue, previousValue) => {
    if (newValue !== previousValue) invalidateLoginContext()
    if (newValue !== previousValue && step.value === 'mobile' && error.value) error.value = ''
  },
  { flush: 'sync' },
)

let ac: AbortController | null = null
type WebOtpCredential = Credential & { code: string }

function hasWebOtpCode(value: Credential | null): value is WebOtpCredential {
  return Boolean(value) && typeof (value as { code?: unknown }).code === 'string'
}

async function initWebOtp() {
  if ('OTPCredential' in window) {
    if (ac) ac.abort()
    ac = new AbortController()

    try {
      const content = await navigator.credentials.get({
        otp: { transport: ['sms'] },
        signal: ac.signal,
      } as unknown as CredentialRequestOptions)

      if (hasWebOtpCode(content)) {
        form.code = content.code
        verifyOtp()
      }
    } catch {
      // WebOTP cancellation/failure is expected. Never log credential values or
      // provider errors because they may embed the one-time code.
    }
  }
}

watch(
  () => step.value,
  async (newStep) => {
    await nextTick()
    if (newStep === 'otp') {
      otpInput.value?.focus()
      // Small delay to ensure view transition
      setTimeout(() => {
        initWebOtp()
      }, 100)
    } else {
      if (newStep === 'mobile') mobileInput.value?.focus()
      else statusStepContainer.value?.focus()
      if (ac) {
        ac.abort()
        ac = null
      }
    }
  },
)

onUnmounted(() => {
  if (ac) ac.abort()
  if (timerInterval) clearInterval(timerInterval)
  if (otpVisibilityHandler) document.removeEventListener('visibilitychange', otpVisibilityHandler)
  stopApprovalPolling()
  stopRecoveryPolling()
  clearBackStack()
})

// Back to mobile step (UI-initiated via "ویرایش شماره" button)
function goBackToMobile() {
  if (pendingAuthenticatedLogin.value) return
  invalidateLoginContext()
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  clearOtpAttempt()
  form.code = ''
  step.value = 'mobile'
  error.value = ''
  popBackState()
}
</script>

<template>
  <AuthFlowShell
    fill-viewport
    :title="authFlowTitle"
    :description="authFlowDescription"
    :current-step="authFlowStep"
    :total-steps="authFlowStep ? 2 : undefined"
  >
    <transition name="ui-v2-auth-state" mode="out-in">
      <section
        v-if="step === 'mobile'"
        key="mobile"
        class="ui-v2-auth-login-step"
        aria-live="polite"
      >
        <div
          v-if="completedRegistrationNotice"
          class="ui-v2-auth-login-note ui-v2-auth-login-note--success"
          role="status"
        >
          <strong>ثبت‌نام قبلاً تکمیل شده است</strong>
          <span>برای ورود به وب‌اپ، کد تأیید دریافت کنید.</span>
        </div>

        <div
          v-if="localLogoutNotice"
          class="ui-v2-auth-login-note"
          :class="{
            'ui-v2-auth-login-note--success': localLogoutNotice === 'server-confirmed',
          }"
          role="status"
          data-local-logout-notice
        >
          <strong>
            {{
              localLogoutNotice === 'server-confirmed'
                ? 'خروج این دستگاه ثبت شد'
                : 'اطلاعات ورود این دستگاه پاک شد'
            }}
          </strong>
          <span>
            {{
              localLogoutNotice === 'server-confirmed'
                ? 'نشست این دستگاه روی سرور پایان یافت.'
                : 'تأیید سرور دریافت نشد؛ اطلاعات ورود فقط از این دستگاه پاک شد.'
            }}
          </span>
        </div>

        <AppFormField label="شماره موبایل" :error="mobileFieldError">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              ref="mobileInput"
              v-model="form.mobile"
              class="ui-v2-auth-login-input--ltr"
              type="tel"
              inputmode="tel"
              dir="ltr"
              placeholder="0912 345 6789"
              autocomplete="tel"
              :aria-describedby="describedby"
              :invalid="invalid"
              autofocus
            />
          </template>
        </AppFormField>

        <AppButton block :loading="loading" @click="requestOtp">
          {{ countdown > 0 ? 'وارد کردن کد' : 'دریافت کد تأیید' }}
        </AppButton>

        <div v-if="countdown > 0" class="ui-v2-auth-login-timer" role="status">
          <Clock :size="16" aria-hidden="true" />
          <bdi>{{ formattedTimer }}</bdi>
        </div>

        <div class="ui-v2-auth-login-guidance">
          <strong>دریافت کد</strong>
          <p>کد ابتدا در تلگرام و در صورت نیاز به‌صورت خودکار با پیامک ارسال می‌شود.</p>
        </div>

        <AppButton v-if="isDevMode" block size="sm" variant="secondary" @click="startDevLogin">
          ورود سریع ۱ ساله
        </AppButton>
      </section>

      <section
        v-else-if="step === 'otp'"
        key="otp"
        class="ui-v2-auth-login-step"
        aria-live="polite"
      >
        <div class="ui-v2-auth-login-meta">
          <bdi v-if="maskedMobileForDisplay" dir="ltr">{{ maskedMobileForDisplay }}</bdi>
          <span v-else>تلاش فعال ورود</span>
          <button
            type="button"
            class="ui-v2-auth-login-link"
            :disabled="Boolean(pendingAuthenticatedLogin)"
            @click="goBackToMobile"
          >
            ویرایش شماره
          </button>
        </div>

        <AppFormField label="کد تأیید" :error="otpFieldError">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              ref="otpInput"
              v-model="form.code"
              class="ui-v2-auth-login-input--code"
              type="text"
              inputmode="numeric"
              pattern="[0-9]*"
              maxlength="5"
              dir="ltr"
              autocomplete="one-time-code"
              placeholder="_____"
              :aria-describedby="describedby"
              :invalid="invalid"
              :disabled="Boolean(pendingAuthenticatedLogin)"
              autofocus
            />
          </template>
        </AppFormField>

        <div
          v-if="countdown > 0 || automaticSmsFallback"
          class="ui-v2-auth-login-delivery"
          role="status"
          aria-live="polite"
        >
          <Clock :size="16" aria-hidden="true" />
          <span>{{ otpDeliveryStatus }}</span>
        </div>
        <button
          v-else
          type="button"
          class="ui-v2-auth-login-link"
          :disabled="Boolean(pendingAuthenticatedLogin)"
          @click="handleResend"
        >
          ارسال مجدد کد
        </button>

        <AppButton block :loading="loading" @click="verifyOtp">
          {{ pendingAuthenticatedLogin ? 'ادامه ورود' : 'تأیید و ادامه' }}
        </AppButton>
      </section>

      <section
        v-else-if="step === 'waiting_approval'"
        ref="statusStepContainer"
        key="waiting"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <Loader2 class="ui-v2-auth-login-spinner" :size="28" aria-hidden="true" />
        <div v-if="approvalCountdown > 0" class="ui-v2-auth-login-countdown">
          <strong>{{ formattedApprovalTimer }}</strong>
          <span>مهلت باقی‌مانده برای تأیید</span>
        </div>
        <AppButton
          block
          variant="secondary"
          :disabled="loading || Boolean(pendingAuthenticatedLogin)"
          @click="startRecoveryFlow"
        >
          به دستگاه قبلی دسترسی ندارم
        </AppButton>
        <button
          type="button"
          class="ui-v2-auth-login-link"
          :disabled="Boolean(pendingAuthenticatedLogin)"
          @click="returnToOtpFromApproval"
        >
          بازگشت به مرحله کد تأیید
        </button>
      </section>

      <section
        v-else-if="step === 'recovery_waiting'"
        ref="statusStepContainer"
        key="recovery-waiting"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <Loader2 class="ui-v2-auth-login-spinner" :size="28" aria-hidden="true" />
        <div v-if="recoveryCountdown > 0" class="ui-v2-auth-login-countdown">
          <strong>{{ formattedRecoveryTimer }}</strong>
          <span>مهلت بررسی این درخواست</span>
        </div>
        <button
          type="button"
          class="ui-v2-auth-login-link ui-v2-auth-login-link--danger"
          @click="cancelRecoveryFlow"
        >
          انصراف از درخواست بازیابی
        </button>
      </section>

      <section
        v-else-if="step === 'recovery_identity'"
        ref="statusStepContainer"
        key="recovery-identity"
        data-auth-status-step
        class="ui-v2-auth-login-step"
        tabindex="-1"
        aria-live="polite"
      >
        <div v-if="recoveryCountdown > 0" class="ui-v2-auth-login-meta">
          <span>مهلت ارسال مدرک</span>
          <strong>{{ formattedRecoveryTimer }}</strong>
        </div>

        <div class="ui-v2-auth-login-picker-grid" aria-label="انتخاب منبع مدرک">
          <AppButton variant="secondary" @click="openRecoveryPicker('gallery')">گالری</AppButton>
          <AppButton variant="secondary" @click="openRecoveryPicker('camera')">دوربین</AppButton>
          <AppButton variant="secondary" @click="openRecoveryPicker('file')">فایل</AppButton>
        </div>

        <div class="ui-v2-auth-login-upload" role="status">
          <FileCheck2 :size="20" aria-hidden="true" />
          <span v-if="selectedRecoveryFileName">{{ selectedRecoveryFileName }}</span>
          <span v-else>هنوز فایلی انتخاب نشده است</span>
        </div>

        <AppFormField label="توضیح اختیاری">
          <template #default="{ id, describedby }">
            <AppTextarea
              :id="id"
              v-model="recoveryCaption"
              rows="3"
              placeholder="توضیح اختیاری"
              :aria-describedby="describedby"
            />
          </template>
        </AppFormField>

        <p class="ui-v2-auth-login-privacy">
          مدرک برای بررسی این درخواست در اختیار بررسی‌کنندگان مجاز بازیابی قرار می‌گیرد.
        </p>

        <AppButton block :loading="loading" @click="submitRecoveryIdentity">
          ارسال مدرک برای بررسی
        </AppButton>
        <button
          type="button"
          class="ui-v2-auth-login-link ui-v2-auth-login-link--danger"
          @click="cancelRecoveryFlow"
        >
          انصراف از درخواست بازیابی
        </button>
      </section>

      <section
        v-else-if="step === 'recovery_submitted'"
        ref="statusStepContainer"
        key="recovery-submitted"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <FileCheck2 :size="32" aria-hidden="true" />
        <div v-if="recoveryCountdown > 0" class="ui-v2-auth-login-countdown">
          <strong>{{ formattedRecoveryTimer }}</strong>
          <span>مهلت بررسی این درخواست</span>
        </div>
      </section>

      <section
        v-else-if="step === 'recovery_approved'"
        ref="statusStepContainer"
        key="recovery-approved"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <FileCheck2 :size="32" aria-hidden="true" />
        <AppButton block :loading="loading" @click="enterWithApprovedRecovery">
          ورود به سامانه
        </AppButton>
      </section>

      <section
        v-else-if="step === 'recovery_rejected'"
        ref="statusStepContainer"
        key="recovery-rejected"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <AppButton block @click="restartLoginFlow">شروع دوباره</AppButton>
      </section>

      <section
        v-else
        ref="statusStepContainer"
        key="recovery-expired"
        data-auth-status-step
        class="ui-v2-auth-login-step ui-v2-auth-login-step--status"
        tabindex="-1"
        aria-live="polite"
      >
        <Clock :size="32" aria-hidden="true" />
        <AppButton block @click="restartLoginFlow">شروع دوباره</AppButton>
      </section>
    </transition>

    <transition name="ui-v2-auth-fade">
      <div v-if="processError" class="ui-v2-auth-login-error" role="alert">
        <span>{{ processError }}</span>
        <AppButton
          v-if="pendingAuthenticatedLogin && step !== 'otp'"
          size="sm"
          :loading="loading"
          @click="retryAuthenticatedLoginTransition"
        >
          ادامه ورود
        </AppButton>
        <AppButton
          v-if="pendingAuthenticatedLogin"
          variant="secondary"
          size="sm"
          :disabled="loading"
          @click="cancelPendingAuthenticatedLogin"
        >
          ورود با حساب دیگر
        </AppButton>
        <div v-if="canOfferAppRecovery" class="ui-v2-auth-login-error__actions">
          <span>اگر بارگذاری برنامه متوقف شده است، بازنشانی امن را اجرا کنید.</span>
          <AppButton variant="danger" size="sm" @click="startAppRecovery">
            پاک‌سازی کش برنامه و بارگذاری مجدد
          </AppButton>
        </div>
      </div>
    </transition>

    <input
      ref="recoveryFileInput"
      type="file"
      accept="image/*"
      class="ui-v2-auth-login-hidden-input"
      @change="handleRecoveryFileInput"
    />
    <input
      ref="recoveryCameraInput"
      type="file"
      accept="image/*"
      capture="environment"
      class="ui-v2-auth-login-hidden-input"
      @change="handleRecoveryFileInput"
    />
    <input
      ref="recoveryDocumentInput"
      type="file"
      accept="image/*,.pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      class="ui-v2-auth-login-hidden-input"
      @change="handleRecoveryFileInput"
    />
  </AuthFlowShell>
</template>
