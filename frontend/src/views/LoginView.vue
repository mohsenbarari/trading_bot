<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Smartphone, Lock, Loader2, Download, Clock, CheckCircle2, XCircle, ShieldCheck } from 'lucide-vue-next'
import { setupExpiryTimer } from '../utils/auth'
import { primeCurrentUserSummary } from '../utils/currentUser'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { routeRequestJson } from '../utils/routeRequest'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import { AppButton, AppFormField, AppInput, AppPage, AppSectionCard, AppStatusBadge, AppTextarea } from '../components/ui'

const router = useRouter()
const route = useRoute()
const completedRegistrationNotice = computed(() => route.query.registration === 'complete')
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

function isIOSUserAgent(userAgent: string) {
  const normalized = userAgent.toLowerCase()
  return /iphone|ipad|ipod/.test(normalized)
}

function isInAppBrowser(userAgent: string) {
  const normalized = userAgent.toLowerCase()
  return /instagram|fbav|fban|telegram|line|micromessenger|; wv\)/.test(normalized)
}

function isLikelyBeforeInstallPromptBrowser(userAgent: string) {
  const normalized = userAgent.toLowerCase()
  if (isIOSUserAgent(normalized) || isInAppBrowser(normalized)) return false
  return /chrome|chromium|crmo|edg\/|edga|opr\/|opera|samsungbrowser/.test(normalized)
}

const initialUserAgent = typeof window !== 'undefined' ? window.navigator.userAgent.toLowerCase() : ''

const step = ref<LoginStep>('mobile')
const loading = ref(false)
const error = ref('')
const isStandalone = ref(false)
const supportsNativeInstallPrompt = ref(isLikelyBeforeInstallPromptBrowser(initialUserAgent))
const showManualInstallGuide = ref(isIOSUserAgent(initialUserAgent))
const isIOS = ref(isIOSUserAgent(initialUserAgent))
const isInstalled = ref(false)
const isInstallButtonDelayElapsed = ref(false)
let installButtonDelayTimer: number | null = null

// OTP Timer State
const countdown = ref(0)
let timerInterval: any = null
let countdownDeadlineMs: number | null = null
const otpRequestId = ref<string | null>(null)
const otpExpiresAt = ref<string | null>(null)
const smsFallbackAt = ref<string | null>(null)
const legacySmsResendAt = ref<string | null>(null)
const legacyManualSmsResend = ref(false)
const OTP_ATTEMPT_SESSION_KEY = 'login_otp_attempt_v1'

const form = reactive({
  mobile: '',
  code: ''
})

// Session approval state
const loginRequestId = ref<string | null>(null)
const approvalExpiresAt = ref<string | null>(null)
const approvalCountdown = ref(0)
let approvalTimerInterval: any = null
let approvalPollInterval: any = null

// Recovery flow state
const recoveryStatus = ref<string | null>(null)
const recoveryCountdown = ref(0)
const recoveryFile = ref<File | null>(null)
const recoveryCaption = ref('')
const recoveryApprovedTokens = ref<{ access_token: string; refresh_token?: string | null } | null>(null)
const recoveryFileInput = ref<HTMLInputElement | null>(null)
const recoveryCameraInput = ref<HTMLInputElement | null>(null)
const recoveryDocumentInput = ref<HTMLInputElement | null>(null)
let recoveryTimerInterval: any = null
let recoveryPollInterval: any = null
type LoginActionKey =
  | 'request-otp'
  | 'resend-otp'
  | 'verify-otp'
  | 'start-recovery'
  | 'cancel-recovery'
  | 'submit-recovery-identity'
  | 'enter-approved-recovery'
  | 'dev-login'
const activeActions = new Set<LoginActionKey>()
let approvalPollPending = false
let recoveryPollPending = false
let loginContextEpoch = 0

type JsonObject = Record<string, unknown>
type LoginTokenReceipt = JsonObject & { access_token: string; refresh_token?: string | null }
type VerifyOtpReceipt = LoginTokenReceipt | (
  JsonObject & {
    status: 'approval_required' | 'registration_required'
    login_request_id?: string
    registration_token?: string
    expires_at?: string
  }
)
type RecoveryReceipt = JsonObject & { status: string }

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
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

function captureLoginContext(options: { includeCode?: boolean; includeOtpRequestId?: boolean } = {}): LoginContextSnapshot {
  return {
    epoch: loginContextEpoch,
    step: step.value,
    mobile: form.mobile,
    ...(options.includeCode ? { code: form.code } : {}),
    ...(options.includeOtpRequestId ? { otpRequestId: otpRequestId.value } : {}),
  }
}

