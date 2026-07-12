<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { openTelegramLink, requestTelegramLink } from '../services/telegramLink'
import { AppButton, AppCard, AppErrorState, AppFormField, AppInput, AppLoadingState, AppPage, AppPageHeader, AppTextarea } from '../components/ui'
import { invitationTerminalMessage, normalizeInvitationContract } from '../utils/invitationContract'
import { formatIranDateTime } from '../utils/iranTime'

const route = useRoute()
const router = useRouter()
const token = route.query.token as string | undefined
const registrationToken = route.query.registration_token as string | undefined

const step = ref(1)
const loading = ref(true)
const redirecting = ref(false)
const error = ref('')
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || ''

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

onMounted(async () => {
  try {
    if (registrationToken) {
      const res = await fetch(`${apiBaseUrl}/api/auth/pending-registration/${registrationToken}`)
      if (!res.ok) throw new Error('جلسه تکمیل ثبت‌نام نامعتبر یا منقضی شده است.')
      inviteInfo.value = await res.json()
      step.value = 3
      return
    }

    if (!token) {
      error.value = 'توکن دعوت یافت نشد.'
      return
    }

    const res = await fetch(`${apiBaseUrl}/api/invitations/validate/${token}`)
    if (!res.ok) throw new Error('دعوت‌نامه نامعتبر است.')
    const data = await res.json()
    const contract = normalizeInvitationContract(data)
    if (contract.state === 'completed') {
      redirecting.value = true
      await router.replace({ name: 'login', query: { registration: 'complete' } })
      return
    }
    if (contract.state !== 'pending' || data.valid === false || !contract.webAvailable) {
      throw new Error(invitationTerminalMessage(contract.state))
    }
    inviteInfo.value = data
  } catch (e: any) {
    redirecting.value = false
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function requestOtp() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${apiBaseUrl}/api/auth/register-otp-request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    })
    if (!res.ok) throw new Error('خطا در ارسال کد تایید')
    step.value = 2
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function verifyOtp() {
  if (otpCode.value.length !== 5) return
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${apiBaseUrl}/api/auth/register-otp-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, code: otpCode.value }),
    })

    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || 'کد نادرست است')
    }

    step.value = 3
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function submitRegistration() {
  if (address.value.length < 10) {
    error.value = 'آدرس باید حداقل ۱۰ کاراکتر باشد.'
    return
  }
  loading.value = true
  try {
    const payload = registrationToken
      ? { registration_token: registrationToken, address: address.value }
      : { token, address: address.value }
    const res = await fetch(`${apiBaseUrl}/api/auth/register-complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })

    if (!res.ok) throw new Error('خطا در ثبت‌نام')

    const data = await res.json()
    localStorage.setItem('auth_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    const meRes = await fetch(`${apiBaseUrl}/api/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    })
    const me = meRes.ok ? await meRes.json().catch(() => null) : null
    canConnectTelegram.value = me?.can_connect_telegram === true && me?.telegram_linked !== true
    if (canConnectTelegram.value) {
      step.value = 4
      return
    }
    router.replace('/')
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function connectTelegram() {
  if (telegramLinkBusy.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = ''
  try {
    const payload = await requestTelegramLink()
    if (payload.telegram_url) {
      openTelegramLink(payload.telegram_url)
      return
    }
    telegramLinkError.value = payload.detail || 'لینک اتصال تلگرام آماده نشد.'
  } catch (e: any) {
    telegramLinkError.value = e?.message || 'ساخت لینک اتصال تلگرام ناموفق بود.'
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
          <template v-if="step > 1" #actions>
            <AppButton variant="secondary" block @click="error = ''; loading = false">تلاش مجدد</AppButton>
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
