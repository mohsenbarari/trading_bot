<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Eye, EyeOff } from 'lucide-vue-next'
import { AppButton, AppFormField, AppInput, AppStatusBadge, AuthFlowShell } from '../components/ui'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import { assertSuccessfulNavigation } from '../utils/navigationResult'
import { routeRequestJson } from '../utils/routeRequest'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)
const passwordDirty = ref(false)
const successfulSetupReceipt = ref<string | null>(null)
const passwordInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
const confirmPasswordInput = ref<{ focus: (options?: FocusOptions) => void } | null>(null)
let submitPending = false

const form = reactive({
  password: '',
  confirmPassword: '',
})

const passwordChecks = computed(() => [
  { key: 'length', label: 'حداقل ۸ کاراکتر', passed: form.password.length >= 8 },
  { key: 'upper', label: 'شامل حروف بزرگ انگلیسی', passed: /[A-Z]/.test(form.password) },
  { key: 'lower', label: 'شامل حروف کوچک انگلیسی', passed: /[a-z]/.test(form.password) },
  { key: 'number', label: 'شامل اعداد', passed: /[0-9]/.test(form.password) },
  { key: 'special', label: 'شامل کاراکتر ویژه', passed: /[^A-Za-z0-9]/.test(form.password) },
])

const isPasswordValid = computed(() => passwordChecks.value.every((rule) => rule.passed))
const setupSucceeded = computed(() => successfulSetupReceipt.value !== null)
const passwordError = computed(() => {
  if (!form.password) return ''
  return isPasswordValid.value ? '' : 'الزامات امنیتی رمز عبور رعایت نشده است'
})
const confirmError = computed(() => {
  if (!form.confirmPassword) return ''
  return form.password === form.confirmPassword ? '' : 'رمز عبور و تکرار آن یکسان نیستند'
})

watch(
  () => form.password,
  () => {
    passwordDirty.value = true
  },
)

function passwordRuleTone(passed: boolean) {
  if (!passwordDirty.value) return 'neutral' as const
  return passed ? ('success' as const) : ('danger' as const)
}

function passwordRuleState(passed: boolean) {
  if (!passwordDirty.value) return 'بررسی‌نشده'
  return passed ? 'تأیید' : 'نیازمند اصلاح'
}

async function submitPassword() {
  if (!setupSucceeded.value && !isPasswordValid.value) {
    error.value = 'الزامات امنیتی رمز عبور رعایت نشده است'
    await nextTick()
    passwordInput.value?.focus()
    return
  }

  if (!setupSucceeded.value && form.password !== form.confirmPassword) {
    error.value = 'رمز عبور و تکرار آن یکسان نیستند'
    await nextTick()
    confirmPasswordInput.value?.focus()
    return
  }

  if (submitPending) return

  error.value = ''
  submitPending = true
  loading.value = true

  try {
    if (!successfulSetupReceipt.value) {
      const data = await routeRequestJson<unknown>('/api/auth/setup-password', {
        method: 'POST',
        body: JSON.stringify({ password: form.password }),
        errorContext: {
          surface: 'auth',
          scope: 'form',
          operation: 'submit',
          fallbackMessage: 'خطا در ثبت رمز عبور',
        },
      })
      const detail =
        data && typeof data === 'object' ? (data as Record<string, unknown>).detail : null
      if (typeof detail !== 'string' || !detail.trim()) {
        throw new Error('پاسخ ثبت رمز عبور کامل نیست.')
      }

      successfulSetupReceipt.value = detail.trim()
      showPassword.value = false
      showConfirmPassword.value = false
    }

    try {
      assertSuccessfulNavigation(await router.replace('/'))
    } catch {
      error.value = 'رمز عبور ثبت شد، اما ورود به سامانه اکنون ممکن نشد. دوباره تلاش کنید.'
      return
    }

    form.password = ''
    form.confirmPassword = ''
    successfulSetupReceipt.value = null
  } catch (cause: unknown) {
    if (isAppHttpError(cause) && cause.status === 405) {
      error.value = 'ثبت رمز عبور اکنون ممکن نشد. دوباره تلاش کنید.'
    } else if (isAppHttpError(cause)) {
      error.value =
        cause.status !== null && cause.status >= 500
          ? cause.presentation.message
          : cause.detail || 'خطا در ثبت رمز عبور'
    } else if (cause instanceof SyntaxError) {
      error.value = 'ثبت رمز عبور اکنون ممکن نشد. دوباره تلاش کنید.'
    } else {
      error.value =
        cause instanceof Error && /^[\u0600-\u06ff]/u.test(cause.message)
          ? cause.message
          : 'ثبت رمز عبور اکنون ممکن نشد. دوباره تلاش کنید.'
    }
  } finally {
    submitPending = false
    loading.value = false
  }
}
</script>

