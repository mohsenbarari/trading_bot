<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'
import { AppLoadingState } from '../components/ui'
import { apiFetch } from '../utils/auth'

const router = useRouter()
const route = useRoute()
const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')
const initialOwnerWorkspace = computed<'customers' | 'accountants' | null>(() => {
  const workspace = route.query.workspace
  return workspace === 'customers' || workspace === 'accountants' ? workspace : null
})

const currentUser = ref<{ id: number; account_name: string } | null>(null)

function handleNavigate(view: string, payload?: any) {
  if (view === 'settings') {
    router.push({ name: 'account-storage' })
  } else if (view === 'operations_customers') {
    router.push({ name: 'operations-customers' })
  } else if (view === 'operations_accountants') {
    router.push({ name: 'operations-accountants' })
  } else if (view === 'chat' && payload?.userId) {
    router.push({
      name: 'messenger',
      query: { user_id: String(payload.userId), user_name: payload.userName || '' }
    })
  } else if ((view === 'public_profile' || view === 'profile') && Number.isInteger(Number(payload?.id ?? payload?.user_id))) {
    const profileId = Number(payload.id ?? payload.user_id)
    router.push({
      name: 'public-profile',
      params: { id: String(profileId) },
      query: payload?.account_name ? { account_name: payload.account_name } : {},
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
      :initialOwnerWorkspace="initialOwnerWorkspace"
      @navigate="handleNavigate"
    />
    <div v-else class="loading-container">
      <AppLoadingState label="در حال دریافت پروفایل" />
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
</style>
