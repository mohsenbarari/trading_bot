<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Smartphone, Lock, Loader2, Download, Clock } from 'lucide-vue-next'

const router = useRouter()
const step = ref<'mobile' | 'otp'>('mobile')
const loading = ref(false)
const error = ref('')
const isStandalone = ref(false)
const showInstallBtn = ref(false)
const deferredPrompt = ref<any>(null)
const isIOS = ref(false)
const isInstalled = ref(false)

// OTP Timer State
const countdown = ref(0)
let timerInterval: any = null

const form = reactive({
  mobile: '',
  code: ''
})

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

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval)
})

const formattedTimer = computed(() => {
  const m = Math.floor(countdown.value / 60).toString().padStart(2, '0')
  const s = (countdown.value % 60).toString().padStart(2, '0')
  return `${m}:${s}`
})

async function requestOtp() {
  if (!form.mobile || form.mobile.length < 10) {
    error.value = 'شماره موبایل معتبر نیست'
    return
  }
  
  if (countdown.value > 0) return

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
      // Handle Rate Limit (429)
      if (res.status === 429) {
        // Try to parse seconds from error details like "Please wait X seconds"
        // Backend text is: "لطفاً {ttl} ثانیه دیگر تلاش کنید."
        const match = err.detail && typeof err.detail === 'string' ? err.detail.match(/(\d+)/) : null
        if (match) {
          const seconds = parseInt(match[1])
          startTimer(seconds)
          // Move to OTP step so user can enter a delayed code
          step.value = 'otp'
          return
        }
      }
      throw new Error(err.detail || 'خطا در ارسال کد')
    }
    
    // Success - Start a 120s timer anyway to prevent immediate re-request
    startTimer(120)
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
        isInstalled.value = true
      }
      deferredPrompt.value = null;
      (window as any).deferredPrompt = null;
    })
  } else {
    alert('برای نصب اپلیکیشن، لطفاً از منوی مرورگر (سه نقطه) گزینه Install App یا Add to Home Screen را انتخاب کنید.')
  }
}

onMounted(() => {
  if (window.matchMedia('(display-mode: standalone)').matches || (window.navigator as any).standalone === true) {
    isStandalone.value = true
  }

  const userAgent = window.navigator.userAgent.toLowerCase()
  isIOS.value = /iphone|ipad|ipod/.test(userAgent)

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferredPrompt.value = e
    showInstallBtn.value = true
  })
  
  window.addEventListener('pwa-install-ready', () => {
    if ((window as any).deferredPrompt) {
       deferredPrompt.value = (window as any).deferredPrompt
       showInstallBtn.value = true
    }
  })
  
  window.addEventListener('appinstalled', () => {
    isInstalled.value = true
  })
  
  if ((window as any).deferredPrompt) {
     deferredPrompt.value = (window as any).deferredPrompt
     showInstallBtn.value = true
  }
})

watch(() => form.code, (newVal) => {
  if (newVal && newVal.length === 5) {
    verifyOtp()
  }
})

watch(() => form.mobile, (newVal) => {
  if (newVal && newVal.length === 11 && /^09\d{9}$/.test(newVal) && !loading.value && countdown.value === 0) {
    requestOtp()
  }
})
</script>

<template>
  <div class="min-h-[100dvh] flex items-center justify-center p-4 bg-gradient-to-br from-amber-50 via-white to-amber-100 overflow-hidden relative">
    
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
              :disabled="loading || countdown > 0" 
              class="btn-primary group relative overflow-hidden disabled:opacity-75 disabled:cursor-not-allowed"
            >
              <div class="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300"></div>
              
              <Loader2 v-if="loading" class="animate-spin" />
              <div v-else-if="countdown > 0" class="flex items-center gap-2 font-mono dir-ltr">
                <Clock :size="18" />
                <span>{{ formattedTimer }}</span>
              </div>
              <span v-else>دریافت کد تایید</span>
            </button>

            <!-- PWA PROMOTION -->
            <div v-if="!isStandalone && !isInstalled" class="pt-6 border-t border-gray-100/50 space-y-4">
                <button @click="installPWA" class="w-full py-3.5 px-4 bg-gradient-to-r from-amber-50 to-orange-50 text-amber-800 font-bold rounded-xl border border-amber-200/50 hover:bg-amber-100 transition-all flex items-center justify-center gap-3 shadow-sm hover:shadow-md">
                   <Download class="w-5 h-5"/>
                   <span>نصب اپلیکیشن</span>
                </button>

                <div v-if="isIOS" class="text-xs text-gray-500 text-center bg-gray-50/50 p-3 rounded-xl">
                  برای نصب در iOS: دکمه <span class="text-blue-500 font-bold">Share</span> و سپس <span class="font-bold">Add to Home Screen</span> را بزنید.
                </div>
            </div>
          </div>

          <!-- Step 2: OTP -->
          <div v-else key="otp" class="space-y-6">
            <div class="text-center mb-6">
              <p class="text-sm text-gray-500 mb-1">کد ارسال شده به {{ form.mobile }}</p>
              <button @click="step = 'mobile'" class="text-xs text-amber-600 font-bold hover:text-amber-700 transition-colors bg-amber-50 px-3 py-1 rounded-full">ویرایش شماره</button>
            </div>

            <div class="space-y-2">
              <div class="relative group">
                <input 
                  v-model="form.code"
                  type="text" 
                  inputmode="numeric"
                  pattern="[0-9]*"
                  class="input-premium !pl-14 tracking-[0.5em] font-mono text-xl font-bold text-gray-800"
                  style="direction: ltr; text-align: left;"
                  placeholder="_____"
                  maxlength="5"
                  dir="ltr"
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
                 <button v-else @click="requestOtp" class="text-xs text-amber-600 hover:text-amber-700 font-bold transition-colors">
                    ارسال مجدد کد
                 </button>
            </div>
          </div>

        </transition>

        <!-- Error Message -->
        <transition name="fade">
          <div v-if="error" class="mt-6 p-4 bg-red-50/80 border border-red-100 text-red-600 text-sm rounded-xl text-center shadow-sm backdrop-blur-sm relative overflow-hidden">
             <div class="absolute top-0 left-0 w-1 h-full bg-red-400"></div>
             {{ error }}
          </div>
        </transition>

      </div>
      
      <!-- Footer Info -->
      <div class="text-center mt-8 text-xs text-gray-400 font-medium opacity-60">
        نسخه ۲.۴.۰ • طراحی شده برای معامله‌گران
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
