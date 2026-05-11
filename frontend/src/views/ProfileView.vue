<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
import { apiFetch } from '../utils/auth'

const router = useRouter()
const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')

const currentUser = ref<{ id: number; account_name: string } | null>(null)

function handleNavigate(view: string, payload?: any) {
  if (view === 'settings') {
    router.push({ name: 'settings' })
  } else if (view === 'chat' && payload?.userId) {
    router.push({
      name: 'messenger',
      query: { user_id: String(payload.userId), user_name: payload.userName || '' }
    })
  } else if (view === 'home') {
    router.push('/')
  }
}

onMounted(async () => {
  try {
    const response = await apiFetch('/api/auth/me')
    if (response.ok) {
      const data = await response.json()
      currentUser.value = {
        id: data.id,
        account_name: data.account_name || data.full_name || 'کاربر'
      }
    }
  } catch (e) {
    console.error(e)
  }
})
</script>

<template>
  <div class="profile-view">
    <PublicProfile
      v-if="currentUser"
      :key="currentUser.id"
      :user="currentUser"
      :viewerUserId="currentUser.id"
      :apiBaseUrl="apiBaseUrl"
      :jwtToken="jwtToken"
      :hideBackButton="true"
      @navigate="handleNavigate"
    />
    <div v-else class="loading-container">
      <div class="loading-spinner"></div>
    </div>
  </div>
</template>

<style scoped>
.profile-view {
  min-height: 100%;
  padding: 16px;
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
</style>