function isLoginContextCurrent(snapshot: LoginContextSnapshot) {
  return snapshot.epoch === loginContextEpoch
    && snapshot.step === step.value
    && snapshot.mobile === form.mobile
    && (snapshot.code === undefined || snapshot.code === form.code)
    && (snapshot.otpRequestId === undefined || snapshot.otpRequestId === otpRequestId.value)
}

function hasLoginTokens(value: unknown): value is LoginTokenReceipt {
  return isObject(value)
    && typeof value.access_token === 'string'
    && Boolean(value.access_token.trim())
    && (value.refresh_token === undefined || value.refresh_token === null || typeof value.refresh_token === 'string')
}

function isOtpDeliveryReceipt(value: unknown): value is JsonObject {
  if (!isObject(value)) return false
  if (value.method === 'log') {
    const hasDetail = typeof value.detail === 'string' && Boolean(value.detail.trim())
    const hasExpiry = (typeof value.expires_in === 'number' && Number.isFinite(value.expires_in) && value.expires_in > 0)
      || (typeof value.expires_at === 'string' && Boolean(value.expires_at.trim()))
    return hasDetail && hasExpiry
  }
  return value.method === 'telegram'
    || value.method === 'sms'
    || (typeof value.otp_request_id === 'string' && Boolean(value.otp_request_id.trim()))
}

function isVerifyOtpReceipt(value: unknown): value is VerifyOtpReceipt {
  if (hasLoginTokens(value)) return true
  if (!isObject(value) || typeof value.status !== 'string') return false
  if (value.status === 'approval_required') {
    return typeof value.login_request_id === 'string' && Boolean(value.login_request_id.trim())
  }
  if (value.status === 'registration_required') {
    return typeof value.registration_token === 'string' && Boolean(value.registration_token.trim())
  }
  return false
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
  return isObject(value)
    && typeof value.status === 'string'
    && knownRecoveryStatuses.has(value.status)
}

