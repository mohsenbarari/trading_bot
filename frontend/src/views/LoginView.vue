<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Smartphone, Lock, Loader2, Download, Clock } from 'lucide-vue-next'
import { apiFetch, setupExpiryTimer } from '../utils/auth'
import { primeCurrentUserSummary } from '../utils/currentUser'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'

const router = useRouter()
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

// OTP Timer State
const countdown = ref(0)
let timerInterval: any = null

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

function startTimer(seconds: number) {
  if (timerInterval) clearInterval(timerInterval)
  countdown.value = seconds
  timerInterval = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(timerInterval)
      countdown.value = 0
    }
  }, 1000)
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

function startAppRecovery() {
  const nextUrl = new URL(window.location.href)
  nextUrl.searchParams.set('app_recovery', Date.now().toString())
  window.location.replace(nextUrl.toString())
}

function goToOtpStep() {
  if (step.value === 'otp') return
  step.value = 'otp'
  pushBackState(() => {
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

  loading.value = true
  error.value = ''
  
  try {
    const res = await apiFetch('/api/auth/request-otp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mobile_number: form.mobile })
    })
    
    if (!res.ok) {
      const err = await res.json()
      if (res.status === 429) {
        const match = err.detail && typeof err.detail === 'string' ? err.detail.match(/(\d+)/) : null
        if (match) {
          const seconds = parseInt(match[1])
          startTimer(seconds)
          goToOtpStep()
          return
        }
      }
      throw new Error(err.detail || 'خطا در ارسال کد')
    }
    
    const data = await res.json()
    lastMethod.value = data.method
    
    // If Telegram -> 30s timer, else 120s
    const timerSeconds = data.method === 'telegram' ? 30 : 120
    startTimer(timerSeconds)
    goToOtpStep()
    
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function resendOtpSms() {
  loading.value = true
  error.value = ''
  
  try {
    const res = await apiFetch('/api/auth/resend-otp-sms', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ mobile_number: form.mobile })
    })
    
    if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'خطا در ارسال پیامک')
    }
    
    const data = await res.json()
    
    // SMS Sent successfully
    lastMethod.value = 'sms'
    
    // Use remaining TTL from backend if available
    const ttl = data.expires_in || 60
    startTimer(ttl)

    
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function handleResend() {
    if (lastMethod.value === 'telegram') {
        resendOtpSms()
    } else {
        requestOtp()
    }
}


async function verifyOtp() {
  if (loading.value) return
  if (!form.code || form.code.length < 4) {
    error.value = 'کد احراز هویت نامعتبر است'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const res = await apiFetch('/api/auth/verify-otp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ mobile_number: form.mobile, code: form.code, suspended_refresh_token: localStorage.getItem("suspended_refresh_token") || undefined })
    })

    
    if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'کد نادرست است')
    }
    
    const data = await res.json()
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
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
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
    if (!loginRequestId.value) return
    try {
      const res = await fetch(`/api/sessions/login-requests/${loginRequestId.value}/status`)
      if (!res.ok) return
      const data = await res.json()
      localStorage.removeItem('suspended_refresh_token')
      
      if (data.status === 'approved' && data.access_token) {
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
    } catch (e) {
      // Ignore polling errors
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
    stopRecoveryPolling()
    clearRecoveryDraft()
    form.code = ''
    step.value = 'mobile'
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تایید دریافت کنید.'
  }
}

