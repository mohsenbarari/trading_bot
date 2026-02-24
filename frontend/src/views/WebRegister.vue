<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';

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
    <div class="card">
      <h2>تکمیل ثبت‌نام</h2>
      
      <div v-if="loading && !inviteInfo" class="loading">
        <div class="spinner"></div>
      </div>
      
      <div v-else-if="error" class="error-box">
        <p>❌ {{ error }}</p>
        <button v-if="step > 1" @click="error = ''; loading = false" class="btn secondary">تلاش مجدد</button>
      </div>
      
      <div v-else-if="step === 1" class="step-content">
        <p class="info-row"><span>نام کاربری:</span> <strong>{{ inviteInfo.account_name }}</strong></p>
        <p class="info-row"><span>موبایل:</span> <strong>{{ inviteInfo.mobile_number }}</strong></p>
        <p class="info-row"><span>نقش:</span> <strong>{{ inviteInfo.role }}</strong></p>
        
        <p class="hint">برای احراز هویت، یک کد تایید به شماره موبایل شما ارسال می‌شود.</p>
        
        <button @click="requestOtp" :disabled="loading" class="btn primary">
          {{ loading ? 'در حال ارسال...' : 'ارسال کد تایید' }}
        </button>
      </div>
      
      <div v-else-if="step === 2" class="step-content">
        <label>کد تایید ۵ رقمی را وارد کنید:</label>
        <input v-model="otpCode" type="tel" maxlength="5" class="otp-input" placeholder="- - - - -" />
        
        <button @click="verifyOtp" :disabled="otpCode.length !== 5 || loading" class="btn primary">
          {{ loading ? 'بررسی...' : 'تایید کد' }}
        </button>
      </div>
      
      <div v-else-if="step === 3" class="step-content">
        <label>آدرس دقیق پستی:</label>
        <textarea v-model="address" rows="4" class="address-input" placeholder="استان، شهر، خیابان، پلاک..."></textarea>
        
        <button @click="submitRegistration" :disabled="address.length < 10 || loading" class="btn primary">
          {{ loading ? 'ثبت نهایی...' : 'تکمیل ثبت‌نام' }}
        </button>
      </div>
      
    </div>
  </div>
</template>

<style scoped>
.register-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f3f4f6;
  padding: 1rem;
}
.card {
  background: white;
  padding: 2rem;
  border-radius: 1.5rem;
  box-shadow: 0 10px 25px rgba(0,0,0,0.1);
  width: 100%;
  max-width: 400px;
}
h2 { text-align: center; margin-bottom: 1.5rem; color: #1f2937; }
.info-row { display: flex; justify-content: space-between; margin-bottom: 1rem; border-bottom: 1px solid #f3f4f6; padding-bottom: 0.5rem; }
.hint { font-size: 0.85rem; color: #6b7280; margin-bottom: 1.5rem; text-align: center; }
.btn { width: 100%; padding: 1rem; border-radius: 1rem; font-weight: 700; border: none; cursor: pointer; margin-top: 1rem; }
.btn.primary { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; }
.btn.secondary { background: #e5e7eb; color: #374151; }
.btn:disabled { opacity: 0.7; cursor: not-allowed; }
.otp-input { width: 100%; padding: 1rem; font-size: 1.5rem; text-align: center; letter-spacing: 0.5rem; border: 2px solid #e5e7eb; border-radius: 1rem; outline: none; }
.otp-input:focus { border-color: #f59e0b; }
.address-input { width: 100%; padding: 1rem; border: 2px solid #e5e7eb; border-radius: 1rem; outline: none; resize: vertical; }
.address-input:focus { border-color: #f59e0b; }
.error-box { background: #fef2f2; color: #dc2626; padding: 1rem; border-radius: 1rem; text-align: center; }
.spinner { width: 30px; height: 30px; border: 3px solid #e5e7eb; border-top-color: #f59e0b; border-radius: 50%; animation: spin 1s infinite; margin: 0 auto; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>