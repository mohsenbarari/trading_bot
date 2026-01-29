<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Smartphone, Lock, Loader2, Download } from 'lucide-vue-next'

const router = useRouter()
const step = ref<'mobile' | 'otp'>('mobile')
const loading = ref(false)
const error = ref('')
const isStandalone = ref(false)
const showInstallBtn = ref(false)
const deferredPrompt = ref<any>(null)
const isIOS = ref(false)
const showHelp = ref(false)
const showManualInstructions = ref(false)

const form = reactive({
  mobile: '',
  code: ''
})

async function requestOtp() {
  if (!form.mobile || form.mobile.length < 10) {
    error.value = 'شماره موبایل معتبر نیست'
    return
  }
  
  loading.value = true
  error.value = ''
  
  try {
    const res = await fetch('/api/auth/request-otp', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ mobile_number: form.mobile })
    })
    
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'خطا در ارسال کد')
    }
    
    step.value = 'otp'
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function verifyOtp() {
  if (!form.code || form.code.length < 4) {
    error.value = 'کد احراز هویت نامعتبر است'
    return
  }

  loading.value = true
  error.value = ''

  try {
    const res = await fetch('/api/auth/verify-otp', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ mobile_number: form.mobile, otp_code: form.code })
    })
    
    if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'کد نادرست است')
    }
    
    const data = await res.json()
    localStorage.setItem('auth_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    
    router.push('/')
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function installPWA() {
  const prompt = deferredPrompt.value || (window as any).deferredPrompt
  
  if (prompt) {
    prompt.prompt()
    prompt.userChoice.then((choiceResult: any) => {
      if (choiceResult.outcome === 'accepted') {
        showInstallBtn.value = false
      }
      deferredPrompt.value = null;
      (window as any).deferredPrompt = null;
    })
  } else {
    // Fallback: Show manual instructions
    showManualInstructions.value = !showManualInstructions.value
  }
}

onMounted(() => {
  // Check if running in standalone mode (PWA)
  if (window.matchMedia('(display-mode: standalone)').matches || (window.navigator as any).standalone === true) {
    isStandalone.value = true
  }

  // Detect iOS for instructions
  const userAgent = window.navigator.userAgent.toLowerCase()
  isIOS.value = /iphone|ipad|ipod/.test(userAgent)

  // Listen for install prompt (Backup listener, though App.vue handles it)
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferredPrompt.value = e
    showInstallBtn.value = true
  })
  
  // Check if we already have it from App.vue
  if ((window as any).deferredPrompt) {
     deferredPrompt.value = (window as any).deferredPrompt
     showInstallBtn.value = true
  }
})
</script>

