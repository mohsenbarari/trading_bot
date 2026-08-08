<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { openTelegramLink } from '../services/telegramLink'
import { AppButton, AppCard, AppErrorState, AppFormField, AppInput, AppLoadingState, AppPage, AppPageHeader, AppTextarea } from '../components/ui'
import { invitationTerminalMessage, normalizeInvitationContract, type InvitationContractPayload } from '../utils/invitationContract'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { formatIranDateTime } from '../utils/iranTime'
import { routeRequestJson } from '../utils/routeRequest'

const route = useRoute()
const router = useRouter()
const token = route.query.token as string | undefined
const registrationToken = route.query.registration_token as string | undefined

const step = ref(1)
const loading = ref(true)
const redirecting = ref(false)
const error = ref('')
type RegistrationOperation = 'load-context' | 'request-otp' | 'verify-otp' | 'complete-registration'
const failedOperation = ref<RegistrationOperation | null>(null)
const retryAvailable = ref(false)
const activeActions = new Set<RegistrationOperation>()

const inviteInfo = ref<any>(null)
const otpCode = ref('')
const address = ref('')
const canConnectTelegram = ref(false)
const telegramLinkBusy = ref(false)
const telegramLinkError = ref('')
const inviteExpiry = computed(() => {
  const value = inviteInfo.value?.expires_at
  return value ? formatIranDateTime(value) : ''
})

const stepTitle = computed(() => {
  if (step.value === 1) return 'بررسی دعوت‌نامه'
  if (step.value === 2) return 'تایید شماره موبایل'
  if (step.value === 4) return 'اتصال تلگرام'
  return 'ثبت اطلاعات نهایی'
})

class TerminalRegistrationError extends Error {}

interface RegistrationContext extends Record<string, unknown> {
  account_name: string
  mobile_number: string
  role: string
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasRegistrationContext(value: unknown): value is RegistrationContext {
  return isObject(value)
    && typeof value.account_name === 'string'
    && Boolean(value.account_name.trim())
    && typeof value.mobile_number === 'string'
    && Boolean(value.mobile_number.trim())
    && typeof value.role === 'string'
    && Boolean(value.role.trim())
}

function hasDetailReceipt(value: unknown) {
  return isObject(value) && typeof value.detail === 'string' && Boolean(value.detail.trim())
}

function hasTokenReceipt(value: unknown): value is { access_token: string; refresh_token: string } {
  return isObject(value)
    && typeof value.access_token === 'string'
    && Boolean(value.access_token.trim())
    && typeof value.refresh_token === 'string'
    && Boolean(value.refresh_token.trim())
}

function hasTelegramLinkReceipt(value: unknown): value is Record<string, unknown> & {
  telegram_linked: boolean
  can_connect_telegram: boolean
} {
  return isObject(value)
    && typeof value.telegram_linked === 'boolean'
    && typeof value.can_connect_telegram === 'boolean'
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
  return cause.status === null || cause.status === 408 || cause.status === 425 || cause.status === 429 || cause.status >= 500
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

function isTerminalContextFailure(cause: unknown) {
  return cause instanceof TerminalRegistrationError || (
    isAppHttpError(cause)
    && cause.status !== null
    && [400, 401, 403, 404, 410, 422].includes(cause.status)
  )
}

async function loadRegistrationContext() {
  if (!beginAction('load-context')) return
  clearFailure()
  redirecting.value = false

  try {
    if (registrationToken) {
      const data = await routeRequestJson<unknown>(`/api/auth/pending-registration/${encodeURIComponent(registrationToken)}`, {
        mode: 'public',
        errorContext: { surface: 'auth', scope: 'page', operation: 'initial-load' },
      })
      if (!hasRegistrationContext(data)) {
        throw new Error('پاسخ جلسه ثبت‌نام کامل نیست.')
      }
      inviteInfo.value = data
      step.value = 3
      return
    }

    if (!token) {
      throw new TerminalRegistrationError('توکن دعوت یافت نشد.')
    }

    const data = await routeRequestJson<unknown>(`/api/invitations/validate/${encodeURIComponent(token)}`, {
      mode: 'public',
      errorContext: { surface: 'auth', scope: 'page', operation: 'initial-load', resourceLabel: 'دعوت‌نامه' },
    })
    if (!isObject(data)) {
      throw new Error('پاسخ بررسی دعوت‌نامه کامل نیست.')
    }
    const contract = normalizeInvitationContract(data as InvitationContractPayload)
    if (contract.state === 'completed') {
      redirecting.value = true
      await router.replace({ name: 'login', query: { registration: 'complete' } })
      return
    }
    if (contract.state !== 'pending' || data.valid === false || !contract.webAvailable) {
      throw new TerminalRegistrationError(invitationTerminalMessage(contract.state))
    }
    if (!hasRegistrationContext(data)) throw new Error('پاسخ بررسی دعوت‌نامه کامل نیست.')
    inviteInfo.value = data
  } catch (cause: unknown) {
    redirecting.value = false
    if (isTerminalContextFailure(cause)) {
      error.value = cause instanceof TerminalRegistrationError
        ? cause.message
        : (registrationToken ? 'جلسه تکمیل ثبت‌نام نامعتبر یا منقضی شده است.' : 'دعوت‌نامه نامعتبر است.')
      failedOperation.value = null
      retryAvailable.value = false
    } else {
      recordFailure('load-context', cause, 'بررسی اطلاعات ثبت‌نام اکنون ممکن نشد.')
    }
  } finally {
    finishAction('load-context')
  }
}

onMounted(() => {
  void loadRegistrationContext()
})

async function requestOtp() {
  if (!beginAction('request-otp')) return
  clearFailure()
  try {
    const data = await routeRequestJson<unknown>('/api/auth/register-otp-request', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      errorContext: { surface: 'auth', scope: 'form', operation: 'submit' },
    })
    if (!hasDetailReceipt(data)) throw new Error('پاسخ ارسال کد تایید کامل نیست.')
    step.value = 2
  } catch (cause: unknown) {
    recordFailure('request-otp', cause, 'ارسال کد تایید اکنون ممکن نشد.')
  } finally {
    finishAction('request-otp')
  }
}

async function verifyOtp() {
  if (otpCode.value.length !== 5) return
  if (!beginAction('verify-otp')) return
  clearFailure()
  try {
    const data = await routeRequestJson<unknown>('/api/auth/register-otp-verify', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, code: otpCode.value }),
      errorContext: { surface: 'auth', scope: 'field', operation: 'submit' },
    })
    if (!hasDetailReceipt(data)) throw new Error('پاسخ تایید کد کامل نیست.')
    step.value = 3
  } catch (cause: unknown) {
    recordFailure('verify-otp', cause, 'کد نادرست است')
  } finally {
    finishAction('verify-otp')
  }
}

