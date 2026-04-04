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
          برای حفظ امنیت سیستم، لطفاً رمز عبور اختصاصی خود را تعریف کنید. این رمز برای ورود از دستگاه‌های جدید یا شرایط اضطراری استفاده می‌شود.
        </p>
      </div>

      <form @submit.prevent="submitPassword" class="space-y-6">
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">رمز عبور جدید</label>
          <input 
            v-model="form.password" 
            type="password" 
            dir="ltr"
            required
            class="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-left outline-none transition"
            placeholder="••••••••"
          />
        </div>
        
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">تکرار رمز عبور جدید</label>
          <input 
            v-model="form.confirmPassword" 
            type="password" 
            dir="ltr"
            required
            class="w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-left outline-none transition"
            placeholder="••••••••"
          />
        </div>

        <div v-if="error" class="p-3 bg-red-50 text-red-700 rounded-lg text-sm text-center">
          {{ error }}
        </div>

        <button 
          type="submit" 
          :disabled="loading"
          class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center"
          :class="{'opacity-75 cursor-not-allowed': loading}"
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
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch } from '../utils/auth'

const router = useRouter()
const loading = ref(false)
const error = ref('')

const form = reactive({
  password: '',
  confirmPassword: ''
})

async function submitPassword() {
  if (form.password.length < 6) {
    error.value = 'رمز عبور باید حداقل ۶ کاراکتر باشد'
    return
  }
  
  if (form.password !== form.confirmPassword) {
    error.value = 'رمز عبور و تکرار آن یکسان نیستند'
    return
  }
  
  error.value = ''
  loading.value = true
  
  try {
    const res = await apiFetch('/auth/setup-password', {
      method: 'POST',
      body: JSON.stringify({ password: form.password })
    })
    
    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || 'خطا در ثبت رمز عبور')
    }
    
    // موفقیت - هدایت به داشبورد
    router.push('/')
  } catch (err: any) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}
</script>