<template>
  <div class="min-h-[100dvh] flex items-center justify-center p-4 bg-gradient-to-br from-gray-50 to-gray-200 overflow-hidden">
    <div class="w-full max-w-md relative">
      
      <!-- Background Blobs -->
      <div class="absolute -top-20 -left-20 w-64 h-64 bg-primary-300 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob"></div>
      <div class="absolute -bottom-20 -right-20 w-64 h-64 bg-yellow-300 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob animation-delay-2000"></div>

      <!-- Glass Card -->
      <div class="relative bg-white/70 backdrop-blur-xl border border-white/50 shadow-2xl rounded-3xl p-8 overflow-hidden">
        
        <!-- Header -->
        <div class="text-center mb-8">
          <div class="w-16 h-16 bg-gradient-to-br from-primary-400 to-primary-600 rounded-2xl mx-auto flex items-center justify-center shadow-lg mb-4">
             <span class="text-3xl text-white font-bold">💎</span>
          </div>
          <h1 class="text-2xl font-bold text-gray-800">Gold</h1>
          <p class="text-gray-500 mt-2 text-sm">بازار امن معاملات طلا و سکه</p>
        </div>

        <!-- Transitions -->
        <transition name="fade" mode="out-in">
          
          <!-- Step 1: Mobile -->
          <div v-if="step === 'mobile'" key="mobile" class="space-y-6">
            <div class="space-y-2">
              <label class="text-sm font-medium text-gray-700 block text-right">شماره موبایل</label>
              <div class="relative">
                <input 
                  v-model="form.mobile"
                  type="tel" 
                  class="input-premium pl-12"
                  placeholder="0912..."
                  dir="ltr"
                />
                <Smartphone class="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" :size="20"/>
              </div>
            </div>

            <button @click="requestOtp" :disabled="loading" class="btn-primary">
              <Loader2 v-if="loading" class="animate-spin" />
              <span v-else>دریافت کد تایید</span>
            </button>

            <!-- PWA PROMOTION SECTION (Only if NOT installed) -->
            <div v-if="!isStandalone" class="pt-4 border-t border-gray-100 space-y-4">
                
                <!-- Install Button (Always Visible) -->
                <button @click="installPWA" class="w-full py-3 px-4 bg-yellow-50 text-yellow-700 font-bold rounded-xl border border-yellow-200 hover:bg-yellow-100 transition-all flex items-center justify-center gap-2">
                   <Download class="w-5 h-5"/>
                   <span>نصب اپلیکیشن Gold</span>
                </button>

                <!-- Android/Chrome Manual Instructions (Hidden by default, shown on click if native prompt fails) -->
                <transition name="fade">
                  <div v-if="showManualInstructions && !isIOS" class="text-sm text-gray-600 bg-gray-50 p-4 rounded-xl border border-gray-100 animate-pulse-once">
                     <p class="mb-1 font-bold text-amber-600 text-center">نصب خودکار انجام نشد ⚠️</p>
                     <p class="mb-4 text-xs text-gray-500 text-center">لطفاً طبق راهنمای زیر نصب کنید:</p>
                     
                     <div class="flex flex-col gap-2 items-center mb-4">
                       <div class="flex items-center gap-1">
                          <span>۱. منوی مرورگر</span>
                          <span class="font-bold text-lg leading-none">⋮</span>
                          <span>را بزنید</span>
                       </div>
                       <div class="flex items-center gap-1">
                          <span>۲. گزینه</span>
                          <span class="font-bold">Install App</span>
                          <span>یا</span>
                          <span class="font-bold">Add to Home Screen</span>
                       </div>
                     </div>
                     
                     <div class="border-t border-gray-200 pt-3">
                       <button @click="showHelp = !showHelp" class="text-primary-600 font-bold hover:underline flex items-center justify-center gap-1 w-full">
                          <span>مشکل در نصب؟</span>
                          <span class="text-xs bg-primary-100 px-2 py-0.5 rounded-full">راهنما</span>
                       </button>
                       
                       <div v-if="showHelp" class="mt-3 text-right text-xs text-gray-500 space-y-2 bg-white p-3 rounded-lg border border-gray-100">
                          <p>۱. مطمئن شوید که از مرورگر <b>Chrome</b> استفاده می‌کنید.</p>
                          <p>۲. اگر دکمه نصب را نمی‌بینید، کش مرورگر را پاک کنید.</p>
                          <p>۳. فیلترشکن خود را بررسی کنید.</p>
                          <p>۴. مطمئن شوید گزینه "Desktop Site" خاموش است.</p>
                       </div>
                     </div>
                  </div>
                </transition>

                <!-- iOS Instructions (Always visible on iOS) -->
                <div v-if="isIOS" class="text-sm text-gray-600 bg-gray-50 p-4 rounded-xl border border-gray-100">
                    <p class="mb-2 font-bold text-center">نصب نسخه iOS:</p>
                    <div class="flex flex-col items-center gap-2">
                        <div class="flex items-center gap-1">
                            <span>۱. دکمه</span>
                            <span class="text-blue-500 font-bold">Share</span>
                            <span>را بزنید</span>
                        </div>
                        <div class="flex items-center gap-1">
                            <span>۲. گزینه</span>
                            <span class="font-bold">Add to Home Screen</span>
                        </div>
                    </div>
                </div>

            </div>
          </div>

          <!-- Step 2: OTP -->
          <div v-else key="otp" class="space-y-6">
            <div class="text-center mb-6">
              <p class="text-sm text-gray-500">کد ارسال شده به {{ form.mobile }} را وارد کنید</p>
              <button @click="step = 'mobile'" class="text-xs text-primary-600 mt-2 hover:underline">ویرایش شماره</button>
            </div>

            <div class="space-y-2">
              <div class="relative">
                <input 
                  v-model="form.code"
                  type="text" 
                  class="input-premium pl-12 text-center tracking-[1em] font-mono text-lg"
                  placeholder="____"
                  maxlength="5"
                  dir="ltr"
                />
                <Lock class="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" :size="20"/>
              </div>
            </div>

            <button @click="verifyOtp" :disabled="loading" class="btn-primary">
              <Loader2 v-if="loading" class="animate-spin" />
              <span v-else>ورود به حساب</span>
            </button>
          </div>

        </transition>

        <!-- Error Message -->
        <div v-if="error" class="mt-4 p-3 bg-red-50 text-red-600 text-sm rounded-xl text-center animate-pulse">
          {{ error }}
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-blob {
  animation: blob 7s infinite;
}
.animation-delay-2000 {
  animation-delay: 2s;
}
@keyframes blob {
  0% { transform: translate(0px, 0px) scale(1); }
  33% { transform: translate(30px, -50px) scale(1.1); }
  66% { transform: translate(-20px, 20px) scale(0.9); }
  100% { transform: translate(0px, 0px) scale(1); }
}
</style>
