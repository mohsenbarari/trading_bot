<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'
import { apiFetch } from '../utils/auth'
import { Shield, X, Check, Smartphone } from 'lucide-vue-next'

const { connect, on, off } = useWebSocket()

const showModal = ref(false)
const pendingRequest = ref<any>(null)
const loading = ref(false)
const countdown = ref(0)
let countdownInterval: any = null

function handleLoginRequest(data: any) {
  pendingRequest.value = data
  showModal.value = true
  // Start 120s countdown
  countdown.value = 120
  if (countdownInterval) clearInterval(countdownInterval)
  countdownInterval = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(countdownInterval)
      showModal.value = false
      pendingRequest.value = null
    }
  }, 1000)
}

async function approve() {
  if (!pendingRequest.value) return
  loading.value = true
  try {
    await apiFetch(`/api/sessions/login-requests/${pendingRequest.value.request_id}/approve`, {
      method: 'POST'
    })
    showModal.value = false
    pendingRequest.value = null
  } catch (e: any) {
    console.error('Approve error:', e)
  } finally {
    loading.value = false
  }
}

async function reject() {
  if (!pendingRequest.value) return
  loading.value = true
  try {
    await apiFetch(`/api/sessions/login-requests/${pendingRequest.value.request_id}/reject`, {
      method: 'POST'
    })
    showModal.value = false
    pendingRequest.value = null
  } catch (e: any) {
    console.error('Reject error:', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  connect()
  on('session:login_request', handleLoginRequest)
})

onUnmounted(() => {
  off('session:login_request', handleLoginRequest)
  if (countdownInterval) clearInterval(countdownInterval)
})
</script>

<template>
  <Teleport to="body">
    <transition name="fade">
      <div v-if="showModal" class="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" @click.self="() => {}">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden animate-scale-in">
          <!-- Header -->
          <div class="bg-gradient-to-r from-amber-500 to-amber-600 p-5 text-center">
            <div class="w-14 h-14 bg-white/20 rounded-full mx-auto flex items-center justify-center mb-3">
              <Smartphone class="w-7 h-7 text-white" />
            </div>
            <h3 class="text-white font-bold text-lg">درخواست ورود جدید</h3>
          </div>

          <!-- Body -->
          <div class="p-5 space-y-4">
            <div class="bg-gray-50 rounded-xl p-4 space-y-2 text-sm text-right">
              <div class="flex justify-between items-center">
                <span class="font-mono text-xs text-gray-500 dir-ltr">{{ pendingRequest?.device_ip || '—' }}</span>
                <span class="text-gray-600 font-medium">آی‌پی</span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-gray-700">{{ pendingRequest?.device_name || 'دستگاه ناشناس' }}</span>
                <span class="text-gray-600 font-medium">دستگاه</span>
              </div>
            </div>

            <p class="text-xs text-gray-500 text-center">
              آیا اجازه ورود از این دستگاه را می‌دهید؟
            </p>

            <div v-if="countdown > 0" class="text-center text-xs text-gray-400 font-mono">
              {{ Math.floor(countdown / 60).toString().padStart(2, '0') }}:{{ (countdown % 60).toString().padStart(2, '0') }}
            </div>

            <!-- Actions -->
            <div class="flex gap-3">
              <button
                @click="reject"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl border border-red-200 text-red-600 font-bold text-sm hover:bg-red-50 transition-colors disabled:opacity-50"
              >
                <X class="w-4 h-4 inline-block ml-1" />
                رد
              </button>
              <button
                @click="approve"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl bg-emerald-500 text-white font-bold text-sm hover:bg-emerald-600 transition-colors disabled:opacity-50"
              >
                <Check class="w-4 h-4 inline-block ml-1" />
                تایید
              </button>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.animate-scale-in {
  animation: scaleIn 0.3s ease-out;
}

@keyframes scaleIn {
  from { transform: scale(0.9); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
