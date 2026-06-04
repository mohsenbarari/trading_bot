<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { apiFetch } from '../utils/auth'
import ChatView from '../components/ChatView.vue'
import MessengerRefactorShell from '../components/messenger-v2/MessengerRefactorShell.vue'
import '../styles/messenger-design-tokens.css'
import {
  markMessengerPerformance,
  resolveMessengerUiVersion,
} from '../utils/messengerRefactor'
import { getMessengerRolloutSurface } from '../utils/messengerRolloutPolicy'
import {
  measureMessengerDiagnostic,
  recordMessengerDomSnapshot,
  scheduleMessengerDiagnosticTask,
  startMessengerFrameBudgetProbe,
} from '../utils/messengerDiagnosticsMetrics'

const router = useRouter()
const route = useRoute()

const user = ref<any>(null)
const loading = ref(true)
const messengerUiVersion = ref(resolveMessengerUiVersion())
let messengerSurfaceMarked = false
const MESSENGER_SURFACE_DIAGNOSTIC_DEFER_MS = 4200

const jwtToken = computed(() => {
  return localStorage.getItem('auth_token') || ''
})

const apiBaseUrl = computed(() => {
  // Use the same base url logic from auth.ts
  return import.meta.env.VITE_API_BASE_URL || ''
})

// Optional: Extract params for direct chat navigation
const targetUserId = computed(() => {
  if (route.query.user_id) {
    return parseInt(route.query.user_id as string)
  }
  return undefined
})

const targetUserName = computed(() => {
  return route.query.user_name as string | undefined
})

const messengerRolloutSurface = computed(() => getMessengerRolloutSurface(messengerUiVersion.value))
const isMessengerRefactorShellEnabled = computed(() => messengerRolloutSurface.value.isRefactorShellEnabled)

watch([loading, user, messengerUiVersion], () => {
  if (loading.value || !user.value || messengerSurfaceMarked) {
    return
  }

  messengerSurfaceMarked = true
  markMessengerPerformance(`${messengerUiVersion.value}-surface-ready`)
  nextTick(() => {
    scheduleMessengerDiagnosticTask(() => {
      const root = typeof document !== 'undefined'
        ? document.querySelector('.messenger-page') || document.body
        : null
      if (root) {
        recordMessengerDomSnapshot(`${messengerUiVersion.value}-surface-ready`, root, {
          uiVersion: messengerUiVersion.value,
        })
      }
      startMessengerFrameBudgetProbe(`${messengerUiVersion.value}-surface-ready`, { frameCount: 30 })
    }, {
      deferMs: MESSENGER_SURFACE_DIAGNOSTIC_DEFER_MS,
      timeoutMs: 750,
      fallbackDelayMs: 120,
    })
  })
})

async function fetchUser() {
  markMessengerPerformance('current-user-fetch-start')
  try {
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      user.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    markMessengerPerformance('current-user-fetch-end')
    measureMessengerDiagnostic('current-user-fetch', 'current-user-fetch-start', 'current-user-fetch-end', {
      uiVersion: messengerUiVersion.value,
    })
    loading.value = false
  }
}

onMounted(() => {
  markMessengerPerformance('route-mounted')
  fetchUser()
})

function handleNavigate(view: string, payload?: any) {
  const profileId = Number(payload?.id ?? payload?.user_id)
  if ((view === 'public_profile' || view === 'profile') && Number.isInteger(profileId) && profileId > 0) {
    router.push({
      name: 'public-profile',
      params: { id: String(profileId) },
      query: payload?.account_name ? { account_name: payload.account_name } : undefined,
    })
  }
}

function handleBack() {
  // If in a chat, the ChatView handles going back to the conversation list internally.
  // If ChatView emits 'back' when there's nowhere to go back to internally, we can route home.
  router.push('/')
}
</script>

<template>
  <div
    class="messenger-page"
    :data-messenger-ui-version="messengerRolloutSurface.uiVersion"
    :data-messenger-rollout-mode="messengerRolloutSurface.rolloutMode"
  >
    <div v-if="loading" class="loading-container">
      <div class="loading-spinner"></div>
    </div>
    <div v-else-if="user" class="chat-wrapper">
      <MessengerRefactorShell
        v-if="isMessengerRefactorShellEnabled"
        :apiBaseUrl="apiBaseUrl"
        :jwtToken="jwtToken"
        :currentUserId="user.id"
        :currentUserRole="user.role || null"
        :currentUserIsAccountant="user.is_accountant === true"
        :currentUserIsCustomer="user.is_customer === true"
        :targetUserId="targetUserId"
        :targetUserName="targetUserName"
        @navigate="handleNavigate"
        @back="handleBack"
      />
      <ChatView 
        v-else
        :apiBaseUrl="apiBaseUrl"
        :jwtToken="jwtToken"
        :currentUserId="user.id"
        :currentUserRole="user.role || null"
        :currentUserIsAccountant="user.is_accountant === true"
        :currentUserIsCustomer="user.is_customer === true"
        :targetUserId="targetUserId"
        :targetUserName="targetUserName"
        @navigate="handleNavigate"
        @back="handleBack"
      />
    </div>
  </div>
</template>

<style scoped>
.messenger-page {
  /* Messenger takes up the full screen height (or what's left behind the nav) */
  height: 100dvh;
  width: 100%;
  background-color: var(--messenger-surface-page, #fceceb); /* Match chat background or app background */
}

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100dvh;
}
.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--messenger-accent, #f59e0b);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

.chat-wrapper {
  height: 100dvh;
  width: 100%;
}
</style>
