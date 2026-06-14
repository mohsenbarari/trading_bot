<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Eye, EyeOff, LockKeyhole, ShieldCheck } from 'lucide-vue-next'
import { AppButton, AppCard, AppFormField, AppInput, AppPage, AppPageHeader, AppStatusBadge } from '../components/ui'
import { apiFetch } from '../utils/auth'

const router = useRouter()
const loading = ref(false)
const error = ref('')
const showPassword = ref(false)
const showConfirmPassword = ref(false)

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
const passwordError = computed(() => {
  if (!form.password) return ''
  return isPasswordValid.value ? '' : 'الزامات امنیتی رمز عبور رعایت نشده است'
})
const confirmError = computed(() => {
  if (!form.confirmPassword) return ''
  return form.password === form.confirmPassword ? '' : 'رمز عبور و تکرار آن یکسان نیستند'
})

async function submitPassword() {
  if (!isPasswordValid.value) {
    error.value = 'الزامات امنیتی رمز عبور رعایت نشده است'
    return
  }

  if (form.password !== form.confirmPassword) {
    error.value = 'رمز عبور و تکرار آن یکسان نیستند'
    return
  }

  error.value = ''
  loading.value = true

  try {
    const res = await apiFetch('/api/auth/setup-password', {
      method: 'POST',
      body: JSON.stringify({ password: form.password }),
    })

    if (!res.ok) {
      if (res.status === 405) {
        throw new Error('خطای دسترسی سیستمی: Method Not Allowed. مسیر API درست نیست.')
      }
      const data = await res.json()
      throw new Error(data.detail || 'خطا در ثبت رمز عبور')
    }

    router.replace('/')
  } catch (err: any) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AppPage narrow>
    <div class="setup-password-view">
      <AppPageHeader
        eyebrow="امنیت حساب"
        title="تنظیم رمز عبور مدیر"
        description="برای ورود امن از دستگاه‌های جدید، یک رمز عبور اختصاصی و قوی برای حساب خود تعریف کنید."
      />

      <AppCard class="setup-password-card">
        <div class="setup-password-intro">
          <span class="setup-password-icon" aria-hidden="true">
            <ShieldCheck :size="28" />
          </span>
          <div class="setup-password-copy">
            <strong>رمز عبور جدید</strong>
            <p>این رمز فقط برای ورودهای مدیریتی و نشست‌های جدید استفاده می‌شود.</p>
          </div>
        </div>

        <form class="setup-password-form" @submit.prevent="submitPassword">
          <AppFormField label="رمز عبور جدید" :error="passwordError">
            <template #default="{ id, describedby, invalid }">
              <div class="password-field">
                <AppInput
                  :id="id"
                  v-model="form.password"
                  :invalid="invalid"
                  :aria-describedby="describedby"
                  :type="showPassword ? 'text' : 'password'"
                  dir="ltr"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  class="password-toggle"
                  :aria-label="showPassword ? 'پنهان کردن رمز عبور' : 'نمایش رمز عبور'"
                  @click="showPassword = !showPassword"
                >
                  <EyeOff v-if="showPassword" :size="18" />
                  <Eye v-else :size="18" />
                </button>
                <span class="password-leading" aria-hidden="true">
                  <LockKeyhole :size="18" />
                </span>
              </div>
            </template>
          </AppFormField>

          <div class="password-rules" aria-label="الزامات امنیتی رمز عبور">
            <AppStatusBadge
              v-for="rule in passwordChecks"
              :key="rule.key"
              :tone="rule.passed ? 'success' : 'neutral'"
            >
              {{ rule.label }}
            </AppStatusBadge>
          </div>

          <AppFormField label="تکرار رمز عبور جدید" :error="confirmError">
            <template #default="{ id, describedby, invalid }">
              <div class="password-field">
                <AppInput
                  :id="id"
                  v-model="form.confirmPassword"
                  :invalid="invalid"
                  :aria-describedby="describedby"
                  :type="showConfirmPassword ? 'text' : 'password'"
                  dir="ltr"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  class="password-toggle"
                  :aria-label="showConfirmPassword ? 'پنهان کردن تکرار رمز عبور' : 'نمایش تکرار رمز عبور'"
                  @click="showConfirmPassword = !showConfirmPassword"
                >
                  <EyeOff v-if="showConfirmPassword" :size="18" />
                  <Eye v-else :size="18" />
                </button>
                <span class="password-leading" aria-hidden="true">
                  <LockKeyhole :size="18" />
                </span>
              </div>
            </template>
          </AppFormField>

          <div v-if="error" class="setup-password-error" role="alert">
            {{ error }}
          </div>

          <AppButton
            type="submit"
            block
            :loading="loading"
            :disabled="loading || !isPasswordValid || Boolean(confirmError)"
          >
            ثبت و ورود
          </AppButton>
        </form>
      </AppCard>
    </div>
  </AppPage>
</template>

<style scoped>
.setup-password-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}

.setup-password-card {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.setup-password-intro {
  display: flex;
  align-items: flex-start;
  gap: 0.85rem;
}

.setup-password-icon {
  width: 3rem;
  height: 3rem;
  border-radius: 0.9rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  flex: 0 0 auto;
}

.setup-password-copy {
  display: grid;
  gap: 0.2rem;
}

.setup-password-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 900;
}

.setup-password-copy p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.75;
}

.setup-password-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.password-field {
  position: relative;
}

.password-field :deep(.ui-input) {
  padding-left: 5.25rem;
  padding-right: 2.75rem;
  text-align: left;
}

.password-leading {
  position: absolute;
  top: 50%;
  right: 0.9rem;
  transform: translateY(-50%);
  color: var(--ds-text-placeholder);
  pointer-events: none;
}

.password-toggle {
  position: absolute;
  top: 50%;
  left: 0.75rem;
  transform: translateY(-50%);
  width: 2rem;
  height: 2rem;
  border: none;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--ds-text-muted);
  cursor: pointer;
}

.password-toggle:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.22);
  outline-offset: 2px;
}

.password-rules {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.setup-password-error {
  padding: 0.85rem 1rem;
  border-radius: var(--ds-radius-md);
  background: var(--ds-danger-50);
  border: 1px solid var(--ds-danger-200);
  color: var(--ds-danger-700);
  font-size: var(--ds-font-sm);
  line-height: 1.7;
}
</style>
