<template>
  <div class="min-h-screen bg-gray-50 flex items-center justify-center p-4">
    <div class="max-w-md w-full bg-white rounded-xl shadow-lg p-8">
      <div class="text-center mb-8">
        <div class="w-16 h-16 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center mx-auto mb-4">
          <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path>
          </svg>
        </div>
        <h2 class="text-2xl font-bold text-gray-900">تنظیم رمز عبور مدیر</h2>
        <p class="text-gray-500 mt-2 text-sm">
          برای حفظ امنیت سیستم، لطفاً رمز عبور اختصاصی خود را تعریف کنید. این رمز برای ورود از دستگاه‌های جدید استفاده می‌شود.
        </p>
      </div>

      <form @submit.prevent="submitPassword" class="space-y-6">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">رمز عبور جدید</label>
          <div class="relative">
            <input 
              v-model="form.password" 
              :type="showPassword ? 'text' : 'password'" 
              dir="ltr"
              required
              class="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-left outline-none transition"
              placeholder="••••••••"
            />
            <button type="button" class="absolute inset-y-0 left-3 flex items-center text-gray-400 hover:text-gray-600" @click="showPassword = !showPassword">
              <svg v-if="!showPassword" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
              <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.29 3.29m0 0a10.05 10.05 0 015.188-1.583c4.477 0 8.268 2.943 9.542 7a10.025 10.025 0 01-4.132 5.411m0 0l-3.29-3.29" /></svg>
            </button>
          </div>
          <!-- راهنمای خطاها زیر اینپوت -->
          <ul class="mt-2 text-xs text-gray-500 space-y-1 block list-none pl-0 text-right uppercase" dir="rtl">
            <li :class="form.password.length >= 8 ? 'text-green-600' : 'text-gray-500'">حداقل ۸ کاراکتر</li>
            <li :class="/[A-Z]/.test(form.password) ? 'text-green-600' : 'text-gray-500'">شامل حروف بزرگ انگلیسی</li>
            <li :class="/[a-z]/.test(form.password) ? 'text-green-600' : 'text-gray-500'">شامل حروف کوچک انگلیسی</li>
            <li :class="/[0-9]/.test(form.password) ? 'text-green-600' : 'text-gray-500'">شامل اعداد</li>
            <li :class="/[^A-Za-z0-9]/.test(form.password) ? 'text-green-600' : 'text-gray-500'">شامل کاراکتر ویژه (مانند @#$%)</li>
          </ul>
        </div>
        
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">تکرار رمز عبور جدید</label>
          <div class="relative">
            <input 
              v-model="form.confirmPassword" 
              :type="showConfirmPassword ? 'text' : 'password'" 
              dir="ltr"
              required
              class="w-full pl-10 pr-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-left outline-none transition"
              placeholder="••••••••"
            />
            <button type="button" class="absolute inset-y-0 left-3 flex items-center text-gray-400 hover:text-gray-600" @click="showConfirmPassword = !showConfirmPassword">
              <svg v-if="!showConfirmPassword" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
              <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.29 3.29m0 0a10.05 10.05 0 015.188-1.583c4.477 0 8.268 2.943 9.542 7a10.025 10.025 0 01-4.132 5.411m0 0l-3.29-3.29" /></svg>
            </button>
          </div>
        </div>

        <div v-if="error" class="p-3 bg-red-50 text-red-700 rounded-lg text-sm text-center">
          {{ error }}
        </div>

        <button 
          type="submit" 
          :disabled="loading || !isPasswordValid"
          class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center"
          :class="{'opacity-75 cursor-not-allowed': loading || !isPasswordValid}"
        >
          <svg v-if="loading" class="animate-spin -ml-1 mr-2 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          ثبت و ورود
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch } from '../utils/auth'

const router = useRouter()
const loading = ref(false)
const error = ref('')

const showPassword = ref(false)
const showConfirmPassword = ref(false)

const form = reactive({
  password: '',
  confirmPassword: ''
})

const isPasswordValid = computed(() => {
  const p = form.password
  return p.length >= 8 && /[A-Z]/.test(p) && /[a-z]/.test(p) && /[0-9]/.test(p) && /[^A-Za-z0-9]/.test(p)
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
      body: JSON.stringify({ password: form.password })
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