async function completeAuthenticatedLogin(data: { access_token: string; refresh_token?: string | null }) {
  localStorage.setItem('auth_token', data.access_token)
  if (data.refresh_token) {
    localStorage.setItem('refresh_token', data.refresh_token)
  }
  localStorage.removeItem('suspended_refresh_token')

  try {
    await primeCurrentUserSummary(true)
  } catch {
    // Do not block the login transition on a best-effort current-user prefetch.
  }

  setupExpiryTimer()
  clearBackStack()
  router.push('/')
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

function startTimer(seconds: number) {
  startTimerUntil(Date.now() + Math.max(0, Number(seconds) || 0) * 1000)
}

function persistOtpAttempt() {
  if (!otpRequestId.value || !otpExpiresAt.value) return
  try {
    sessionStorage.setItem(OTP_ATTEMPT_SESSION_KEY, JSON.stringify({
      requestId: otpRequestId.value,
      method: lastMethod.value,
      expiresAt: otpExpiresAt.value,
      smsFallbackAt: smsFallbackAt.value,
    }))
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

function applyOtpTiming(data: any) {
  otpRequestId.value = typeof data?.otp_request_id === 'string' ? data.otp_request_id : null
  lastMethod.value = data?.method === 'telegram' || data?.method === 'sms' ? data.method : null
  otpExpiresAt.value = typeof data?.expires_at === 'string'
    ? data.expires_at
    : new Date(Date.now() + Math.max(0, Number(data?.expires_in) || 0) * 1000).toISOString()
  smsFallbackAt.value = typeof data?.sms_fallback_at === 'string'
    ? data.sms_fallback_at
    : null
  legacyManualSmsResend.value = data?.manual_sms_resend === true || (
    lastMethod.value === 'telegram' && !smsFallbackAt.value
  )
  legacySmsResendAt.value = (
    legacyManualSmsResend.value
      ? (
          typeof data?.legacy_sms_resend_at === 'string'
            ? data.legacy_sms_resend_at
            : new Date(Date.now() + 30_000).toISOString()
        )
      : null
  )
  const displayDeadline = lastMethod.value === 'telegram' && smsFallbackAt.value
    ? smsFallbackAt.value
    : (legacySmsResendAt.value || otpExpiresAt.value)
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
    const displayDeadline = lastMethod.value === 'telegram' && smsFallbackAt.value
      ? smsFallbackAt.value
      : otpExpiresAt.value
    startTimerUntil(displayDeadline)
  } catch {
    clearOtpAttempt()
  }
}



const formattedTimer = computed(() => {
  const m = Math.floor(countdown.value / 60).toString().padStart(2, '0')
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

const lastMethod = ref<'telegram' | 'sms' | null>(null)
const automaticSmsFallback = computed(() => (
  lastMethod.value === 'telegram' && Boolean(smsFallbackAt.value)
))
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
  nextUrl.searchParams.set('app_recovery', Date.now().toString())
  window.location.replace(nextUrl.toString())
}

function goToOtpStep() {
  if (step.value === 'otp') return
  step.value = 'otp'
  pushBackState(() => {
    invalidateLoginContext()
    clearOtpAttempt()
    form.code = ''
    step.value = 'mobile'
    error.value = ''
  })
}

async function requestOtp() {
  if (!form.mobile || form.mobile.length < 10) {
    error.value = 'شماره موبایل معتبر نیست'
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
      headers: {'Content-Type': 'application/json'},
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
      headers: {'Content-Type': 'application/json'},
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
    applyOtpTiming({ ...data, method: 'sms', otp_request_id: data.otp_request_id || otpRequestId.value })

    
  } catch (cause: unknown) {
    if (!isLoginContextCurrent(requestContext)) return
    error.value = requestErrorMessage(cause, 'خطا در ارسال پیامک')
  } finally {
    finishAction('resend-otp')
  }
}

function handleResend() {
    if (automaticSmsFallback.value) return
    if (legacyManualSmsResend.value || lastMethod.value === 'telegram') {
        resendOtpSms()
        return
    }
    requestOtp()
}


async function verifyOtp() {
  if (!form.code || form.code.length < 4) {
    error.value = 'کد احراز هویت نامعتبر است'
    return
  }

  if (!beginAction('verify-otp')) return
  const requestContext = captureLoginContext({ includeCode: true, includeOtpRequestId: true })
  error.value = ''

  try {
    const data = await routeRequestJson<unknown>('/api/auth/verify-otp', {
      mode: 'public',
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        mobile_number: requestContext.mobile || undefined,
        otp_request_id: requestContext.otpRequestId || undefined,
        code: requestContext.code,
        suspended_refresh_token: localStorage.getItem("suspended_refresh_token") || undefined,
      }),
      errorContext: {
        surface: 'auth',
        scope: 'field',
        operation: 'submit',
        fallbackMessage: 'کد نادرست است',
      },
    })
    if (!isLoginContextCurrent(requestContext)) return
    if (!isVerifyOtpReceipt(data)) throw new Error('پاسخ تایید ورود کامل نیست.')
    clearOtpAttempt()
    localStorage.removeItem('suspended_refresh_token')

    // Session management: check if approval is required
    if (data.status === 'approval_required') {
      loginRequestId.value = data.login_request_id
      approvalExpiresAt.value = data.expires_at
      step.value = 'waiting_approval'
      startApprovalPolling()
      return
    }

    if (data.status === 'registration_required' && data.registration_token) {
      clearBackStack()
      router.push(`/register?registration_token=${encodeURIComponent(data.registration_token)}`)
      return
    }
    
    await completeAuthenticatedLogin(data)
  } catch (cause: unknown) {
    if (!isLoginContextCurrent(requestContext)) return
    error.value = requestErrorMessage(cause, 'کد نادرست است')
  } finally {
    finishAction('verify-otp')
  }
}

function startApprovalPolling() {
  const expiresAtMs = approvalExpiresAt.value ? new Date(approvalExpiresAt.value).getTime() : Date.now() + (120 * 1000)
  approvalCountdown.value = Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000))
  if (approvalTimerInterval) clearInterval(approvalTimerInterval)
  approvalTimerInterval = setInterval(() => {
    approvalCountdown.value--
    if (approvalCountdown.value <= 0) {
      clearInterval(approvalTimerInterval)
      stopApprovalPolling()
      error.value = 'زمان انتظار تایید به پایان رسید. لطفاً دوباره تلاش کنید.'
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
      const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(requestId)}/status`, {
        mode: 'public',
        errorContext: { surface: 'auth', scope: 'action', operation: 'background-refresh' },
      })
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
        error.value = 'زمان انتظار تایید به پایان رسید.'
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
  if (approvalTimerInterval) { clearInterval(approvalTimerInterval); approvalTimerInterval = null }
  if (approvalPollInterval) { clearInterval(approvalPollInterval); approvalPollInterval = null }
  if (!preserveRequestId) {
    loginRequestId.value = null
  }
}

function stopRecoveryPolling(preserveRequestId = false) {
  if (recoveryTimerInterval) { clearInterval(recoveryTimerInterval); recoveryTimerInterval = null }
  if (recoveryPollInterval) { clearInterval(recoveryPollInterval); recoveryPollInterval = null }
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
    recoveryCountdown.value = Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000))
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
      clearInterval(recoveryTimerInterval)
      recoveryTimerInterval = null
      stopRecoveryPolling(true)
      step.value = 'recovery_expired'
    }
  }, 1000)
}

function parseResponseError(data: any, fallback: string) {
  if (data && typeof data.detail === 'string' && data.detail.trim()) {
    return data.detail
  }
  return fallback
}

function applyRecoveryStatus(data: any) {
  const nextStatus = typeof data?.status === 'string' ? data.status : null
  recoveryStatus.value = nextStatus

  const expiresAt = typeof data?.chat_action_expires_at === 'string'
    ? data.chat_action_expires_at
    : (typeof data?.inline_action_expires_at === 'string' ? data.inline_action_expires_at : null)

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
    recoveryApprovedTokens.value = data?.access_token
      ? {
          access_token: data.access_token,
          refresh_token: data.refresh_token || null,
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
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تایید دریافت کنید.'
  }
}

async function pollRecoveryStatusOnce() {
  if (!loginRequestId.value || recoveryPollPending) return
  const requestId = loginRequestId.value
  recoveryPollPending = true

  try {
    const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(requestId)}/recovery/status`, {
      mode: 'public',
      errorContext: { surface: 'auth', scope: 'action', operation: 'background-refresh' },
    })
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
    const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery`, {
      mode: 'public',
      method: 'POST',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'submit',
        fallbackMessage: 'شروع مسیر بازیابی ممکن نشد',
      },
    })
    if (!isRecoveryReceipt(data) || data.status === 'not_started') throw new Error('پاسخ شروع مسیر بازیابی کامل نیست.')

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
    const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery/cancel`, {
      mode: 'public',
      method: 'POST',
      errorContext: {
        surface: 'auth',
        scope: 'action',
        operation: 'submit',
        fallbackMessage: 'لغو درخواست بازیابی ممکن نشد',
      },
    })
    const recovery = isObject(data) && isRecoveryReceipt(data.recovery) ? data.recovery : data
    if (!isRecoveryReceipt(recovery) || recovery.status !== 'cancelled') {
      throw new Error('پاسخ لغو درخواست بازیابی کامل نیست.')
    }

    stopApprovalPolling()
    stopRecoveryPolling()
    clearRecoveryDraft()
    form.code = ''
    step.value = 'mobile'
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تایید دریافت کنید.'
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

    const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(loginRequestId.value)}/recovery/identity`, {
      mode: 'public',
      method: 'POST',
      body: formData,
      errorContext: {
        surface: 'auth',
        scope: 'form',
        operation: 'upload',
        fallbackMessage: 'ارسال مدرک ممکن نشد',
      },
    })
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

      const data = await routeRequestJson<unknown>(`/api/sessions/login-requests/${encodeURIComponent(requestId)}/recovery/status`, {
        mode: 'public',
        errorContext: {
          surface: 'auth',
          scope: 'action',
          operation: 'submit',
          fallbackMessage: 'دریافت دسترسی ورود ممکن نشد.',
        },
      })
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
  invalidateLoginContext()
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  clearOtpAttempt()
  form.code = ''
  error.value = ''
  step.value = 'mobile'
}

const shouldShowInstallEntry = computed(() => (
  isInstallButtonDelayElapsed.value &&
  !isStandalone.value &&
  !isInstalled.value &&
  (isIOS.value || !supportsNativeInstallPrompt.value)
))

const manualInstallGuideTitle = computed(() => (
  isIOS.value ? 'راهنمای نصب در آیفون' : 'راهنمای نصب دستی'
))

async function installPWA() {
  const deferredPrompt = (window as any).deferredPrompt
  if (supportsNativeInstallPrompt.value && !isIOS.value && deferredPrompt?.prompt) {
    showManualInstallGuide.value = false
    try {
      deferredPrompt.prompt()
      const result = await deferredPrompt.userChoice
      if (result?.outcome === 'accepted') {
        isInstalled.value = true
        return
      }
    } catch {
      // Fall back to manual instructions below when the browser prompt fails.
    } finally {
      ;(window as any).deferredPrompt = null
    }
  }

  showManualInstallGuide.value = true
}

const stagingDevLoginFlag = String(import.meta.env.VITE_STAGING_DEV_LOGIN ?? '').trim().toLowerCase()
const isDevMode = stagingDevLoginFlag === 'true' ||
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

let beforeInstallPromptHandler: ((event: Event) => void) | null = null
let pwaInstallReadyHandler: (() => void) | null = null
let appInstalledHandler: (() => void) | null = null
let otpVisibilityHandler: (() => void) | null = null

onMounted(() => {
  restoreOtpAttempt()
  otpVisibilityHandler = () => syncCountdown()
  document.addEventListener('visibilitychange', otpVisibilityHandler)

  const isStandaloneDisplay = typeof window.matchMedia === 'function'
    && window.matchMedia('(display-mode: standalone)').matches
  if (isStandaloneDisplay || (window.navigator as any).standalone === true) {
    isStandalone.value = true
  }

  const userAgent = window.navigator.userAgent.toLowerCase()
  isIOS.value = isIOSUserAgent(userAgent)
  supportsNativeInstallPrompt.value = isLikelyBeforeInstallPromptBrowser(userAgent)
  showManualInstallGuide.value = isIOS.value

  beforeInstallPromptHandler = (e: Event) => {
    e.preventDefault()
    ;(window as any).deferredPrompt = e
    supportsNativeInstallPrompt.value = true
    showManualInstallGuide.value = false
  }
  window.addEventListener('beforeinstallprompt', beforeInstallPromptHandler)
  
  pwaInstallReadyHandler = () => {
    if ((window as any).deferredPrompt && !isIOS.value) {
      supportsNativeInstallPrompt.value = true
      showManualInstallGuide.value = false
    }
  }
  window.addEventListener('pwa-install-ready', pwaInstallReadyHandler)
  
  appInstalledHandler = () => {
    isInstalled.value = true
  }
  window.addEventListener('appinstalled', appInstalledHandler)
  
  if ((window as any).deferredPrompt && !isIOS.value) {
    supportsNativeInstallPrompt.value = true
    showManualInstallGuide.value = false
  }

  installButtonDelayTimer = window.setTimeout(() => {
    isInstallButtonDelayElapsed.value = true
  }, 4000)
})

watch(() => form.code, (newVal, previousValue) => {
  if (newVal !== previousValue) invalidateLoginContext()
  if (newVal && newVal.length === 5) {
    verifyOtp()
  }
}, { flush: 'sync' })

watch(() => form.mobile, (newValue, previousValue) => {
  if (newValue !== previousValue) invalidateLoginContext()
}, { flush: 'sync' })

let ac: AbortController | null = null;

async function initWebOtp() {
  if ('OTPCredential' in window) {
    if (ac) ac.abort();
    ac = new AbortController();
    
    try {
      const content = await navigator.credentials.get({
        otp: { transport: ['sms'] },
        signal: ac.signal
      } as any);

      if (content && (content as any).code) {
        form.code = (content as any).code;
        verifyOtp();
      }
    } catch (err) {
      console.log('Web OTP Error:', err);
    }
  }
}

watch(() => step.value, (newStep) => {
  if (newStep === 'otp') {
    // Small delay to ensure view transition
    setTimeout(() => {
        initWebOtp();
    }, 100);
  } else {
    if (ac) {
      ac.abort();
      ac = null;
    }
  }
});

onUnmounted(() => {
  if (ac) ac.abort();
  if (timerInterval) clearInterval(timerInterval)
  if (installButtonDelayTimer !== null) window.clearTimeout(installButtonDelayTimer)
  if (beforeInstallPromptHandler) window.removeEventListener('beforeinstallprompt', beforeInstallPromptHandler)
  if (pwaInstallReadyHandler) window.removeEventListener('pwa-install-ready', pwaInstallReadyHandler)
  if (appInstalledHandler) window.removeEventListener('appinstalled', appInstalledHandler)
  if (otpVisibilityHandler) document.removeEventListener('visibilitychange', otpVisibilityHandler)
  stopApprovalPolling()
  stopRecoveryPolling()
  clearBackStack()
})

// Back to mobile step (UI-initiated via "ویرایش شماره" button)
function goBackToMobile() {
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
  <AppPage narrow>
    <section class="login-view">
      <AppSectionCard
        class="login-card"
        title="ورود به بازار"
        description="ورود و بازیابی نشست از همین صفحه انجام می‌شود."
      >
        <template #actions>
          <AppStatusBadge tone="warning">نسخه وب</AppStatusBadge>
        </template>

        <div class="login-brand-mark" aria-hidden="true">
          <ShieldCheck :size="24" />
        </div>

        <transition name="slide-up" mode="out-in">
          <div v-if="step === 'mobile'" key="mobile" class="login-step">
            <div v-if="completedRegistrationNotice" class="login-note-card" role="status">
              <p class="login-note-title">ثبت‌نام قبلاً تکمیل شده است</p>
              <p>برای ورود به وب‌اپ، کد تایید دریافت کنید.</p>
            </div>
            <AppFormField label="شماره موبایل">
              <template #default="{ id }">
                <div class="login-field-shell">
                  <Smartphone class="login-field-icon" :size="18" />
                  <AppInput
                    :id="id"
                    v-model="form.mobile"
                    type="tel"
                    aria-label="شماره موبایل"
                    dir="ltr"
                    placeholder="0912..."
                    autocomplete="tel"
                    class="login-input login-input--ltr"
                  />
                </div>
              </template>
            </AppFormField>

            <AppButton
              block
              :loading="loading"
              @click="requestOtp"
            >
              {{ countdown > 0 ? 'وارد کردن کد' : 'دریافت کد تایید' }}
            </AppButton>

            <div v-if="countdown > 0" class="login-timer">
              <Clock :size="14" />
              <bdi>{{ formattedTimer }}</bdi>
            </div>

            <div v-if="shouldShowInstallEntry" class="login-install">
              <AppButton block variant="secondary" @click="installPWA">
                <template #icon>
                  <Download :size="16" />
                </template>
                نصب اپلیکیشن
              </AppButton>

              <div v-if="showManualInstallGuide" class="login-note-card">
                <p class="login-note-title">{{ manualInstallGuideTitle }}</p>
                <template v-if="isIOS">
                  <p>برای نصب در iPhone یا iPad، سایت را در Safari باز کنید و سپس از منوی Share گزینه Add to Home Screen را بزنید. از آیکن Gold روی Home Screen وارد شوید.</p>
                </template>
                <template v-else>
                  <p>اگر نصب مستقیم فعال نبود، سایت را با Chrome یا Edge باز کنید و از منوی مرورگر گزینه Add to Home Screen یا Install App را بزنید.</p>
                </template>
              </div>
            </div>
          </div>

          <div v-else-if="step === 'otp'" key="otp" class="login-step">
            <div class="login-step-meta">
              <span v-if="form.mobile">کد ارسال شده به {{ form.mobile }}</span>
              <button type="button" class="login-link-btn" @click="goBackToMobile()">ویرایش شماره</button>
            </div>

            <AppFormField label="کد تایید">
              <template #default="{ id }">
                <div class="login-field-shell">
                  <Lock class="login-field-icon" :size="18" />
                  <AppInput
                    :id="id"
                    v-model="form.code"
                    type="text"
                    aria-label="کد تایید"
                    inputmode="numeric"
                    pattern="[0-9]*"
                    maxlength="5"
                    dir="ltr"
                    autocomplete="one-time-code"
                    placeholder="_____"
                    class="login-input login-input--code"
                    autofocus
                  />
                </div>
              </template>
            </AppFormField>

            <AppButton block :loading="loading" @click="verifyOtp">ورود به بازار</AppButton>

            <div class="login-inline-actions">
              <div v-if="countdown > 0 || automaticSmsFallback" class="login-timer" role="status" aria-live="polite">
                <Clock :size="14" />
                <span>{{ otpDeliveryStatus }}</span>
              </div>
              <button v-else type="button" class="login-link-btn" @click="handleResend">ارسال مجدد کد</button>
            </div>
          </div>

          <div v-else-if="step === 'waiting_approval'" key="waiting" class="login-step login-step--centered">
            <Loader2 class="spin text-amber-600" :size="28" />
            <h3>در انتظار تایید</h3>
            <p>درخواست ورود شما به دستگاه اصلی ارسال شد. تایید را از همان دستگاه انجام دهید.</p>
            <div v-if="approvalCountdown > 0" class="login-timer">
              <Clock :size="14" />
              <span>{{ formattedApprovalTimer }}</span>
            </div>
            <div class="login-stack-actions">
              <AppButton block variant="secondary" :disabled="loading" @click="startRecoveryFlow">به دستگاه قبلی دسترسی ندارم</AppButton>
              <button type="button" class="login-link-btn" @click="stopApprovalPolling(); step = 'otp'; error = ''">بازگشت به مرحله قبل</button>
            </div>
          </div>

          <div v-else-if="step === 'recovery_waiting'" key="recovery-waiting" class="login-step login-step--centered">
            <Loader2 class="spin text-amber-600" :size="28" />
            <h3>در حال بررسی توسط مدیریت</h3>
            <p>درخواست بازیابی ثبت شد و نتیجه از همین صفحه اعلام می‌شود.</p>
            <div v-if="recoveryCountdown > 0" class="login-timer">
              <Clock :size="14" />
              <span>{{ formattedRecoveryTimer }}</span>
            </div>
            <button type="button" class="login-link-btn" @click="cancelRecoveryFlow">انصراف از درخواست</button>
          </div>

          <div v-else-if="step === 'recovery_identity'" key="recovery-identity" class="login-step">
            <div class="login-step-copy">
              <h3>ارسال مدرک احراز هویت</h3>
              <p>تصویر کارت شناسایی یا فایل مدرک را همراه با توضیح اختیاری ارسال کنید.</p>
            </div>

            <div v-if="recoveryCountdown > 0" class="login-timer">
              <Clock :size="14" />
              <span>{{ formattedRecoveryTimer }}</span>
            </div>

            <div class="login-picker-grid">
              <AppButton variant="secondary" @click="openRecoveryPicker('gallery')">گالری</AppButton>
              <AppButton variant="secondary" @click="openRecoveryPicker('camera')">دوربین</AppButton>
              <AppButton variant="secondary" @click="openRecoveryPicker('file')">فایل</AppButton>
            </div>

            <div class="login-upload-state">
              <span v-if="selectedRecoveryFileName">{{ selectedRecoveryFileName }}</span>
              <span v-else>هنوز فایلی انتخاب نشده است</span>
            </div>

            <AppFormField label="توضیح اختیاری">
              <template #default="{ id }">
                <AppTextarea :id="id" v-model="recoveryCaption" rows="3" placeholder="توضیح اختیاری..." />
              </template>
            </AppFormField>

            <div class="login-stack-actions">
              <AppButton block :loading="loading" @click="submitRecoveryIdentity">ارسال مدارک</AppButton>
              <button type="button" class="login-link-btn" @click="cancelRecoveryFlow">انصراف از درخواست</button>
            </div>
          </div>

          <div v-else-if="step === 'recovery_submitted'" key="recovery-submitted" class="login-step login-step--centered">
            <Loader2 class="spin text-amber-600" :size="28" />
            <h3>مدرک ارسال شد</h3>
            <p>مدارک برای بررسی ارسال شد و نتیجه از همین صفحه اعلام می‌شود.</p>
            <div v-if="recoveryCountdown > 0" class="login-timer">
              <Clock :size="14" />
              <span>{{ formattedRecoveryTimer }}</span>
            </div>
          </div>

          <div v-else-if="step === 'recovery_approved'" key="recovery-approved" class="login-step login-step--centered">
            <CheckCircle2 class="text-emerald-600" :size="32" />
            <h3>درخواست شما تایید شد</h3>
            <p>نشست قدیمی منقضی شد و اکنون می‌توانید وارد سامانه شوید.</p>
            <AppButton block :loading="loading" @click="enterWithApprovedRecovery">ورود به سامانه</AppButton>
          </div>

          <div v-else-if="step === 'recovery_rejected'" key="recovery-rejected" class="login-step login-step--centered">
            <XCircle class="text-rose-600" :size="32" />
            <h3>درخواست شما رد شد</h3>
            <p>در صورت نیاز می‌توانید دوباره درخواست بازیابی را ثبت کنید.</p>
            <AppButton block @click="restartLoginFlow">شروع دوباره</AppButton>
          </div>

          <div v-else-if="step === 'recovery_expired'" key="recovery-expired" class="login-step login-step--centered">
            <Clock class="text-slate-500" :size="32" />
            <h3>مهلت درخواست به پایان رسید</h3>
            <p>در مهلت تعیین‌شده پاسخی ثبت نشد. در صورت نیاز درخواست جدید ثبت کنید.</p>
            <AppButton block @click="restartLoginFlow">شروع دوباره</AppButton>
          </div>
        </transition>

        <transition name="fade">
          <div v-if="error" class="login-error-box" role="alert">
            <div>{{ error }}</div>
            <div v-if="canOfferAppRecovery" class="login-error-actions">
              <span>اگر نسخه قدیمی برنامه یا کش PWA گیر کرده، بازنشانی امن را اجرا کنید.</span>
              <AppButton variant="danger" size="sm" @click="startAppRecovery">پاک‌سازی کش برنامه و بارگذاری مجدد</AppButton>
            </div>
          </div>
        </transition>
      </AppSectionCard>

      <input ref="recoveryFileInput" type="file" accept="image/*" class="hidden" @change="handleRecoveryFileInput" />
      <input ref="recoveryCameraInput" type="file" accept="image/*" capture="environment" class="hidden" @change="handleRecoveryFileInput" />
      <input ref="recoveryDocumentInput" type="file" accept="image/*,.pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" class="hidden" @change="handleRecoveryFileInput" />

      <footer class="login-footer">
        <span>نسخه ۲.۴.۰</span>
        <span>ویب‌اپ ویژه معامله‌گران</span>
        <AppButton v-if="isDevMode" size="sm" variant="secondary" @click="startDevLogin">ورود سریع ۱ ساله</AppButton>
      </footer>
    </section>
  </AppPage>
</template>

<style scoped>
.login-view {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 1rem;
  padding: 1.25rem 0;
}

.login-card {
  gap: 1rem;
}

.login-brand-mark {
  width: 3rem;
  height: 3rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 1rem;
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
}

.login-step {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.login-step--centered {
  text-align: center;
  align-items: center;
}

.login-step--centered p,
.login-step-copy p,
.login-note-card,
.login-footer {
  color: var(--ds-text-muted);
}

.login-field-shell {
  position: relative;
}

.login-field-icon {
  position: absolute;
  top: 50%;
  left: 0.95rem;
  transform: translateY(-50%);
  color: var(--ds-text-muted);
  pointer-events: none;
}

.login-input {
  width: 100%;
  min-height: 3rem;
}

.login-input--ltr {
  padding-left: 2.75rem;
  direction: ltr;
  text-align: left;
}

.login-input--code {
  padding-left: 2.75rem;
  direction: ltr;
  text-align: center;
  letter-spacing: 0.4em;
  font-weight: 800;
}

.login-timer {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  align-self: center;
  border-radius: 999px;
  padding: 0.35rem 0.75rem;
  background: rgba(245, 158, 11, 0.08);
  color: #b45309;
  font-size: 0.78rem;
  font-weight: 700;
}

.login-install,
.login-stack-actions {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.login-note-card,
.login-upload-state,
.login-error-box {
  border: 1px solid var(--ds-border-subtle);
  border-radius: 1rem;
  padding: 0.85rem 1rem;
  background: var(--ds-surface-subtle);
  font-size: 0.82rem;
  line-height: 1.9;
}

.login-note-title {
  color: var(--ds-text-strong);
  font-weight: 800;
  margin-bottom: 0.35rem;
}

.login-step-meta,
.login-inline-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  flex-wrap: wrap;
  font-size: 0.82rem;
  color: var(--ds-text-muted);
}

.login-link-btn {
  border: 0;
  background: none;
  padding: 0;
  color: var(--ds-color-info-700, #0369a1);
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
}

.login-link-btn:hover {
  opacity: 0.85;
}

.login-picker-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
}

.login-upload-state {
  text-align: center;
}

.login-step h3 {
  margin: 0;
  font-size: 1rem;
  font-weight: 850;
  color: var(--ds-text-strong);
}

.login-step p,
.login-step-copy {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.9;
}

.login-error-box {
  color: var(--ds-color-danger-700, #b91c1c);
  border-color: rgba(220, 38, 38, 0.18);
  background: rgba(254, 242, 242, 0.9);
}

.login-error-actions {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-top: 0.75rem;
}

.login-footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
  text-align: center;
  font-size: 0.76rem;
}

.spin {
  animation: spin 1s linear infinite;
}

/* Slide Up Transition */
.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.slide-up-enter-from {
  opacity: 0;
  transform: translateY(20px) scale(0.98);
}

.slide-up-leave-to {
  opacity: 0;
  transform: translateY(-20px) scale(0.98);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 480px) {
  .login-picker-grid {
    grid-template-columns: 1fr;
  }
}
</style>
