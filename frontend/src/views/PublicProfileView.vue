<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublicProfile from '../components/PublicProfile.vue'

const route = useRoute()
const router = useRouter()

const jwtToken = computed(() => localStorage.getItem('auth_token'))
const apiBaseUrl = computed(() => import.meta.env.VITE_API_BASE_URL || '')

const profileUser = computed(() => {
  const rawId = route.params.id
  const id = Number(rawId)
  if (!Number.isInteger(id) || id <= 0) {
    return null
  }

  const accountName = typeof route.query.account_name === 'string' ? route.query.account_name : ''
  return {
    id,
    account_name: accountName,
  }
})

function handleNavigate(view: string, payload?: { userId?: number; userName?: string }) {
  if (view === 'chat' && payload?.userId) {
    router.push({
      name: 'messenger',
      query: {
        user_id: String(payload.userId),
        user_name: payload.userName || '',
      },
    })
    return
  }

  const canGoBack = typeof window !== 'undefined' && Boolean(window.history.state?.back)
  if (canGoBack) {
    router.back()
    return
  }

  router.push('/')
}
</script>

<template>
  <div class="public-profile-view">
    <PublicProfile
      :key="profileUser?.id || 'invalid-profile'"
      :user="profileUser"
      :apiBaseUrl="apiBaseUrl"
      :jwtToken="jwtToken"
      @navigate="handleNavigate"
    />
  </div>
</template>

<style scoped>
.public-profile-view {
  min-height: 100%;
  padding: 16px;
}
</style>