async function submitRegistration() {
  if (address.value.length < 10) {
    error.value = 'آدرس باید حداقل ۱۰ کاراکتر باشد.'
    failedOperation.value = 'complete-registration'
    retryAvailable.value = false
    return
  }
  if (!beginAction('complete-registration')) return
  clearFailure()
  try {
    const payload = registrationToken
      ? { registration_token: registrationToken, address: address.value }
      : { token, address: address.value }
    const data = await routeRequestJson<unknown>('/api/auth/register-complete', {
      mode: 'public',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      errorContext: { surface: 'auth', scope: 'form', operation: 'submit' },
    })
    if (!hasTokenReceipt(data)) throw new Error('پاسخ تکمیل ثبت‌نام کامل نیست.')
    localStorage.setItem('auth_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    const me = await routeRequestJson<Record<string, unknown>>('/api/auth/me', {
      errorContext: { surface: 'auth', scope: 'action', operation: 'initial-load' },
    }).catch(() => null)
    canConnectTelegram.value = me?.can_connect_telegram === true && me?.telegram_linked !== true
    if (canConnectTelegram.value) {
      step.value = 4
      return
    }
    router.replace('/')
  } catch (cause: unknown) {
    recordFailure('complete-registration', cause, 'تکمیل ثبت‌نام اکنون ممکن نشد.')
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

function returnToForm() {
  clearFailure()
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
    telegramLinkError.value = typeof payload.detail === 'string' && payload.detail.trim()
      ? payload.detail
      : 'لینک اتصال تلگرام آماده نشد.'
  } catch (cause: unknown) {
    telegramLinkError.value = failureMessage(cause, 'ساخت لینک اتصال تلگرام ناموفق بود.')
  } finally {
    telegramLinkBusy.value = false
  }
}

function skipTelegramConnect() {
  router.replace('/')
}
</script>

