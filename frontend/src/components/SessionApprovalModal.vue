<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'
import { apiFetch } from '../utils/auth'
import { Shield, X, Check, Smartphone } from 'lucide-vue-next'
import { type SessionLoginRequestPayload, WS_NOTIFICATION_EVENTS } from '../types/notifications'

const { connect, on, off } = useWebSocket()

const showModal = ref(false)
const pendingRequest = ref<SessionLoginRequestPayload | null>(null)
const loading = ref(false)
const countdown = ref(0)
let countdownInterval: any = null

async function fetchPendingRequests() {
  if (!localStorage.getItem('auth_token')) return
  if (showModal.value) return // Already showing a request
  try {
    const res = await apiFetch('/api/sessions/login-requests/pending')
    if (res.ok) {
      const data = await res.json()
      if (Array.isArray(data) && data.length > 0) {
        pendingRequest.value = data[0]
        showModal.value = true
        
        // Start countdown based on expires_at
        if (data[0].expires_at) {
          const expires = new Date(data[0].expires_at).getTime()
          const now = new Date().getTime()
          countdown.value = Math.max(0, Math.floor((expires - now) / 1000))
        } else {
          countdown.value = 120
        }
        
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
    }
  } catch (e) {
    // Ignore, maybe not primary
  }
}

async function handleLoginRequest(data: SessionLoginRequestPayload) {
  if (!localStorage.getItem('auth_token')) return
  // Only show on primary device
  try {
    const res = await apiFetch('/api/sessions/active')
    if (res.ok) {
      const sessions = await res.json()
      const myRefresh = localStorage.getItem('refresh_token')
      // If we can't determine primary status, show anyway as fallback
      if (Array.isArray(sessions) && myRefresh) {
        const mySession = sessions.find((s: any) => s.is_current)
        if (mySession && !mySession.is_primary) return
      }
    }
  } catch {
    // On error, show modal as fallback
  }
  pendingRequest.value = data
  showModal.value = true
  // Start 120s countdown
  if (data.expires_at) {
    const expires = new Date(data.expires_at).getTime()
    const now = new Date().getTime()
    countdown.value = Math.max(0, Math.floor((expires - now) / 1000))
  } else {
    countdown.value = 120
  }
  
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
  on(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
  on(WS_NOTIFICATION_EVENTS.wsReconnect, fetchPendingRequests)
  
  // Also check when tab comes back to foreground
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === 'visible') {
      fetchPendingRequests()
    }
  })
  
  // Check initially just in case
  setTimeout(fetchPendingRequests, 1000)
})

onUnmounted(() => {
  off(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
  off(WS_NOTIFICATION_EVENTS.wsReconnect, fetchPendingRequests)
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