<template>
  <AuthFlowShell
    focused
    fill-viewport
    title="تنظیم رمز عبور"
    description="برای تکمیل گیت امنیتی، یک رمز قوی تعریف کنید. پس از ثبت موفق وارد سامانه می‌شوید."
  >
    <form class="ui-v2-auth-password-form" @submit.prevent="submitPassword">
      <AppFormField label="رمز عبور جدید" :error="passwordError">
        <template #default="{ id, describedby, invalid }">
          <div class="ui-v2-auth-password-field">
            <AppInput
              ref="passwordInput"
              :id="id"
              v-model="form.password"
              :invalid="invalid"
              :aria-describedby="describedby"
              :type="showPassword ? 'text' : 'password'"
              dir="ltr"
              placeholder="••••••••"
              autocomplete="new-password"
              autofocus
              :disabled="loading || setupSucceeded"
            />
            <button
              type="button"
              class="ui-v2-auth-password-toggle"
              :disabled="loading || setupSucceeded"
              :aria-label="showPassword ? 'پنهان کردن رمز عبور' : 'نمایش رمز عبور'"
              @click="showPassword = !showPassword"
            >
              <EyeOff v-if="showPassword" :size="18" />
              <Eye v-else :size="18" />
            </button>
          </div>
        </template>
      </AppFormField>

      <div
        class="ui-v2-auth-password-rules"
        aria-label="الزامات امنیتی رمز عبور"
        aria-live="polite"
      >
        <AppStatusBadge
          v-for="rule in passwordChecks"
          :key="rule.key"
          :tone="passwordRuleTone(rule.passed)"
        >
          {{ rule.label }} — {{ passwordRuleState(rule.passed) }}
        </AppStatusBadge>
      </div>

      <AppFormField label="تکرار رمز عبور جدید" :error="confirmError">
        <template #default="{ id, describedby, invalid }">
          <div class="ui-v2-auth-password-field">
            <AppInput
              ref="confirmPasswordInput"
              :id="id"
              v-model="form.confirmPassword"
              :invalid="invalid"
              :aria-describedby="describedby"
              :type="showConfirmPassword ? 'text' : 'password'"
              dir="ltr"
              placeholder="••••••••"
              autocomplete="new-password"
              :disabled="loading || setupSucceeded"
            />
            <button
              type="button"
              class="ui-v2-auth-password-toggle"
              :disabled="loading || setupSucceeded"
              :aria-label="
                showConfirmPassword ? 'پنهان کردن تکرار رمز عبور' : 'نمایش تکرار رمز عبور'
              "
              @click="showConfirmPassword = !showConfirmPassword"
            >
              <EyeOff v-if="showConfirmPassword" :size="18" />
              <Eye v-else :size="18" />
            </button>
          </div>
        </template>
      </AppFormField>

      <div v-if="error" class="ui-v2-auth-error" role="alert">
        {{ error }}
      </div>

      <div class="ui-v2-auth-password-actions">
        <AppButton
          type="submit"
          block
          :loading="loading"
          :disabled="loading || (!setupSucceeded && (!isPasswordValid || Boolean(confirmError)))"
        >
          {{ setupSucceeded ? 'تلاش دوباره برای ورود' : 'ثبت و ورود' }}
        </AppButton>
      </div>
    </form>
  </AuthFlowShell>
</template>
