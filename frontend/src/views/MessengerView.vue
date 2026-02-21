<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { apiFetch } from '../utils/auth'
import ChatView from '../components/ChatView.vue'

const router = useRouter()
const route = useRoute()

const user = ref<any>(null)
const loading = ref(true)

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

async function fetchUser() {
  try {
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      user.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchUser()
})

function handleNavigate(view: string, payload?: any) {
  // The ChatView emits "navigate" for things like profile
  if (view === 'profile' && payload?.user_id) {
    // If we had a public profile view, we could route there. For now, do nothing.
    console.log('Navigate to profile', payload.user_id)
  }
}

function handleBack() {
  // If in a chat, the ChatView handles going back to the conversation list internally.
  // If ChatView emits 'back' when there's nowhere to go back to internally, we can route home.
  router.push('/')
}
</script>

<template>
  <div class="messenger-page">
    <div v-if="loading" class="loading-container">
      <div class="loading-spinner"></div>
    </div>
    <div v-else-if="user" class="chat-wrapper">
      <ChatView 
        :apiBaseUrl="apiBaseUrl"
        :jwtToken="jwtToken"
        :currentUserId="user.id"
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
  background-color: #fceceb; /* Match chat background or app background */
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
  border: 3px solid #f59e0b;
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