<template>
  <AppPage narrow>
    <div class="register-view">
      <AppPageHeader
        eyebrow="ثبت‌نام"
        title="تکمیل ثبت‌نام"
        :description="stepTitle"
      />

      <AppCard class="register-card">
        <AppLoadingState v-if="redirecting || (loading && !inviteInfo)" :label="redirecting ? 'در حال انتقال به ورود' : 'در حال بررسی دعوت‌نامه'" />

        <AppErrorState v-else-if="error" title="ثبت‌نام ادامه پیدا نکرد" :message="error">
          <template v-if="failedOperation" #actions>
            <AppButton v-if="retryAvailable" variant="secondary" block :loading="loading" @click="retryFailedOperation">تلاش مجدد</AppButton>
            <AppButton v-else variant="secondary" block @click="returnToForm">بازگشت به فرم</AppButton>
          </template>
        </AppErrorState>

        <div v-else-if="step === 1" class="step-content">
          <div class="invite-info">
            <p class="info-row"><span>نام کاربری:</span> <strong>{{ inviteInfo.account_name }}</strong></p>
            <p class="info-row"><span>موبایل:</span> <strong>{{ inviteInfo.mobile_number }}</strong></p>
            <p class="info-row"><span>نقش:</span> <strong>{{ inviteInfo.role }}</strong></p>
            <p v-if="inviteExpiry" class="info-row"><span>مهلت ثبت‌نام:</span> <strong>{{ inviteExpiry }}</strong></p>
          </div>

          <p class="hint">برای احراز هویت، یک کد تایید به شماره موبایل شما ارسال می‌شود.</p>
          <AppButton block :loading="loading" @click="requestOtp">ارسال کد تایید</AppButton>
        </div>

        <div v-else-if="step === 2" class="step-content">
          <AppFormField label="کد تایید ۵ رقمی را وارد کنید:">
            <template #default="{ id, describedby }">
              <AppInput
                :id="id"
                v-model="otpCode"
                class="otp-input"
                :aria-describedby="describedby"
                type="tel"
                maxlength="5"
                dir="ltr"
                placeholder="- - - - -"
              />
            </template>
          </AppFormField>

          <AppButton block :disabled="otpCode.length !== 5" :loading="loading" @click="verifyOtp">تایید کد</AppButton>
        </div>

        <div v-else-if="step === 3" class="step-content">
          <div v-if="inviteInfo" class="invite-info">
            <p class="info-row"><span>نام کاربری:</span> <strong>{{ inviteInfo.account_name }}</strong></p>
            <p class="info-row"><span>موبایل:</span> <strong>{{ inviteInfo.mobile_number }}</strong></p>
            <p class="info-row"><span>نقش:</span> <strong>{{ inviteInfo.role }}</strong></p>
            <p v-if="inviteExpiry" class="info-row"><span>مهلت ثبت‌نام:</span> <strong>{{ inviteExpiry }}</strong></p>
          </div>

          <AppFormField label="آدرس دقیق پستی:" hint="استان، شهر، خیابان، پلاک و هر توضیح لازم را کامل وارد کنید.">
            <template #default="{ id, describedby }">
              <AppTextarea
                :id="id"
                v-model="address"
                class="address-input"
                :aria-describedby="describedby"
                rows="4"
                placeholder="استان، شهر، خیابان، پلاک..."
              />
            </template>
          </AppFormField>

          <AppButton block :disabled="address.length < 10" :loading="loading" @click="submitRegistration">تکمیل ثبت‌نام</AppButton>
        </div>

        <div v-else-if="step === 4" class="step-content">
          <p class="hint">اتصال تلگرام اجباری نیست، اما برای دریافت پیام‌های معاملاتی در ربات توصیه می‌شود.</p>
          <p v-if="telegramLinkError" class="telegram-link-error">{{ telegramLinkError }}</p>
          <AppButton block :loading="telegramLinkBusy" @click="connectTelegram">اتصال به ربات تلگرام</AppButton>
          <AppButton block variant="secondary" @click="skipTelegramConnect">فعلاً رد می‌کنم</AppButton>
        </div>
      </AppCard>
    </div>
  </AppPage>
</template>

<style scoped>
.register-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}

.register-card {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.invite-info {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.info-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  margin: 0;
  padding-bottom: 0.55rem;
  border-bottom: 1px solid var(--ds-border-light);
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
}

.info-row strong {
  color: var(--ds-text-primary);
}

.hint {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.otp-input {
  width: 100%;
  text-align: center;
  letter-spacing: 0.4em;
  font-weight: 800;
}

.address-input {
  width: 100%;
  min-height: 7rem;
}

.telegram-link-error {
  margin: 0;
  color: var(--ds-danger-600);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}
</style>
