<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppButton from '../components/ui/AppButton.vue';
import AppCard from '../components/ui/AppCard.vue';
import AppErrorState from '../components/ui/AppErrorState.vue';
import AppLoadingState from '../components/ui/AppLoadingState.vue';

const route = useRoute();
const router = useRouter();
const token = route.query.token as string;

const step = ref(1); // 1: Info/SendOTP, 2: VerifyOTP, 3: Address
const loading = ref(true);
const error = ref('');
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '';

const inviteInfo = ref<any>(null);
const otpCode = ref('');
const address = ref('');

// Step 1: Validate Token & Show Info
onMounted(async () => {
  if (!token) {
    error.value = 'توکن دعوت یافت نشد.';
    loading.value = false;
    return;
  }
  try {
    const res = await fetch(`${apiBaseUrl}/api/invitations/validate/${token}`);
    if (!res.ok) throw new Error('دعوت‌نامه نامعتبر است.');
    inviteInfo.value = await res.json();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
});

// Request OTP
async function requestOtp() {
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${apiBaseUrl}/api/auth/register-otp-request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
    if (!res.ok) throw new Error('خطا در ارسال کد تایید');
    step.value = 2;
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

// Verify OTP
async function verifyOtp() {
  if (otpCode.value.length !== 5) return;
  loading.value = true;
  error.value = '';
  try {
    const res = await fetch(`${apiBaseUrl}/api/auth/register-otp-verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, code: otpCode.value })
    });
    
    if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'کد نادرست است');
    }
    
    step.value = 3;
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

// Final Submit (Address)
async function submitRegistration() {
  if (address.value.length < 10) {
    error.value = 'آدرس باید حداقل ۱۰ کاراکتر باشد.';
    return;
  }
  loading.value = true;
  try {
    const res = await fetch(`${apiBaseUrl}/api/auth/register-complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, address: address.value })
    });
    
    if (!res.ok) throw new Error('خطا در ثبت‌نام');
    
    const data = await res.json();
    
    // Store tokens
    localStorage.setItem('auth_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    
    // Redirect to Home
    router.replace('/');
    
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="register-container">
    <AppCard class="register-card">
      <h2>تکمیل ثبت‌نام</h2>
      
      <AppLoadingState v-if="loading && !inviteInfo" label="در حال بررسی دعوت‌نامه" />
      
      <AppErrorState v-else-if="error" title="ثبت‌نام ادامه پیدا نکرد" :message="error">
        <template v-if="step > 1" #actions>
          <AppButton variant="secondary" block @click="error = ''; loading = false">تلاش مجدد</AppButton>
        </template>
      </AppErrorState>
      
      <div v-else-if="step === 1" class="step-content">
        <p class="info-row"><span>نام کاربری:</span> <strong>{{ inviteInfo.account_name }}</strong></p>
        <p class="info-row"><span>موبایل:</span> <strong>{{ inviteInfo.mobile_number }}</strong></p>
        <p class="info-row"><span>نقش:</span> <strong>{{ inviteInfo.role }}</strong></p>
        
        <p class="hint">برای احراز هویت، یک کد تایید به شماره موبایل شما ارسال می‌شود.</p>
        
        <AppButton block :loading="loading" @click="requestOtp">ارسال کد تایید</AppButton>
      </div>
      
      <div v-else-if="step === 2" class="step-content">
        <label>کد تایید ۵ رقمی را وارد کنید:</label>
        <input v-model="otpCode" type="tel" maxlength="5" class="otp-input" placeholder="- - - - -" />
        
        <AppButton block :disabled="otpCode.length !== 5" :loading="loading" @click="verifyOtp">تایید کد</AppButton>
      </div>
      
      <div v-else-if="step === 3" class="step-content">
        <label>آدرس دقیق پستی:</label>
        <textarea v-model="address" rows="4" class="address-input" placeholder="استان، شهر، خیابان، پلاک..."></textarea>
        
        <AppButton block :disabled="address.length < 10" :loading="loading" @click="submitRegistration">تکمیل ثبت‌نام</AppButton>
      </div>
      
    </AppCard>
  </div>
</template>

<style scoped>
.register-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: var(--ds-bg-page);
  padding: 1rem;
}
.register-card {
  width: 100%;
  max-width: 400px;
}

h2 {
  margin: 0 0 1.5rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
  text-align: center;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.info-row {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  margin: 0;
  border-bottom: 1px solid var(--ds-border-light);
  padding-bottom: 0.5rem;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
}

.info-row strong {
  color: var(--ds-text-primary);
}

.hint {
  margin: 0 0 0.5rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
  text-align: center;
}

label {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 800;
}

.otp-input,
.address-input {
  width: 100%;
  padding: 0.9rem;
  border: 1px solid var(--ds-border-medium);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  outline: none;
}

.otp-input {
  font-size: 1.35rem;
  letter-spacing: 0.45rem;
  text-align: center;
}

.address-input {
  resize: vertical;
}

.otp-input:focus,
.address-input:focus {
  border-color: var(--ds-primary-500);
  box-shadow: 0 0 0 3px var(--ds-primary-soft);
}
</style>