async function pollRecoveryStatusOnce() {
  if (!loginRequestId.value) return

  try {
    const res = await fetch(`/api/sessions/login-requests/${loginRequestId.value}/recovery/status`)
    if (!res.ok) return
    const data = await res.json()
    applyRecoveryStatus(data)
  } catch {
    // Ignore polling errors.
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
  if (!loginRequestId.value || loading.value) return

  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/sessions/login-requests/${loginRequestId.value}/recovery`, {
      method: 'POST',
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(parseResponseError(data, 'شروع مسیر بازیابی ممکن نشد'))
    }

    stopApprovalPolling(true)
    applyRecoveryStatus(data)
    startRecoveryPolling()
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function cancelRecoveryFlow() {
  if (!loginRequestId.value || loading.value) return

  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/sessions/login-requests/${loginRequestId.value}/recovery/cancel`, {
      method: 'POST',
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(parseResponseError(data, 'لغو درخواست بازیابی ممکن نشد'))
    }

    stopApprovalPolling()
    stopRecoveryPolling()
    clearRecoveryDraft()
    form.code = ''
    step.value = 'mobile'
    error.value = 'درخواست بازیابی لغو شد. برای ادامه دوباره کد تایید دریافت کنید.'
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
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

  loading.value = true
  error.value = ''
  try {
    const formData = new FormData()
    formData.set('file', recoveryFile.value)
    const trimmedCaption = recoveryCaption.value.trim()
    if (trimmedCaption) {
      formData.set('caption', trimmedCaption)
    }

    const res = await fetch(`/api/sessions/login-requests/${loginRequestId.value}/recovery/identity`, {
      method: 'POST',
      body: formData,
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(parseResponseError(data, 'ارسال مدرک ممکن نشد'))
    }

    setRecoveryFile(null)
    recoveryCaption.value = ''
    applyRecoveryStatus(data?.recovery || data)
    startRecoveryPolling()
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function enterWithApprovedRecovery() {
  if (!recoveryApprovedTokens.value?.access_token) {
    error.value = 'توکن ورود آماده نیست. لطفاً دوباره تلاش کنید.'
    return
  }

  await completeAuthenticatedLogin(recoveryApprovedTokens.value)
}

function restartLoginFlow() {
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  form.code = ''
  error.value = ''
  step.value = 'mobile'
}

const shouldShowManualInstallEntry = computed(() => (
  !isStandalone.value &&
  !isInstalled.value &&
  !supportsNativeInstallPrompt.value
))

const manualInstallGuideTitle = computed(() => (
  isIOS.value ? 'راهنمای نصب در آیفون' : 'راهنمای نصب دستی'
))

function installPWA() {
  showManualInstallGuide.value = true
}

const isDevMode = window.location.hostname === 'localhost' || 
                    window.location.hostname === '127.0.0.1' || 
                    window.location.hostname.startsWith('192.168.') || 
                    window.location.hostname.startsWith('172.') || 
                    window.location.hostname.startsWith('10.');

async function startDevLogin() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
    const res = await fetch(`${baseUrl}/api/auth/dev-login`, { method: 'POST' })
    if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'دسترسی مجاز نیست')
    }
    const data = await res.json()
    await completeAuthenticatedLogin(data)
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

let beforeInstallPromptHandler: ((event: Event) => void) | null = null
let pwaInstallReadyHandler: (() => void) | null = null
let appInstalledHandler: (() => void) | null = null

onMounted(() => {
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
})

watch(() => form.code, (newVal) => {
  if (newVal && newVal.length === 5) {
    verifyOtp()
  }
})

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

watch(() => form.mobile, (newVal) => {
  if (newVal && newVal.length === 11 && /^09\d{9}$/.test(newVal) && !loading.value && countdown.value === 0) {
    requestOtp()
  }
})

onUnmounted(() => {
  if (ac) ac.abort();
  if (timerInterval) clearInterval(timerInterval)
  if (beforeInstallPromptHandler) window.removeEventListener('beforeinstallprompt', beforeInstallPromptHandler)
  if (pwaInstallReadyHandler) window.removeEventListener('pwa-install-ready', pwaInstallReadyHandler)
  if (appInstalledHandler) window.removeEventListener('appinstalled', appInstalledHandler)
  stopApprovalPolling()
  stopRecoveryPolling()
  clearBackStack()
})

// Back to mobile step (UI-initiated via "ویرایش شماره" button)
function goBackToMobile() {
  stopApprovalPolling()
  stopRecoveryPolling()
  clearRecoveryDraft()
  form.code = ''
  step.value = 'mobile'
  error.value = ''
  popBackState()
}
</script>

<template>
  <div class="min-h-[100dvh] flex items-center justify-center p-4 overflow-hidden relative">
    
    <!-- Background Elements -->
    <div class="absolute inset-0 overflow-hidden pointer-events-none">
       <div class="absolute -top-[20%] -right-[10%] w-[70vw] h-[70vw] bg-amber-200/20 rounded-full blur-3xl animate-pulse-slow"></div>
       <div class="absolute top-[40%] -left-[10%] w-[50vw] h-[50vw] bg-yellow-400/10 rounded-full blur-3xl animate-pulse-slower"></div>
    </div>

    <div class="w-full max-w-sm relative z-10 perspective-1000">
      
      <!-- Glass Card -->
      <div class="relative bg-white/70 backdrop-blur-xl border border-white/80 shadow-[0_8px_30px_rgb(0,0,0,0.04)] rounded-[2rem] p-8 overflow-hidden transition-all duration-500 hover:shadow-[0_8px_30px_rgb(251,191,36,0.1)]">
        
        <!-- Header -->
        <div class="text-center mb-10 relative">
          <div class="w-20 h-20 bg-gradient-to-br from-amber-400 to-amber-600 rounded-2xl mx-auto flex items-center justify-center shadow-lg shadow-amber-500/30 mb-5 relative group transform transition-transform hover:scale-105 duration-300">
             <div class="absolute inset-0 bg-white/20 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity"></div>
             <span class="text-4xl filter drop-shadow-md pb-1">💎</span>
          </div>
          <h1 class="text-2xl font-black text-gray-800 tracking-tight">Gold Market</h1>
          <p class="text-gray-500 mt-2 text-sm font-medium">ورود به بازار امن طلا</p>
        </div>

        <!-- Transitions -->
        <transition name="slide-up" mode="out-in">
          
          <!-- Step 1: Mobile -->
          <div v-if="step === 'mobile'" key="mobile" class="space-y-6">
            <div class="space-y-3">
              <label class="text-sm font-bold text-gray-700 block text-right pr-1">شماره موبایل</label>
              <div class="relative group">
                <input 
                  v-model="form.mobile"
                  type="tel" 
                  class="input-premium peer"
                  style="direction: ltr; text-align: left !important; padding-left: 3.5rem !important;"
                  placeholder="0912..."
                  dir="ltr"
                />
                <Smartphone class="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 peer-focus:text-amber-500 transition-colors" :size="20"/>
                <div class="absolute inset-0 rounded-xl border border-amber-500/0 peer-focus:border-amber-500/50 pointer-events-none transition-all duration-300 peer-focus:ring-4 peer-focus:ring-amber-500/10"></div>
              </div>
            </div>

            <button 
              @click="requestOtp" 
              :disabled="loading" 
              class="btn-primary group relative overflow-hidden disabled:opacity-75 disabled:cursor-not-allowed"
            >
              <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              
              <Loader2 v-if="loading" class="animate-spin" />
              <div v-else-if="countdown > 0" class="flex items-center gap-2">
                <span>وارد کردن کد</span>
                <span class="font-mono text-xs opacity-75 dir-ltr">({{ formattedTimer }})</span>
              </div>
              <span v-else>دریافت کد تایید</span>
            </button>

            <!-- PWA PROMOTION -->
            <div v-if="shouldShowManualInstallEntry" class="pt-6 border-t border-gray-100/50 space-y-4">
                <button @click="installPWA" class="w-full py-3.5 px-4 bg-gradient-to-r from-amber-50 to-orange-50 text-amber-800 font-bold rounded-xl border border-amber-200/50 hover:bg-amber-100 transition-all flex items-center justify-center gap-3 shadow-sm hover:shadow-md">
                   <Download class="w-5 h-5"/>
                   <span>نصب اپلیکیشن</span>
                </button>

                <div v-if="showManualInstallGuide" class="text-xs text-gray-600 bg-gray-50/70 p-3 rounded-xl border border-gray-100 leading-6 text-right space-y-2">
                  <p class="font-bold text-gray-700">{{ manualInstallGuideTitle }}</p>
                  <template v-if="isIOS">
                    <p>برای نصب در iPhone یا iPad، نصب مستقیم از داخل Chrome یا مرورگر داخلی تلگرام انجام نمی‌شود. سایت را در Safari باز کنید و این مراحل را انجام دهید:</p>
                    <ol class="list-decimal list-inside space-y-1">
                      <li>آدرس سایت را کپی کنید و در Safari باز کنید.</li>
                      <li>در نوار پایین Safari دکمه Share، یعنی مربع با فلش رو به بالا، را بزنید.</li>
                      <li>در فهرست باز شده کمی پایین بروید و Add to Home Screen را انتخاب کنید.</li>
                      <li>نام Gold را تایید کنید و از بالای صفحه Add را بزنید.</li>
                      <li>بعد از نصب، از آیکن Gold روی Home Screen وارد شوید تا برنامه بدون نوار مرورگر باز شود.</li>
                    </ol>
                  </template>
                  <template v-else>
                    <p>این مرورگر نصب مستقیم داخل صفحه را پشتیبانی نمی‌کند. برای نصب شبیه اپ، سایت را با Chrome یا Edge باز کنید؛ یا از منوی مرورگر گزینه Add to Home Screen / Install App را انتخاب کنید.</p>
                  </template>
                </div>
            </div>
          </div>

          <!-- Step 2: OTP -->
          <div v-else-if="step === 'otp'" key="otp" class="space-y-6">
            <div class="text-center mb-6">
              <p class="text-sm text-gray-500 mb-1">کد ارسال شده به {{ form.mobile }}</p>
              <button @click="goBackToMobile()" class="text-xs text-amber-600 font-bold hover:text-amber-700 transition-colors bg-amber-50 px-3 py-1 rounded-full">ویرایش شماره</button>
            </div>

            <div class="space-y-2">
              <div class="relative group">
                <input 
                  v-model="form.code"
                  type="text" 
                  inputmode="numeric"
                  pattern="[0-9]*"
                  class="input-premium !pl-14 !pr-14 tracking-[0.5em] font-mono text-xl font-bold text-gray-800"
                  style="direction: ltr; text-align: center;"
                  placeholder="_____"
                  maxlength="5"
                  dir="ltr"
                  autocomplete="one-time-code"
                  autofocus
                />
                <Lock class="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 peer-focus:text-amber-500 transition-colors" :size="20"/>
                <div class="absolute inset-0 rounded-xl border border-amber-500/0 peer-focus:border-amber-500/50 pointer-events-none transition-all duration-300 peer-focus:ring-4 peer-focus:ring-amber-500/10"></div>
              </div>
            </div>

            <button @click="verifyOtp" :disabled="loading" class="btn-primary group relative overflow-hidden">
               <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              <Loader2 v-if="loading" class="animate-spin" />
              <span v-else>ورود به بازار</span>
            </button>
            
            <div class="text-center mt-4">
                 <div v-if="countdown > 0" class="inline-flex items-center gap-2 text-xs font-mono text-gray-400 bg-gray-50 px-3 py-1 rounded-full">
                    <Clock :size="14" />
                    <span>{{ formattedTimer }} تا ارسال مجدد</span>
                 </div>
                 <button v-else @click="handleResend" class="text-xs text-amber-600 hover:text-amber-700 font-bold transition-colors">
                    ارسال مجدد کد
                 </button>

            </div>
          </div>

          <!-- Step 3: Waiting for Approval -->
          <div v-else-if="step === 'waiting_approval'" key="waiting" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-amber-50 flex items-center justify-center animate-pulse">
                <Loader2 class="w-8 h-8 text-amber-500 animate-spin" />
              </div>
              <h3 class="text-lg font-bold text-gray-800">در انتظار تایید</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                درخواست ورود شما به دستگاه اصلی ارسال شد.
                <br/>لطفاً از دستگاه اصلی خود تایید کنید.
              </p>
              <div v-if="approvalCountdown > 0" class="inline-flex items-center gap-2 text-sm font-mono text-amber-600 bg-amber-50 px-4 py-2 rounded-full">
                <Clock :size="16" />
                <span>{{ formattedApprovalTimer }}</span>
              </div>
            </div>
            <div class="space-y-3">
              <button
                @click="startRecoveryFlow"
                :disabled="loading"
                class="w-full py-3 rounded-xl border border-amber-200 text-amber-700 font-bold text-sm bg-amber-50 hover:bg-amber-100 transition-colors disabled:opacity-50"
              >
                به دستگاه قبلی دسترسی ندارم
              </button>
              <button @click="stopApprovalPolling(); step = 'otp'; error = ''" class="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                بازگشت به مرحله قبل
              </button>
            </div>
          </div>

          <div v-else-if="step === 'recovery_waiting'" key="recovery-waiting" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-amber-50 flex items-center justify-center animate-pulse">
                <Loader2 class="w-8 h-8 text-amber-500 animate-spin" />
              </div>
              <h3 class="text-lg font-bold text-gray-800">در حال بررسی توسط مدیریت</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                درخواست شما برای بررسی توسط تیم مدیریت ثبت شد.
                <br/>نتیجه از طریق پیامک و همین صفحه به شما اعلام می‌شود.
              </p>
              <div v-if="recoveryCountdown > 0" class="inline-flex items-center gap-2 text-sm font-mono text-amber-600 bg-amber-50 px-4 py-2 rounded-full">
                <Clock :size="16" />
                <span>{{ formattedRecoveryTimer }}</span>
              </div>
            </div>
            <button @click="cancelRecoveryFlow" class="text-xs text-gray-500 hover:text-gray-700 transition-colors">
              انصراف از درخواست
            </button>
          </div>

          <div v-else-if="step === 'recovery_identity'" key="recovery-identity" class="space-y-5 text-right">
            <div class="text-center space-y-3">
              <h3 class="text-lg font-bold text-gray-800">ارسال مدرک احراز هویت</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                تیم مدیریت برای ادامه بررسی، تصویر کارت شناسایی یا فایل مدرک شما را نیاز دارد.
                <br/>در صورت تمایل می‌توانید توضیح کوتاه هم همراه آن ارسال کنید.
              </p>
              <div v-if="recoveryCountdown > 0" class="inline-flex items-center gap-2 text-sm font-mono text-amber-600 bg-amber-50 px-4 py-2 rounded-full">
                <Clock :size="16" />
                <span>{{ formattedRecoveryTimer }}</span>
              </div>
            </div>

            <div class="space-y-3">
              <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <button @click="openRecoveryPicker('gallery')" type="button" class="py-3 rounded-xl border border-gray-200 text-sm font-bold text-gray-700 hover:bg-gray-50 transition-colors">
                  گالری
                </button>
                <button @click="openRecoveryPicker('camera')" type="button" class="py-3 rounded-xl border border-gray-200 text-sm font-bold text-gray-700 hover:bg-gray-50 transition-colors">
                  دوربین
                </button>
                <button @click="openRecoveryPicker('file')" type="button" class="py-3 rounded-xl border border-gray-200 text-sm font-bold text-gray-700 hover:bg-gray-50 transition-colors">
                  فایل
                </button>
              </div>

              <div class="rounded-xl border border-dashed border-amber-200 bg-amber-50/70 px-4 py-3 text-sm text-gray-600 text-center min-h-[58px] flex items-center justify-center">
                <span v-if="selectedRecoveryFileName">{{ selectedRecoveryFileName }}</span>
                <span v-else>هنوز فایلی انتخاب نشده است</span>
              </div>

              <textarea
                v-model="recoveryCaption"
                rows="3"
                class="input-premium !min-h-[96px] !py-3"
                placeholder="توضیح اختیاری..."
              />
            </div>

            <div class="space-y-3 text-center">
              <button @click="submitRecoveryIdentity" :disabled="loading" class="btn-primary group relative overflow-hidden">
                <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
                <Loader2 v-if="loading" class="animate-spin" />
                <span v-else>ارسال مدارک</span>
              </button>
              <button @click="cancelRecoveryFlow" class="text-xs text-gray-500 hover:text-gray-700 transition-colors">
                انصراف از درخواست
              </button>
            </div>
          </div>

          <div v-else-if="step === 'recovery_submitted'" key="recovery-submitted" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-amber-50 flex items-center justify-center animate-pulse">
                <Loader2 class="w-8 h-8 text-amber-500 animate-spin" />
              </div>
              <h3 class="text-lg font-bold text-gray-800">مدرک ارسال شد</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                مدارک شما برای بررسی ارسال شد.
                <br/>نتیجه از طریق پیامک و همین صفحه به شما اعلام می‌شود.
              </p>
              <div v-if="recoveryCountdown > 0" class="inline-flex items-center gap-2 text-sm font-mono text-amber-600 bg-amber-50 px-4 py-2 rounded-full">
                <Clock :size="16" />
                <span>{{ formattedRecoveryTimer }}</span>
              </div>
            </div>
          </div>

          <div v-else-if="step === 'recovery_approved'" key="recovery-approved" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-emerald-50 flex items-center justify-center">
                <span class="text-3xl">✓</span>
              </div>
              <h3 class="text-lg font-bold text-gray-800">درخواست شما تایید شد</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                اطلاعات لاگین دستگاه قبلی شما منقضی شد.
                <br/>اکنون می‌توانید وارد سامانه شوید.
              </p>
            </div>
            <button @click="enterWithApprovedRecovery" :disabled="loading" class="btn-primary group relative overflow-hidden">
              <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              <span>ورود به سامانه</span>
            </button>
          </div>

          <div v-else-if="step === 'recovery_rejected'" key="recovery-rejected" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-red-50 flex items-center justify-center">
                <span class="text-3xl">✕</span>
              </div>
              <h3 class="text-lg font-bold text-gray-800">درخواست شما رد شد</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                درخواست بازیابی نشست توسط تیم مدیریت رد شد.
                <br/>در صورت نیاز می‌توانید دوباره درخواست خود را ثبت کنید.
              </p>
            </div>
            <button @click="restartLoginFlow" class="btn-primary group relative overflow-hidden">
              <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              <span>شروع دوباره</span>
            </button>
          </div>

          <div v-else-if="step === 'recovery_expired'" key="recovery-expired" class="space-y-6 text-center">
            <div class="flex flex-col items-center gap-4">
              <div class="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center">
                <Clock class="w-8 h-8 text-gray-500" />
              </div>
              <h3 class="text-lg font-bold text-gray-800">مهلت درخواست به پایان رسید</h3>
              <p class="text-sm text-gray-500 leading-relaxed">
                برای این درخواست در مهلت تعیین‌شده پاسخی ثبت نشد.
                <br/>در صورت نیاز می‌توانید دوباره درخواست خود را ثبت کنید.
              </p>
            </div>
            <button @click="restartLoginFlow" class="btn-primary group relative overflow-hidden">
              <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              <span>شروع دوباره</span>
            </button>
          </div>

        </transition>

        <!-- Error Message -->
        <transition name="fade">
          <div v-if="error" class="mt-6 p-4 bg-red-50/80 border border-red-100 text-red-600 text-sm rounded-xl text-center shadow-sm backdrop-blur-sm relative overflow-hidden">
             <div class="absolute top-0 left-0 w-1 h-full bg-red-400"></div>
             <div>{{ error }}</div>
             <div v-if="canOfferAppRecovery" class="mt-3 flex flex-col items-center gap-2 text-xs text-red-500">
               <span>اگر نسخهٔ قدیمی برنامه یا کش PWA گیر کرده، این بازنشانی امن را اجرا کنید.</span>
               <button
                 type="button"
                 class="px-4 py-2 rounded-full bg-white text-red-600 font-bold border border-red-200 hover:bg-red-50 transition-colors"
                 @click="startAppRecovery"
               >
                 پاک‌سازی کش برنامه و بارگذاری مجدد
               </button>
             </div>
          </div>
        </transition>

      </div>

      <input ref="recoveryFileInput" type="file" accept="image/*" class="hidden" @change="handleRecoveryFileInput" />
      <input ref="recoveryCameraInput" type="file" accept="image/*" capture="environment" class="hidden" @change="handleRecoveryFileInput" />
      <input ref="recoveryDocumentInput" type="file" accept="image/*,.pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" class="hidden" @change="handleRecoveryFileInput" />
      
      <!-- Footer Info -->
      <div class="text-center mt-8 text-xs text-gray-400 font-medium opacity-60 flex flex-col gap-2 relative z-50">
        <div>نسخه ۲.۴.۰ • طراحی شده برای معامله‌گران</div>
        
        <button 
          v-if="isDevMode" 
          @click="startDevLogin" 
          class="inline-block mt-2 px-3 py-1 bg-amber-100 text-amber-800 rounded-md hover:bg-amber-200 transition-colors mx-auto w-max font-bold border border-amber-300">
             ورود سریع ۱ ساله (توسعه‌دهنده)
        </button>
      </div>
      
    </div>
  </div>
</template>

<style scoped>
.animate-pulse-slow {
  animation: pulse 8s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
.animate-pulse-slower {
  animation: pulse 12s cubic-bezier(0.4, 0, 0.6, 1) infinite;
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

.perspective-1000 {
  perspective: 1000px;
}
</style>
